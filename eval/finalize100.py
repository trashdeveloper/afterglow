"""H10 landing: re-verify every kept mutant through the sanctioned
firing path, then materialize the committable artifacts.

For each kept entry in /tmp/mutboard100/board.json:
  1. Write sim/targets/mut-100-XXX.yaml (anchor=before, bug=after,
     pinned mutant_line, parent TB/oracle) — same schema as the
     sanctioned mut-pilot specs.
  2. Re-verify via loop.prepare_mutant_tree (the exact path a live
     fire uses) + judge_tree. Non-kept on re-verify is DROPPED with a
     documented reason (never silently kept).
  3. Save the full boot log to eval/mutant_logs_100/mut-100-XXX.boot.log.

Then finalize the board copy (finalized_at_N, ceiling note), render
the markdown, and report. Does NOT edit the test or plan (controller).
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))
sys.path.insert(0, os.path.join(ROOT, "agent"))

import draw_board as lib  # noqa: E402
import loop  # noqa: E402
from afterglow_core.verilator import load_case_file  # noqa: E402
import vbackend  # noqa: E402

WORKDIR = "/tmp/mutboard100"
LOGDIR = os.path.join(ROOT, "eval", "mutant_logs_100")
SPEC_STEMS = os.path.join(ROOT, "sim", "targets")

SPEC_TMPL = """# SANCTIONED mutant spec {mid} (H10 nightly board, seed 777).
# Firing rule: agent/loop.py --spec ONLY (line-targeted apply of bug at
# mutant_line in a runs/scratch copy of the pinned pristine tree).
# Fault provenance: eval/mutate.py {op} on {mfile}:{mline}
# (before {before!r}, after {after!r}); kept-tree at
# /tmp/mutboard100/kept/{mid} is PROVENANCE ONLY -- firing rebuilds
# byte-identically from anchor/bug+line (see eval/FIRING.md pattern).
# Parent TB/oracle: {parent}. Red = oracle FAIL, green = patch restores PASS.
project: "picorv32"
files: [{mfile_q}]
top: "{top}"
tb: "{tb}"
vflags: [{vflags}]
anchor: {anchor_q}
bug: {bug_q}
gate: "{parent} H10 {op}"
oracle_pass: "tohost: PASS"
oracle_trap: "TRAP:"
mutant_id: "{mid}"
mutant_seed: 777
mutant_op: "{op}"
mutant_file: "{mfile}"
mutant_line: {mline}
parent_case: "{parent}"
mutant_tree: "/tmp/mutboard100/kept/{mid}"
"""


def q(s):
    return json.dumps(str(s))


def main():
    board = json.load(open(os.path.join(WORKDIR, "board.json")))
    os.makedirs(LOGDIR, exist_ok=True)
    kept = board["kept"]
    redone, dropped = [], []
    for k in kept:
        mid = k["mutant_id"]
        spec_path = os.path.join(SPEC_STEMS, "%s.yaml" % mid)
        with open(spec_path, "w", encoding="utf-8") as fh:
            fh.write(SPEC_TMPL.format(
                mid=mid, op=k["op"], mfile=k["file"], mline=k["line"],
                before=k["before"], after=k["after"], parent=k["parent_case"],
                mfile_q=q(k["file"]), top=k["parent"]["top"],
                tb=k["parent"]["tb"],
                vflags=", ".join(q(v)
                                 for v in k["parent"].get("vflags", [])),
                anchor_q=q(k["before"]), bug_q=q(k["after"])))
        case = load_case_file(spec_path)
        dest = os.path.join(WORKDIR, "land-%s" % mid)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        base = board["base"]["tree"]
        try:
            loop.prepare_mutant_tree(case, base, dest)
        except Exception as e:  # noqa: BLE001 (documented drop)
            dropped.append((mid, "prepare failed: %s" % e))
            continue
        parent = vbackend.load_case(k["parent_case"])
        status, rec = lib.judge_tree(dest, parent)
        shutil.rmtree(dest, ignore_errors=True)
        if status != "kept":
            dropped.append((mid, "reverify status=%s rec=%r"
                            % (status, {x: rec.get(x)
                                        for x in ("note", "cycles")})))
            continue
        with open(os.path.join(LOGDIR, "%s.boot.log" % mid), "w",
                   encoding="utf-8") as fh:
            fh.write(rec["log"])
        k["red"] = {"boot_log": "eval/mutant_logs_100/%s.boot.log" % mid,
                    "cycles": rec["cycles"],
                    "oracle_lines": rec["oracle_lines"],
                    "rc": rec.get("rc", 0),
                    "reverified_landing": True}
        redone.append(mid)
    print("reverified=%d dropped=%d" % (len(redone), len(dropped)))
    for mid, reason in dropped:
        print("DROPPED %s: %s" % (mid, reason))
    board["kept"] = [k for k in board["kept"]
                     if k["mutant_id"] in set(redone)]
    json.dump(board, open(os.path.join(WORKDIR, "board-landed.json"), "w"),
              indent=2)
    print("wrote board-landed.json with %d keeps" % len(redone))


if __name__ == "__main__":
    main()
