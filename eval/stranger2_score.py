"""Afterglow stranger-2 scorer (stranger #2, darkriscv entry).

Reads ``runs/*/meta.json`` ONLY -- no hand-typed scores -- and joins
them to the entry in ``eval/stranger2.yaml``. Prints the stranger-2
board to stdout AND writes ``eval/stranger2_results.md``
(generated, never hand-edited).

Run attribution is by run-id prefix: a live run counts for
``stranger2`` iff its run directory is ``runs/stranger2`` or
``runs/stranger2-*`` (the controller MUST pass
``--run-id stranger2-*``). With several runs the latest bundle by
``wall_end_utc`` (mtime fallback) scores; a later triage re-run
never masks an earlier decisive GREEN/RED (same rule as
``eval/score.py``).

Per-entry verdicts ride a ``Verdict(**{"pass": ...})`` read via
``getattr(v, "pass")`` -- the pass bit ALWAYS comes from the bundle,
never from hand text. The entry with no runs boards as UNRUN (unknown
fix status, not RED: no patch was ever attempted).

Metrics: fix rate (GREEN entries / total, here 0/1 or 1/1) plus, when
GREEN, time-to-green vs the workforce-estimated manual baseline from
``stranger2.yaml`` (estimated hours, labelled as such -- never
measured watts, never measured baselines). Reds/UNRUN stay visible
with run-ids (or "-" where no run exists yet). TIME RULE:
time_to_green_s prints only for GREEN rows; RED/TRIAGE/UNRUN show
"-" (no time-to-green exists).
"""

from __future__ import annotations

import glob
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from afterglow_core.types import Verdict  # noqa: E402

STRANGER_PATH = os.path.join(ROOT, "eval", "stranger2.yaml")
RUNS_DIR = os.path.join(ROOT, "runs")
RESULTS_PATH = os.path.join(ROOT, "eval", "stranger2_results.md")


def load_strangers(path: str):
    """Flat-YAML reader with `key: |` literal-block support (stdlib)."""
    entries = []
    cur = None
    lit_key = None
    lit_indent = 0
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read().splitlines()
    for ln in raw:
        if lit_key is not None:
            m = re.match(r"^(\s*)\S", ln)
            if ln.strip() == "" or (m and len(m.group(1)) > lit_indent):
                cur[lit_key] += (ln.strip() + "\n" if ln.strip() else "\n")
                continue
            lit_key = None
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"-\s*id:\s*(\S+)\s*$", s)
        if m:
            if cur and "id" in cur:
                entries.append(cur)
            cur = {"id": m.group(1)}
            continue
        m = re.match(r"([A-Za-z_]+):\s*(\|)?\s*(.*)$", s)
        if m and cur is not None:
            key, pipe, val = m.group(1), m.group(2), m.group(3)
            if pipe:
                cur[key] = ""
                lit_key = key
                lit_indent = len(ln) - len(ln.lstrip())
            else:
                v = val.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                cur[key] = v
    if cur and "id" in cur:
        entries.append(cur)
    return entries


def load_runs(runs_dir: str):
    """Every runs/*/meta.json whose run-id carries the stranger2 prefix."""
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
        if not (run_id == "stranger2" or run_id.startswith("stranger2-")):
            continue
        status_raw = d.get("status", d.get("verdict", ""))
        mode_raw = str(d.get("mode", "") or "").strip().lower()
        triage = mode_raw == "triage" or str(status_raw).strip().lower() == "triage"
        if isinstance(d.get("green"), bool):
            green = d["green"]
        elif isinstance(d.get("passed"), bool):
            green = d["passed"]
        else:
            green = str(status_raw).strip().lower() in (
                "green", "pass", "passed", "success", "match")
        # The pass bit rides a Verdict object, read via getattr --
        # never a bare attribute, never hand text.
        verdict = Verdict(
            **{"pass": bool(green),
               "evidence": {"run_id": run_id, "source": "meta.json"}})
        try:
            mtime = os.path.getmtime(meta_path)
        except OSError:
            mtime = 0.0
        runs.append({
            "run_id": run_id,
            "case": str(d.get("case_id") or d.get("case") or run_id),
            "verdict": verdict,
            "triage": bool(triage),
            "wall_end_utc": str(d.get("wall_end_utc") or ""),
            "wall_start_utc": str(d.get("wall_start_utc") or ""),
            "time_to_green_s": d.get("time_to_green_s"),
            "patch_lines": d.get("patch_lines"),
            "human_touches": d.get("human_touches", 0),
            "mtime": mtime,
        })
    return runs


def runs_for(sid: str, runs):
    """Runs attributed to stranger entry ``sid`` by run-id prefix."""
    return [r for r in runs
            if r["run_id"] == sid or r["run_id"].startswith(sid + "-")]


def latest(runs):
    """Latest bundle wins; decisive (non-triage) beats later triage."""
    if not runs:
        return None
    decisive = [r for r in runs if not r.get("triage")]
    pool = decisive if decisive else runs
    return max(pool,
               key=lambda r: (r["wall_end_utc"], r["mtime"], r["run_id"]))


def fmt(value):
    if value is None or value == "":
        return "-"
    return str(value)


def speedup(time_to_green_s, baseline_hours):
    try:
        ttg = float(time_to_green_s)
        base = float(baseline_hours) * 3600.0
    except (TypeError, ValueError):
        return "-"
    if ttg <= 0 or base <= 0:
        return "-"
    return "%.0fx" % (base / ttg,)


def build_board(strangers, runs):
    green = 0
    ttgs = []
    lines = []
    lines.append("# Afterglow stranger-2 board (darkriscv entry)")
    lines.append("")
    lines.append("_Generated by `eval/stranger2_score.py` from "
                 "`runs/*/meta.json` only (run-ids `stranger2-*`). "
                 "Do not hand-edit._")
    lines.append("")
    lines.append("Quarantine: `ground_truth_fix` in `eval/stranger2.yaml` "
                 "is judge evidence only -- loop prompts never see it. "
                 "Baselines are workforce estimates (see "
                 "`baseline_source`), not measurements. The case injects "
                 "the literal pre-fix line (no proxy): "
                 "`sim/targets/stranger2.yaml`.")
    lines.append("")
    lines.append("| stranger id | upstream commit | status | run | "
                 "time_to_green_s | patch_lines | baseline_h | "
                 "speedup_vs_baseline | human_touches |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in strangers:
        sid = s["id"]
        commit = (s.get("commit") or "")[:12]
        base_h = s.get("manual_baseline_hours", "")
        r = latest(runs_for(sid, runs))
        if r is None:
            lines.append("| %s | %s | UNRUN | - | - | - | %s | - | - |"
                         % (sid, commit, fmt(base_h)))
            continue
        if r.get("triage"):
            status = "TRIAGE"
        else:
            status = "GREEN" if getattr(r["verdict"], "pass") else "RED"
        if status == "GREEN":
            green += 1
            try:
                ttgs.append(float(r["time_to_green_s"]))
            except (TypeError, ValueError):
                pass
        spd = speedup(r["time_to_green_s"], base_h) \
            if status == "GREEN" else "-"
        ttg_str = fmt(r["time_to_green_s"]) \
            if status == "GREEN" else "-"
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                     % (sid, commit, status, r["run_id"],
                        ttg_str, fmt(r["patch_lines"]),
                        fmt(base_h), spd, fmt(r["human_touches"])))
    total = len(strangers)
    lines.append("")
    lines.append("fix rate: %d/%d" % (green, total))
    if ttgs:
        lines.append("median time-to-green (green only): %.1fs "
                     "(baselines are estimated hours; see rows)" %
                     statistics.median(ttgs))
    else:
        lines.append("median time-to-green: n/a (no green stranger2 runs yet)")
    if green < total:
        pending = [s["id"] for s in strangers
                   if latest(runs_for(s["id"], runs)) is None]
        if pending:
            lines.append("awaiting live runs: %s" % ", ".join(pending))
    lines.append("")
    return "\n".join(lines) + "\n", green, total


def main() -> int:
    try:
        strangers = load_strangers(STRANGER_PATH)
        if not strangers:
            raise ValueError("no stranger2 entries in %s" % STRANGER_PATH)
        runs = load_runs(RUNS_DIR) if os.path.isdir(RUNS_DIR) else []
        board, _green, _total = build_board(strangers, runs)
        with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
            fh.write(board)
        sys.stdout.write(board)
        return 0
    except Exception as exc:  # never crash the gate on a scorer bug
        sys.stdout.write(
            "# Afterglow stranger-2 board (darkriscv entry)\n\n"
            "scorer error (runs unreadable?): %s\n\nfix rate: 0/0\n"
            % (exc,))
        return 2


if __name__ == "__main__":
    sys.exit(main())
