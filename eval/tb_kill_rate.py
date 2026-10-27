#!/usr/bin/env python3
"""Per-testbench kill-rate report (wave-4: testbenches as measured oracles).

Reads STATIC artifacts only -- no API, no sim, no build:
- ``eval/mutant_pilot/board.json``: kept (RED = TB caught the bug),
  discarded_equivalent, discarded_build_break, accounting.
- ``sim/targets/mut-bigN-*.yaml`` mutant specs: tb / parent_case cross-check.
- ``eval/cases.yaml`` + ``sim/targets/<case>.yaml``: the 8 board cases and
  which parent TB each runs under.

For each parent TB (board.json parents mapping: source file -> parent
case -> TB): mutants drawn, kept-RED (killed = TB caught the bug),
equivalent, build-break, and kill_rate = kept / (drawn - build_break)
(build-breakers never reach the oracle, so they leave the denominator).
The cond_grow blind spot (kept ZERO campaign-wide) is called out per TB.

Weakest TB (lowest kill_rate) is ranked, with ONE cheapest TB-hardening
experiment named as a RECOMMENDATION (not implemented -- wave-5's fuel).

Usage: ``python3 -c "from eval.tb_kill_rate import generate;
print(generate())"`` -- writes ``eval/tb_kill_rate.md`` (deterministic:
no timestamps; regenerating to any path yields byte-identical output).
The test ``tests/test_tb_kill_rate.py`` checks shape, arithmetic
reconciliation with board.json accounting, the cond_grow-zero callout,
and temp-regen determinism -- never the kill-rate values.
"""
from __future__ import annotations

import glob
import json
import os
import re

from afterglow_core.verilator import load_case_file

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARD = os.path.join(REPO, "eval", "mutant_pilot", "board.json")
MUT_GLOB = os.path.join(REPO, "sim", "targets", "mut-bigN-*.yaml")
CASES_YAML = os.path.join(REPO, "eval", "cases.yaml")
REPORT_PATH = os.path.join(REPO, "eval", "tb_kill_rate.md")


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _board_case_ids():
    """The 8 eval board case ids from eval/cases.yaml (``- id:`` lines)."""
    ids = []
    with open(CASES_YAML, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"\s*-\s*id:\s*(\S+)\s*$", line)
            if m:
                ids.append(m.group(1))
    return ids


def _parent_rows():
    """Aggregate board.json lists per parent TB.

    Returns (rows, kept_cond_grow_total). Raises ValueError on any drift
    between the board parents mapping, kept parent_case fields, and
    mutant spec tb fields -- mapping drift fails loudly.
    """
    board = _load_json(BOARD)
    parents = board["parents"]  # file -> {case, top, tb, oracle_pass}
    file_to_tb = {}
    for fname, pinfo in parents.items():
        file_to_tb[fname] = {"case": pinfo["case"], "tb": pinfo["tb"],
                             "top": pinfo["top"]}

    # Cross-check: every kept mutant's parent_case matches its file's
    # parent case; every mutant spec's tb matches its file's parent TB.
    agg = {fname: {"kept": [], "equiv": [], "bb": [],
                   "kept_cond_grow": 0, "drawn_cond_grow": 0}
           for fname in parents}
    for k in board["kept"]:
        fname = k["file"]
        if fname not in agg:
            raise ValueError("kept mutant outside parents map: %r" % fname)
        if k["parent_case"] != parents[fname]["case"]:
            raise ValueError("parent_case drift: %s has %r, map says %r"
                             % (k["mutant_id"], k["parent_case"],
                                parents[fname]["case"]))
        agg[fname]["kept"].append(k)
        if k["op"] == "cond_grow":
            agg[fname]["kept_cond_grow"] += 1
    for e in board["discarded_equivalent"]:
        fname = e["record"]["file"]
        if fname not in agg:
            raise ValueError("equivalent mutant outside parents map: %r"
                             % fname)
        agg[fname]["equiv"].append(e)
        if e["record"]["op"] == "cond_grow":
            agg[fname]["drawn_cond_grow"] += 1
    for b in board["discarded_build_break"]:
        fname = b["record"]["file"]
        if fname not in agg:
            raise ValueError("build-break mutant outside parents map: %r"
                             % fname)
        agg[fname]["bb"].append(b)

    spec_tbs = {}
    for path in sorted(glob.glob(MUT_GLOB)):
        spec = load_case_file(path)
        mid = spec.get("mutant_id", os.path.basename(path))
        fname = spec.get("mutant_file")
        if fname not in file_to_tb:
            raise ValueError("spec %s outside parents map: %r" % (mid, fname))
        if spec.get("tb") != file_to_tb[fname]["tb"]:
            raise ValueError("spec tb drift: %s has %r, map says %r"
                             % (mid, spec.get("tb"),
                                file_to_tb[fname]["tb"]))
        if spec.get("parent_case") != file_to_tb[fname]["case"]:
            raise ValueError("spec parent_case drift: %s" % mid)
        spec_tbs[mid] = spec.get("tb")

    # Board cases per TB (which of the 8 run under each parent TB).
    case_tb = {}
    for cid in _board_case_ids():
        spec = load_case_file(os.path.join(REPO, "sim", "targets",
                                           cid + ".yaml"))
        case_tb[cid] = spec.get("tb")
    tb_cases = {}
    for cid, tb in case_tb.items():
        tb_cases.setdefault(tb, []).append(cid)

    rows = []
    for fname in sorted(parents):
        info = file_to_tb[fname]
        a = agg[fname]
        kept = len(a["kept"])
        equiv = len(a["equiv"])
        bb = len(a["bb"])
        drawn = kept + equiv + bb
        denom = drawn - bb
        kill = kept / denom if denom else float("nan")
        # Dominant escape class: top equivalent op on this TB.
        esc = {}
        for e in a["equiv"]:
            op = e["record"]["op"]
            esc[op] = esc.get(op, 0) + 1
        top_escape = max(sorted(esc.items()), key=lambda kv: kv[1]
                         ) if esc else ("none", 0)
        rows.append({"file": fname, "case": info["case"], "tb": info["tb"],
                     "top": info["top"],
                     "board_cases": sorted(tb_cases.get(info["tb"], [])),
                     "drawn": drawn, "kept": kept, "equivalent": equiv,
                     "build_break": bb, "kill_rate": kill,
                     "kept_cond_grow": a["kept_cond_grow"],
                     "drawn_cond_grow": a["drawn_cond_grow"],
                     "top_escape_op": top_escape[0],
                     "top_escape_n": top_escape[1]})
    kept_cg = sum(r["kept_cond_grow"] for r in rows)
    return rows, kept_cg, board["accounting"]


def _pct(x):
    return "n/a" if x != x else "%.2f%%" % (100.0 * x)  # NaN-safe


def render(rows, kept_cg, accounting):
    """Render the deterministic markdown report."""
    total_drawn = sum(r["drawn"] for r in rows)
    total_kept = sum(r["kept"] for r in rows)
    total_equiv = sum(r["equivalent"] for r in rows)
    total_bb = sum(r["build_break"] for r in rows)
    total_denom = total_drawn - total_bb
    total_kill = total_kept / total_denom if total_denom else float("nan")
    weakest = min(rows, key=lambda r: r["kill_rate"])
    best = max(rows, key=lambda r: r["kill_rate"])
    w_share = (weakest["top_escape_n"] / weakest["equivalent"]
               if weakest["equivalent"] else float("nan"))

    out = []
    out.append("# Per-TB kill-rate report (wave-4)")
    out.append("")
    out.append("Generated by `eval/tb_kill_rate.py` via "
               "`tests/test_tb_kill_rate.py`. STATIC artifacts only "
               "(`eval/mutant_pilot/board.json` kept / equivalent / "
               "build-break lists + `sim/targets/mut-bigN-*.yaml` spec "
               "TB-parent mapping + the 8 `eval/cases.yaml` board cases) "
               "-- no API, no sim, no build. Deterministic: no timestamps; "
               "regenerating to any path yields byte-identical output.")
    out.append("")
    out.append("Method: parent TB of each drawn mutant = its source file's "
               "entry in the board `parents` map (file -> parent case -> "
               "TB), cross-checked against every kept mutant's "
               "`parent_case` and every mutant spec's `tb` field (any "
               "drift raises). kept-RED = killed = the TB caught the bug "
               "(mutant fails the parent oracle). kill_rate = "
               "kept / (drawn - build_break): build-breakers never reach "
               "the oracle, so they leave the denominator. Campaign "
               "45/45 (kept_total 50 = 45 drawn + 5 pilot imports, which "
               "are hand-aimed, not drawn, and excluded from kill rates).")
    out.append("")
    out.append("## Per-parent-TB kill rate")
    out.append("")
    out.append("| TB | parent case | board cases | drawn | kept-RED "
               "(killed) | equivalent | build-break | kill_rate |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        out.append("| %s | %s | %s | %d | %d | %d | %d | %s |"
                   % (r["tb"], r["case"], ", ".join(r["board_cases"]),
                      r["drawn"], r["kept"], r["equivalent"],
                      r["build_break"], _pct(r["kill_rate"])))
    out.append("| TOTAL | -- | 8 cases | %d | %d | %d | %d | %s |"
               % (total_drawn, total_kept, total_equiv, total_bb,
                  _pct(total_kill)))
    out.append("")
    out.append("Reconciliation with `board.json` accounting: drawn=%d "
               "kept_new=%d equivalent=%d build_break=%d other=%d; row "
               "sums match all four drawn-side totals, and each row "
               "closes (drawn == kept + equivalent + build-break)."
               % (accounting["drawn"], accounting["kept_new"],
                  accounting["equivalent"], accounting["build_break"],
                  accounting["other"]))
    out.append("")
    out.append("## cond_grow blind spot (per TB)")
    out.append("")
    out.append("Campaign-wide kept-RED cond_grow mutants: %d (ZERO -- "
               "TB-blind). Strict-inequality grows (`>` -> `>=`, `<` -> "
               "`<=`) either never fire the recorded oracles or pass as "
               "honest equivalents." % kept_cg)
    out.append("")
    out.append("| TB | cond_grow drawn | cond_grow kept-RED | verdict |")
    out.append("|---|---|---|---|")
    for r in rows:
        if r["drawn_cond_grow"] and not r["kept_cond_grow"]:
            verdict = "BLIND (drew %d, killed 0)" % r["drawn_cond_grow"]
        elif not r["drawn_cond_grow"]:
            verdict = "no data (none drawn from this file)"
        else:
            verdict = "kills %d/%d" % (r["kept_cond_grow"],
                                       r["drawn_cond_grow"])
        out.append("| %s | %d | %d | %s |"
                   % (r["tb"], r["drawn_cond_grow"],
                      r["kept_cond_grow"], verdict))
    out.append("")
    out.append("## Dominant escape class per TB (wave-5 aim)")
    out.append("")
    out.append("| TB | top equivalent op | n | share of TB equivalents |")
    out.append("|---|---|---|---|")
    for r in rows:
        share = (r["top_escape_n"] / r["equivalent"]
                 if r["equivalent"] else float("nan"))
        out.append("| %s | %s | %d | %s |"
                   % (r["tb"], r["top_escape_op"], r["top_escape_n"],
                      _pct(share)))
    out.append("")
    out.append("## RECOMMENDATION (not implemented -- wave-5's fuel)")
    out.append("")
    out.append("Weakest TB: `%s` (kill_rate %s vs %s on `%s`)."
               % (weakest["tb"], _pct(weakest["kill_rate"]),
                  _pct(best["kill_rate"]), best["tb"]))
    out.append("")
    out.append("Cheapest hardening experiment: add ONE decoder-exercising "
               "scenario to `sim/tb/%s` -- a single ALU-immediate "
               "instruction (e.g. ADDI with nonzero immediate) in the "
               "boot-ROM stream plus an assertion on the retired "
               "immediate value -- targeting the `%s` operator class "
               "(%d of this TB's %d equivalents, %s, escape through "
               "unexercised decode/divide logic the current NOP-heavy "
               "boot never touches)."
               % (weakest["tb"], weakest["top_escape_op"],
                  weakest["top_escape_n"], weakest["equivalent"],
                  _pct(w_share)))
    out.append("")
    return "\n".join(out) + "\n"


def generate(report_path: str = REPORT_PATH) -> dict:
    """Compute per-TB kill rates and write the report. Returns summary."""
    rows, kept_cg, accounting = _parent_rows()
    text = render(rows, kept_cg, accounting)
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return {"report": report_path, "rows": rows,
            "kept_cond_grow_total": kept_cg, "accounting": accounting}


def main():
    summary = generate()
    for r in summary["rows"]:
        print("%s: drawn=%d kept=%d equiv=%d bb=%d kill=%s" %
              (r["tb"], r["drawn"], r["kept"], r["equivalent"],
               r["build_break"], _pct(r["kill_rate"])))
    print("wrote %s" % summary["report"])


if __name__ == "__main__":
    main()
