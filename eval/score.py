"""Afterglow eval scorer (AGENTS.md section 5).

Reads ``runs/*/meta.json`` ONLY — no hand-typed scores. Groups bundles by
case, picks the latest bundle per case by ``wall_end_utc``, prints the
markdown board to stdout AND writes ``eval/results.md`` (generated, never
hand-edited).

Expected ``meta.json`` keys (written by ``agent/loop.py``):
  case_id (or case), status ("green"/"red"/"triage"; also accepts
  verdict/green keys), mode ("live"/"triage"), wall_start_utc,
  wall_end_utc, plus optional time_to_green_s, patch_lines,
  human_touches, sim_seed, prompt_hash, model_id.

Triage handling (Task 4 decision, documented here): triage bundles
(mode/status "triage": DIAGNOSE+EXPLAIN done, no patch ever applied
-- API flake with no patch, guard reject with no patch, or --triage
withhold -- exit 2) get an explicit TRIAGE row -- they never silently
vanish from the board -- and are never counted green. A patch applied
but resim still RED stays RED (exit 1), not triage. When a case has both decisive
(green/red live) and triage bundles, the latest DECISIVE bundle scores:
a later triage re-run must not mask an earlier fix verdict, because
triage proves a diagnosis happened without claiming fix status. A case
with ONLY triage bundles shows TRIAGE (unknown fix status, not RED:
triage never attempted a patch, so RED would be a false failure claim).

Empty ``runs/`` is not an error: the board prints with all cases
UNRUN and 0/3 + 0/8. Exit 0 on success, 2 on internal failure.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_PATH = os.path.join(ROOT, "eval", "cases.yaml")
RUNS_DIR = os.path.join(ROOT, "runs")
RESULTS_PATH = os.path.join(ROOT, "eval", "results.md")

LOCKED_IDS = ("picosoc-uart-div", "picorv32-fetch-misalign", "uart-tx-div")


def load_cases(path: str):
    """Minimal ``- id:`` block parser for eval/cases.yaml (no PyYAML needed)."""
    cases = []
    cur = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().splitlines()
    except OSError:
        return []
    for line in raw:
        m = re.match(r"\s*-\s*id:\s*(\S+)\s*$", line)
        if m:
            if cur and "id" in cur:
                cases.append(cur)
            cur = {"id": m.group(1), "tier": "stress", "budget_s": "?"}
            continue
        if cur is not None:
            m2 = re.match(r"\s*tier:\s*(\S+)\s*$", line)
            if m2:
                cur["tier"] = m2.group(1)
                continue
            m3 = re.match(r"\s*budget_s:\s*(\S+)\s*$", line)
            if m3:
                cur["budget_s"] = m3.group(1)
    if cur and "id" in cur:
        cases.append(cur)
    return cases


def load_runs(runs_dir: str):
    """Read every runs/*/meta.json; skip unreadable/corrupt bundles."""
    runs = []
    for meta_path in sorted(glob.glob(os.path.join(runs_dir, "*", "meta.json"))):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        run_id = os.path.basename(os.path.dirname(meta_path))
        case = d.get("case_id") or d.get("case") or run_id
        status_raw = d.get("status", d.get("verdict", ""))
        mode_raw = str(d.get("mode", "") or "").strip().lower()
        triage = (mode_raw == "triage"
                  or str(status_raw).strip().lower() == "triage")
        if isinstance(d.get("green"), bool):
            green = d["green"]
        elif isinstance(d.get("passed"), bool):
            green = d["passed"]
        else:
            green = str(status_raw).strip().lower() in (
                "green",
                "pass",
                "passed",
                "success",
                "match",
            )
        try:
            mtime = os.path.getmtime(meta_path)
        except OSError:
            mtime = 0.0
        runs.append(
            {
                "run_id": run_id,
                "case": str(case),
                "green": bool(green),
                "triage": bool(triage),
                "status": str(status_raw),
                "wall_end_utc": str(d.get("wall_end_utc") or ""),
                "wall_start_utc": str(d.get("wall_start_utc") or ""),
                "time_to_green_s": d.get("time_to_green_s"),
                "patch_lines": d.get("patch_lines"),
                "human_touches": d.get("human_touches", 0),
                "mtime": mtime,
            }
        )
    return runs


def latest_by_case(runs):
    """Pick the latest bundle per case by wall_end_utc (mtime fallback).

    Triage-aware (Task 4): when a case has any decisive (non-triage)
    bundles, the latest DECISIVE one scores; triage-only cases fall back
    to their latest triage bundle so they show as TRIAGE, never UNRUN.
    """
    by_case: dict = {}
    for r in runs:
        by_case.setdefault(r["case"], []).append(r)
    best = {}
    for case, rs in by_case.items():
        decisive = [r for r in rs if not r.get("triage")]
        pool = decisive if decisive else rs
        top = max(pool,
                  key=lambda r: (r["wall_end_utc"], r["mtime"],
                                 r["run_id"]))
        best[case] = top
    return best


def fmt_cell(value):
    if value is None or value == "":
        return "-"
    return str(value)


def build_board(cases, latest):
    locked_green = 0
    locked_total = 0
    full_green = 0
    lines = []
    lines.append("# Afterglow eval board")
    lines.append("")
    lines.append("_Generated by `eval/score.py` from `runs/*/meta.json` only. "
                 "Do not hand-edit._")
    lines.append("")
    lines.append("| case | tier | status | run | time_to_green_s | patch_lines | human_touches |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in cases:
        cid = c["id"]
        tier = c.get("tier", "?")
        r = latest.get(cid)
        if r is None:
            status = "UNRUN"
            row = f"| {cid} | {tier} | UNRUN | - | - | - | - |"
        elif r.get("triage"):
            status = "TRIAGE"
            row = (
                f"| {cid} | {tier} | TRIAGE | {r['run_id']} | "
                f"{fmt_cell(r['time_to_green_s'])} | {fmt_cell(r['patch_lines'])} | "
                f"{fmt_cell(r['human_touches'])} |"
            )
        else:
            status = "GREEN" if r["green"] else "RED"
            row = (
                f"| {cid} | {tier} | {status} | {r['run_id']} | "
                f"{fmt_cell(r['time_to_green_s'])} | {fmt_cell(r['patch_lines'])} | "
                f"{fmt_cell(r['human_touches'])} |"
            )
        lines.append(row)
        if tier == "locked":
            locked_total += 1
            if r is not None and r["green"]:
                locked_green += 1
        if r is not None and r["green"]:
            full_green += 1
    # Cases seen in runs/ but not in cases.yaml are reported, never scored.
    # Tier cell reads FRESH: fresh-design acceptance run (Task 6), reported
    # for visibility, excluded from locked/full totals below.
    known = {c["id"] for c in cases}
    unknown = sorted(set(latest) - known)
    for cid in unknown:
        r = latest[cid]
        status = "GREEN" if r["green"] else "RED"
        lines.append(
            f"| {cid} | FRESH | {status} | {r['run_id']} | "
            f"{fmt_cell(r['time_to_green_s'])} | {fmt_cell(r['patch_lines'])} | "
            f"{fmt_cell(r['human_touches'])} |"
        )
    lines.append("")
    lines.append(f"locked: {locked_green}/{locked_total}, full: {full_green}/{len(cases)}")
    if not latest:
        lines.append("no runs yet.")
    return "\n".join(lines) + "\n", locked_green, locked_total, full_green, len(cases)


def main() -> int:
    try:
        cases = load_cases(CASES_PATH)
        if not cases:
            cases = [
                {"id": cid, "tier": "locked" if cid in LOCKED_IDS else "stress",
                 "budget_s": "?"}
                for cid in list(LOCKED_IDS) + [
                    "uart-start-half", "fetch-highword", "rdata-latch",
                    "trap-stuck", "pc-incr",
                ]
            ]
        runs = load_runs(RUNS_DIR) if os.path.isdir(RUNS_DIR) else []
        latest = latest_by_case(runs)
        board, lg, lt, fg, ft = build_board(cases, latest)
        with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
            fh.write(board)
        sys.stdout.write(board)
        return 0
    except Exception as exc:  # never crash the gate on a scorer bug
        sys.stdout.write(
            "# Afterglow eval board\n\n"
            f"scorer error (runs unreadable?): {exc}\n\nlocked: 0/3, full: 0/8\n"
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
