#!/usr/bin/env python3
"""Live per-TB kill-matrix measurement (wave-5: hardened oracles as measured).

Recomputes the kill matrix against the CURRENT tree by actually firing
every kept mutant under every testbench (real Verilator builds+runs on
scratch copies of the pinned pristine trees). CPU-only, deterministic
sim seed, no API calls, no network, no commits.

Why a new file instead of extending ``eval/tb_kill_rate.py``: that
script is STATIC-only by contract (board.json tallies + spec mapping;
its test pins "no sim, no build"). This script is the LIVE counterpart:
it measures what the oracles actually catch now that wave-4 seeding +
wave-5 native TBs (fetch-decode, serv-alu-lut) hardened them. It reuses
the same flat-spec loader and the loop's own pinned-line firing path
(``agent.loop.prepare_mutant_tree``), but shares no report code.

Method per (mutant, TB) cell (sim seed fixed, default 42):
- cross-project (mutant project != TB project): ``error`` -- the fault
  file cannot exist in that tree, so no build is attempted. error != killed.
- fault file not in the TB's build files: ``survived`` by
  pristine-equivalence -- the compiled sources are byte-identical to the
  live-measured pristine baseline of that TB (which must be GREEN or the
  run aborts), so the verdict is reused and counted separately as
  ``equiv_reuse``. Sound: identical sources -> identical binary behavior.
- else: copy pristine tree -> apply anchor->bug at the PINNED line only
  (loop.prepare_mutant_tree, fails loudly on anchor drift) -> verilator
  build -> run +seed=N (300 s timeout) -> ``killed`` iff the oracle
  marker is absent, ``survived`` iff present, ``timeout`` on expiry,
  ``error`` on build/fire failure. error != killed, timeout != killed.

kill_rate per TB (and TOTAL) = killed / (killed + survived + timeout):
errors leave the denominator, same convention as the published static
report (build-breakers excluded there). Rows that cannot fire anything
(all-error, e.g. serv vs picorv32 mutants) report ``n/a``.

Outputs:
- ``eval/kill_rate_v2.json``: full per-cell proof (verdict, tohost,
  cycles, checksum, runtime_s, note). Timings are machine-dependent and
  NOT part of determinism.
- ``eval/kill_rate_part2.md``: deterministic report (no timestamps, no
  timings): per-TB rows + totals + delta vs the published static
  kill-rates + classification changes + the full 50x4 matrix + the exact
  reproduce command. Re-rendering from the same JSON yields
  byte-identical markdown.

Usage:
  python3 eval/kill_rate_v2.py [--seed 42]
  python3 eval/kill_rate_v2.py --seed 42 --json /tmp/kr2.json --report /tmp/kr2.md
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, REPO)

import vbackend  # noqa: E402
from agent import loop as agent_loop  # noqa: E402

TARGETS = os.path.join(REPO, "sim", "targets")
MUT_GLOB_BIGN = os.path.join(TARGETS, "mut-bigN-*.yaml")
MUT_GLOB_PILOT = os.path.join(TARGETS, "mut-pilot-*.yaml")
DEFAULT_JSON = os.path.join(REPO, "eval", "kill_rate_v2.json")
DEFAULT_REPORT = os.path.join(REPO, "eval", "kill_rate_part2.md")

RUN_TIMEOUT_S = 300
SEED_DEFAULT = 42

# TB harnesses under test: key -> eval-case stem whose top/tb/files/
# vflags/oracle define the build. The two parent TBs (fetch, uart) plus
# the two wave-5 native TBs (decode, serv).
TB_HARNESS = {
    "fetch": "picorv32-fetch-misalign",
    "uart": "picosoc-uart-div",
    "decode": "picorv32-fetch-decode",
    "serv": "serv-alu-lut",
}

# Published STATIC kill-rates (eval/tb_kill_rate.md, wave-4 board:
# kept / (drawn - build_break) over drawn candidates). Quoted here for
# the delta line; the report cites the source file, not this constant.
PUBLISHED = {
    "fetch": {"killed": 30, "denom": 2206, "rate": 30 / 2206},
    "uart": {"killed": 15, "denom": 49, "rate": 15 / 49},
    "total": {"killed": 45, "denom": 2255, "rate": 45 / 2255},
}


def _load_mutants():
    """All 50 kept mutants: 45 big-N + 5 pilot, sorted by mutant_id."""
    paths = sorted(glob.glob(MUT_GLOB_BIGN) + glob.glob(MUT_GLOB_PILOT))
    if len(paths) != 50:
        raise SystemExit("want exactly 50 mutant specs, found %d" % len(paths))
    mutants = []
    for p in paths:
        case = vbackend.load_case(os.path.basename(p)[:-5])
        for req in ("mutant_id", "mutant_file", "mutant_line", "anchor",
                    "bug", "project", "tb", "parent_case", "oracle_pass"):
            if req not in case or str(case[req]).strip() == "":
                raise SystemExit("spec %s missing %s" % (p, req))
        mutants.append(case)
    ids = [m["mutant_id"] for m in mutants]
    if len(set(ids)) != 50:
        raise SystemExit("duplicate mutant ids")
    return mutants


def _harness(tbkey):
    """Harness case dict + pristine tree dir for a TB key. No fetch."""
    stem = TB_HARNESS[tbkey]
    case = vbackend.load_case(stem)
    pins = vbackend.load_pins()
    if case["project"] not in pins:
        raise SystemExit("unknown project %r" % case["project"])
    _url, sha = pins[case["project"]]
    tree = str(vbackend.tree_dir(case["project"], sha))
    if not os.path.isdir(tree):
        raise SystemExit(
            "pristine tree absent (no network fetch in measurement): %s"
            % tree)
    need = ["project", "files", "top", "tb", "oracle_pass"]
    for req in need:
        if req not in case:
            raise SystemExit("harness %s missing %s" % (stem, req))
    files = case["files"]
    if isinstance(files, str):
        files = [files]
    return {"key": tbkey, "stem": stem, "project": case["project"],
            "files": list(files), "top": case["top"], "tb": case["tb"],
            "vflags": list(case.get("vflags", [])),
            "oracle_pass": case["oracle_pass"],
            "oracle_trap": case.get("oracle_trap", "TRAP:"),
            "tree": tree}


def _pristine_baseline(h):
    """Live pristine build+run for one harness. Must be GREEN or abort."""
    t0 = time.perf_counter()
    tmp = tempfile.mkdtemp(prefix="kr2-base-%s-" % h["key"])
    try:
        dest = os.path.join(tmp, "tree")
        shutil.copytree(h["tree"], dest,
                        ignore=shutil.ignore_patterns(
                            ".git", "__pycache__", "obj_afterglow_*"))
        case = {"name": "kr2-base-%s" % h["key"], "files": h["files"],
                "top": h["top"], "tb": h["tb"], "vflags": h["vflags"],
                "oracle_pass": h["oracle_pass"],
                "oracle_trap": h["oracle_trap"]}
        t1 = time.perf_counter()
        binary = vbackend.build(dest, case)
        t2 = time.perf_counter()
        try:
            proc = subprocess.run(
                [str(binary), "+seed=%d" % SEED_DEFAULT], cwd=str(dest),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=RUN_TIMEOUT_S)
            log = proc.stdout
        except subprocess.TimeoutExpired:
            raise SystemExit("BASELINE TIMEOUT on pristine %s (refusing)"
                             % h["key"])
        passed = h["oracle_pass"] in log
        det = [ln for ln in log.splitlines() if not ln.startswith("- ")]
        checksum = hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()
        tohost = None
        for ln in log.splitlines():
            if "tohost:" in ln:
                tohost = ln.split("tohost:", 1)[1].strip()
        m = re.search(r"CYCLES=(\d+)", log)
        return {"pass": passed, "tohost": tohost, "checksum": checksum,
                "cycles": int(m.group(1)) if m else None,
                "build_s": round(t2 - t1, 1), "run_s": round(
                    time.perf_counter() - t2, 1),
                "total_s": round(time.perf_counter() - t0, 1)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fire_live(mut, h, seed):
    """Fire one mutant under one harness. Returns cell dict (no timing)."""
    if mut["project"] != h["project"]:
        return {"verdict": "error", "tohost": None, "cycles": None,
                "checksum": None, "built": False,
                "note": "cross-project: mutant file %s not in %s tree "
                        "(no build attempted; error != killed)"
                        % (mut["mutant_file"], h["project"])}
    if mut["mutant_file"] not in h["files"]:
        return {"verdict": "survived", "tohost": None, "cycles": None,
                "checksum": None, "equiv_reuse": True, "built": False,
                "note": "fault file outside TB build; pristine-baseline "
                        "verdict reused (identical sources)"}
    tmp = tempfile.mkdtemp(prefix="kr2-cell-")
    try:
        dest = os.path.join(tmp, "tree")
        try:
            agent_loop.prepare_mutant_tree(mut, h["tree"], dest)
        except Exception as exc:  # anchor drift etc: honest error
            return {"verdict": "error", "tohost": None, "cycles": None,
                    "checksum": None, "built": True,
                    "note": "fire failed: %s: %s"
                            % (type(exc).__name__, exc)}
        case = {"name": "kr2-%s-%s" % (
                    h["key"], re.sub(r"[^A-Za-z0-9]+", "-", mut["mutant_id"])),
                "files": h["files"], "top": h["top"], "tb": h["tb"],
                "vflags": h["vflags"], "oracle_pass": h["oracle_pass"],
                "oracle_trap": h["oracle_trap"]}
        try:
            binary = vbackend.build(dest, case)
        except Exception as exc:
            return {"verdict": "error", "tohost": None, "cycles": None,
                    "checksum": None, "built": True,
                    "note": "build failed: %s: %s"
                            % (type(exc).__name__, str(exc)[-300:])}
        try:
            proc = subprocess.run(
                [str(binary), "+seed=%d" % seed], cwd=str(dest),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=RUN_TIMEOUT_S)
            log = proc.stdout
        except subprocess.TimeoutExpired:
            return {"verdict": "timeout", "tohost": None, "cycles": None,
                    "checksum": None, "built": True,
                    "note": "run exceeded %ds (timeout != killed)"
                            % RUN_TIMEOUT_S}
        tohost = None
        for ln in log.splitlines():
            if "tohost:" in ln:
                tohost = ln.split("tohost:", 1)[1].strip()
        m = re.search(r"CYCLES=(\d+)", log)
        det = [ln for ln in log.splitlines() if not ln.startswith("- ")]
        checksum = hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()
        if h["oracle_pass"] in log:
            return {"verdict": "survived", "tohost": tohost,
                    "cycles": int(m.group(1)) if m else None,
                    "checksum": checksum, "built": True,
                    "note": "oracle marker present (GREEN)"}
        trap_line = next((ln.strip() for ln in log.splitlines()
                          if "TRAP:" in ln), None)
        return {"verdict": "killed", "tohost": tohost,
                "cycles": int(m.group(1)) if m else None,
                "checksum": checksum, "built": True,
                "note": "oracle absent (RED)%s" % (
                    "; %s" % trap_line if trap_line else "")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _pct(x):
    return "n/a" if x != x else "%.2f%%" % (100.0 * x)


def measure(seed=SEED_DEFAULT):
    """Fire the full 50x4 matrix. Returns results dict (timings inside)."""
    t_start = time.perf_counter()
    mutants = _load_mutants()
    harnesses = {k: _harness(k) for k in TB_HARNESS}
    baselines = {}
    for k, h in harnesses.items():
        print("baseline pristine run: TB=%s ..." % k, flush=True)
        b = _pristine_baseline(h)
        baselines[k] = b
        print("  %s baseline: %s tohost=%s cycles=%s checksum=%s build=%.1fs run=%.1fs" % (
            k, "PASS" if b["pass"] else "RED", b["tohost"], b["cycles"],
            b["checksum"][:12], b["build_s"], b["run_s"]), flush=True)
        if not b["pass"]:
            raise SystemExit("pristine baseline RED under %s -- refusing "
                             "(oracle broken, measurement meaningless)" % k)
    cells = {}
    counts = {k: {"killed": 0, "survived": 0, "timeout": 0, "error": 0,
                  "equiv_reuse": 0, "live": 0} for k in harnesses}
    for hkey in TB_HARNESS:
        h = harnesses[hkey]
        for mut in mutants:
            mid = mut["mutant_id"]
            t0 = time.perf_counter()
            cell = _fire_live(mut, h, seed)
            dt = round(time.perf_counter() - t0, 1)
            if cell["verdict"] == "survived" and cell.get("equiv_reuse"):
                cell = dict(cell, tohost=baselines[hkey]["tohost"],
                            cycles=baselines[hkey]["cycles"],
                            checksum=baselines[hkey]["checksum"])
            if cell.get("built"):
                counts[hkey]["live"] += 1
            if cell.get("equiv_reuse"):
                counts[hkey]["equiv_reuse"] += 1
            counts[hkey][cell["verdict"]] += 1
            cell["runtime_s"] = dt
            cells[(mid, hkey)] = cell
        c = counts[hkey]
        denom = c["killed"] + c["survived"] + c["timeout"]
        rate = c["killed"] / denom if denom else float("nan")
        print("TB=%s: killed=%d survived=%d (reuse %d) timeout=%d error=%d "
              "live=%d kill_rate=%s" % (
                  hkey, c["killed"], c["survived"], c["equiv_reuse"],
                  c["timeout"], c["error"], c["live"], _pct(rate)),
              flush=True)
    total = {k: sum(counts[h][k] for h in counts) for k in
             ("killed", "survived", "timeout", "error")}
    total_denom = total["killed"] + total["survived"] + total["timeout"]
    total_rate = total["killed"] / total_denom if total_denom else float("nan")
    # classification changes vs old matrix: every kept mutant was RED
    # under its parent TB (board kept-RED + pilot RED logs); flag any
    # parent-TB cell that is not killed now.
    changed = []
    for mut in mutants:
        mid = mut["mutant_id"]
        parent_tb = mut["tb"]
        parent_key = next((k for k, h in harnesses.items()
                           if h["tb"] == parent_tb), None)
        cell = cells.get((mid, parent_key)) if parent_key else None
        got = cell["verdict"] if cell else "no-harness"
        if got != "killed":
            changed.append({"mutant_id": mid,
                            "parent_case": mut["parent_case"],
                            "parent_tb": parent_tb, "now": got,
                            "note": (cell or {}).get("note", "")})
    results = {
        "seed": seed,
        "mutants": [{"mutant_id": m["mutant_id"],
                     "parent_case": m["parent_case"], "tb": m["tb"],
                     "mutant_file": m["mutant_file"],
                     "mutant_op": m.get("mutant_op")} for m in mutants],
        "harnesses": {k: {"stem": h["stem"], "tb": h["tb"],
                          "project": h["project"], "files": h["files"],
                          "top": h["top"]}
                      for k, h in harnesses.items()},
        "baselines": baselines,
        "cells": [{"mutant_id": mid, "tb": hkey,
                   "verdict": cells[(mid, hkey)]["verdict"],
                   "tohost": cells[(mid, hkey)]["tohost"],
                   "cycles": cells[(mid, hkey)]["cycles"],
                   "checksum": cells[(mid, hkey)]["checksum"],
                   "runtime_s": cells[(mid, hkey)]["runtime_s"],
                   "built": cells[(mid, hkey)].get("built", False),
                   "note": cells[(mid, hkey)]["note"]}
                  for (mid, hkey) in sorted(cells)],
        "counts": counts,
        "total": dict(total, rate=total_rate),
        "classification_changed": changed,
        "wall_s": round(time.perf_counter() - t_start, 1),
    }
    return results


def _row_rate(c):
    denom = c["killed"] + c["survived"] + c["timeout"]
    return c["killed"] / denom if denom else float("nan")


def render(results):
    """Deterministic markdown from results (timings excluded)."""
    counts = results["counts"]
    order = list(TB_HARNESS)
    total = results["total"]
    out = []
    out.append("# Per-TB kill matrix, measured live (wave-5, part 2)")
    out.append("")
    out.append("Measured by `eval/kill_rate_v2.py` (live Verilator "
               "builds+runs on scratch copies of the pinned pristine "
               "trees; CPU-only, no API, no network, no commits). "
               "Deterministic sim seed %d; report carries no timestamps "
               "or timings, so re-rendering from the same "
               "`eval/kill_rate_v2.json` is byte-identical." % results["seed"])
    out.append("")
    out.append("Method: every kept mutant (45 big-N + 5 pilot = 50) fired "
               "under every TB harness (2 parent + 2 wave-5 native = 4), "
               "200 cells. Cell taxonomy: killed = oracle marker absent "
               "(RED); survived = marker present (GREEN); timeout = run "
               "exceeded %ds; error = no verdict possible (cross-project "
               "fault, build/fire failure) -- error != killed, timeout != "
               "killed. Faults outside a TB's build files reuse the "
               "live-measured pristine baseline verdict of that TB "
               "(identical sources, counted as equiv_reuse). kill_rate = "
               "killed / (killed + survived + timeout); errors leave the "
               "denominator (same convention as the static report). "
               "Every harness pristine baseline measured GREEN before "
               "firing (checksums in JSON)." % RUN_TIMEOUT_S)
    out.append("")
    out.append("## Per-TB rows (live)")
    out.append("")
    out.append("| TB | harness stem | fired-live | killed | survived "
               "(of which reuse) | timeout | error | kill_rate |")
    out.append("|---|---|---|---|---|---|---|---|")
    for k in order:
        c = counts[k]
        h = results["harnesses"][k]
        out.append("| %s | %s | %d | %d | %d (%d) | %d | %d | %s |"
                   % (h["tb"], h["stem"], c["live"], c["killed"],
                      c["survived"], c["equiv_reuse"], c["timeout"],
                      c["error"], _pct(_row_rate(c))))
    t = results["total"]
    out.append("| TOTAL | 4 harnesses x 50 mutants = 200 cells | %d | %d | "
               "%d | %d | %d | %s |"
               % (sum(counts[k]["live"] for k in order), t["killed"],
                  t["survived"], t["timeout"], t["error"], _pct(t["rate"])))
    out.append("")
    out.append("## Delta vs the published static kill-rates")
    out.append("")
    out.append("Published (`eval/tb_kill_rate.md`, wave-4 board, STATIC over "
               "drawn candidates): fetch 30/2206 = 1.36%, uart 15/49 = "
               "30.61%, total 45/2255 = 2.00%.")
    out.append("")
    out.append("Measured here (LIVE over the 50 kept mutants): fetch %s, "
               "uart %s, decode %s, serv %s, total %s." % (
                   _pct(_row_rate(counts["fetch"])),
                   _pct(_row_rate(counts["uart"])),
                   _pct(_row_rate(counts["decode"])),
                   _pct(_row_rate(counts["serv"])),
                   _pct(t["rate"])))
    out.append("")
    out.append("One-sentence delta: the fetch-oracle live kill-rate over "
               "kept mutants is %s vs the published 1.36%% seeding "
               "kill-rate over drawn candidates -- denominators differ by "
               "construction (50 kept faults here vs 2325 drawn candidates "
               "there, of which 2210 were honest equivalents the oracle "
               "correctly passes), so the live number measures oracle "
               "strength on real faults while 1.36%% measures fault-draw "
               "yield; the genuinely new live signal is the decode row, "
               "which kills %d of the 50 kept mutants with the wave-5 "
               "decoder oracle (per-cell dispositions below)." % (
                   _pct(_row_rate(counts["fetch"])),
                   counts["decode"]["killed"]))
    out.append("")
    out.append("## Classification changes vs the old matrix")
    out.append("")
    old = results["classification_changed"]
    if not old:
        out.append("NONE: all 50 kept mutants are still killed (RED) under "
                   "their parent TB on the current tree -- no kept-RED "
                   "classification changed.")
    else:
        out.append("%d mutant(s) changed classification vs kept-RED:" % len(old))
        out.append("")
        for ch in old:
            out.append("- %s (parent %s / %s): now %s -- %s" % (
                ch["mutant_id"], ch["parent_case"], ch["parent_tb"],
                ch["now"], ch["note"]))
    out.append("")
    out.append("## Full 50x4 matrix (K=killed S=survived T=timeout E=error; "
               "*=pristine-equivalence reuse)")
    out.append("")
    out.append("| mutant | parent TB | fetch | uart | decode | serv |")
    out.append("|---|---|---|---|---|---|")
    cellmap = {(c["mutant_id"], c["tb"]): c for c in results["cells"]}
    for m in results["mutants"]:
        mid = m["mutant_id"]
        row = [mid, m["tb"]]
        for k in order:
            c = cellmap[(mid, k)]
            letter = {"killed": "K", "survived": "S", "timeout": "T",
                      "error": "E"}[c["verdict"]]
            if c.get("note", "").startswith("fault file outside"):
                letter += "*"
            row.append(letter)
        out.append("| %s |" % " | ".join(row))
    out.append("")
    out.append("## Reproduce (exact command, CPU-only, no API)")
    out.append("")
    out.append("```")
    out.append("python3 eval/kill_rate_v2.py --seed %d" % results["seed"])
    out.append("```")
    out.append("")
    out.append("Writes `eval/kill_rate_v2.json` (per-cell proof incl. "
               "checksums, cycles, runtimes) and this report. Rerun is "
               "verdict-deterministic (fixed seed, pinned trees, pinned "
               "TBs); only `runtime_s`/`wall_s` in the JSON vary.")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Live per-TB kill matrix.")
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    ap.add_argument("--json", default=DEFAULT_JSON)
    ap.add_argument("--report", default=DEFAULT_REPORT)
    args = ap.parse_args(argv)
    results = measure(seed=args.seed)
    text = render(results)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, sort_keys=False)
        fh.write("\n")
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %s + %s in %.1fs" % (args.json, args.report,
                                      results["wall_s"]))
    print("TOTAL kill_rate=%s killed=%d survived=%d timeout=%d error=%d" % (
        _pct(results["total"]["rate"]), results["total"]["killed"],
        results["total"]["survived"], results["total"]["timeout"],
        results["total"]["error"]))
    print("classification_changed=%d" % len(results["classification_changed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
