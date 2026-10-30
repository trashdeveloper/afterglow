#!/usr/bin/env python3
"""H10: 100-mutant nightly board (controller-authored, new seeds).

Reuses eval/draw_board.py machinery (setup_base, pool, pilot exclusion,
verify_candidate, write_spec) with a FRESH draw seed and a NEW id family
(mut-100-*), so all 100 kept mutants are quarantined from the pilot,
big-N, pair, and stranger families. Controller-run sim only, no API.

Usage:
  python3 eval/mine100.py --workdir /tmp/mutboard100   # full campaign
  python3 eval/mine100.py --workdir /tmp/mutboard100 --resume
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))

import draw_board as lib  # noqa: E402  (reused, not modified)

DRAW_SEED = 777
DRAW_K = 20000  # draw_order caps at available pool; covers full sweep
TARGET_NEW = 100
BOARD_NAME = "mutant-board-100"
# H10 scope = bigN scope (stable pool indices: picorv32.v <
# picosoc/simpleuart.v, so resume done-sets stay valid). spimemio.v was
# considered and REJECTED: sim/tb/picosoc-spimemio_tb.sv does not exist
# (H2's TB was honestly skipped), so no oracle could judge it.
SCOPE_100 = ["picosoc/simpleuart.v", "picorv32.v"]
PARENT_100 = {"picosoc/simpleuart.v": "picosoc-uart-div",
              "picorv32.v": "picorv32-fetch-misalign"}
# Second oracle for picorv32.v: the fetch-decode TB checks different
# properties than the fetch TB, so mutants the first misses the second
# may catch (each keep still needs a real FAIL boot log; the catching
# oracle is recorded per kept entry).
SECONDARY_100 = {"picorv32.v": "picorv32-fetch-decode"}


def load_parents_100():
    import vbackend  # sim/vbackend.py (strict flat parser, same as bigN)
    return {rel: vbackend.load_case(PARENT_100[rel]) for rel in SCOPE_100}


def load_secondary_100():
    import vbackend
    return {rel: vbackend.load_case(SECONDARY_100[rel])
            for rel in SECONDARY_100}
OUT_JSON = os.path.join(ROOT, "eval", "mutant_board_100.json")
OUT_MD = os.path.join(ROOT, "eval", "mutant_board_100.md")


def prior_bug_texts(base, targets_dir):
    """(prior_lines, prior_texts) from every pre-H10 mutant/stranger spec.

    prior_lines: {(file, line)} — pool-level exclusion (conservative:
    a mined mutant at an already-used fault site is not fresh evidence,
    regardless of op).
    prior_texts: {(file, line, bug_stripped)} — exact-duplicate detection
    on the applied after-text (catches same-fault-different-site draws).
    Schemas handled: flat mutant specs (mutant_file/mutant_line/bug),
    pair specs (faults list), stranger specs (anchor/bug, line located
    by anchor-text match).
    """
    import glob as _glob

    import yaml as _yaml

    prior_lines = set()
    prior_texts = set()

    def _add(rel, lineno, bug):
        if rel and lineno and bug:
            prior_lines.add((rel, int(lineno)))
            prior_texts.add((rel, int(lineno), str(bug).strip()))

    for path in sorted(_glob.glob(os.path.join(targets_dir, "*.yaml"))):
        stem = os.path.basename(path)
        if stem.startswith("mut-100-"):
            continue
        with open(path, encoding="utf-8") as fh:
            try:
                spec = _yaml.safe_load(fh)
            except Exception:
                continue
        if not isinstance(spec, dict):
            continue
        if isinstance(spec.get("faults"), list):
            for f in spec["faults"]:
                if isinstance(f, dict):
                    _add(f.get("file"), f.get("line"), f.get("bug"))
        elif "mutant_file" in spec:
            _add(spec.get("mutant_file"), spec.get("mutant_line"),
                 spec.get("bug"))
        elif "anchor" in spec and "bug" in spec:
            anchor = str(spec["anchor"]).strip()
            found = None
            for rel in SCOPE_100:
                full = os.path.join(base, rel)
                if not os.path.isfile(full):
                    continue
                with open(full, encoding="utf-8") as fh:
                    for n, text in enumerate(fh.read().splitlines(), 1):
                        if text.strip() == anchor:
                            found = (rel, n)
                            break
                if found:
                    break
            if found:
                _add(found[0], found[1], spec["bug"])
    return prior_lines, prior_texts


def run_campaign_100(args):
    os.makedirs(args.workdir, exist_ok=True)
    os.makedirs(lib.LOGS_DIR, exist_ok=True)
    skips = lib.mutate.load_locked_skips()

    if args.resume and os.path.isfile(lib.board_path(args.workdir)):
        with open(lib.board_path(args.workdir)) as fh:
            board = json.load(fh)
        if board.get("complete"):
            print("board already complete: %s"
                  % lib.board_path(args.workdir))
            return 0
        if board.get("draw_seed") != DRAW_SEED:
            raise SystemExit("resume seed mismatch")
        base = board["base"]["tree"]
        print("resuming: %d drawn so far" % board["accounting"]["drawn"])
    else:
        base, sanctree, pinsha = lib.setup_base(args.workdir)
        parents = load_parents_100()
        pool = lib.mutate.enumerate_candidates(
            base, skips, files=SCOPE_100)
        with open(lib.PILOT_MANIFEST) as fh:
            pilot = json.load(fh)
        _fresh_parents = dict(parents)
        for _rel, _case in load_secondary_100().items():
            _fresh_parents["secondary:" + _rel] = _case
        baseline = lib.run_pristine_controls(
            base, _fresh_parents, lib.LOGS_DIR)
        board = {
            "board": BOARD_NAME,
            "draw_seed": DRAW_SEED,
            "sim_seed": lib.SIM_SEED,
            "pool": {"files": list(SCOPE_100), "size": len(pool),
                      "pin": "picorv32 %s" % pinsha,
                      "sanctioned_tree": sanctree},
            "base": {"tree": base},
            "baseline": baseline,
            "parents": {rel: {"case": c["name"], "top": c["top"],
                              "tb": c["tb"],
                              "oracle_pass": c["oracle_pass"]}
                        for rel, c in parents.items()},
            "pilot_import": {
                "manifest": os.path.relpath(lib.PILOT_MANIFEST, lib.REPO),
                "ids": [p["mutant_id"] for p in pilot["pilot"]],
                "reverified": {},
                "method": ("fire each sanctioned spec via "
                           "loop.prepare_mutant_tree (anchor/bug + line)."),
            },
            "draw": {},
            "kept": [],
            "discarded_equivalent": [],
            "discarded_build_break": [],
            "discarded_other": [],
            "untried": [],
            "accounting": {"drawn": 0, "kept_new": 0, "kept_total": 0,
                           "equivalent": 0, "build_break": 0, "other": 0},
            "self_check": {"ran": False},
        }
    parents = load_parents_100()
    secondaries = load_secondary_100()
    pool = lib.mutate.enumerate_candidates(
        base, skips, files=SCOPE_100)
    # Resume across scope extension: run pristine controls for any parent
    # case added after the board was created, merge, persist. Includes
    # secondary oracles (fetch-decode), keyed distinctly.
    _all_parents = dict(parents)
    for _rel, _case in secondaries.items():
        _all_parents["secondary:" + _rel] = _case
    for rel, case in _all_parents.items():
        if case["name"] not in board.get("baseline", {}):
            extra = lib.run_pristine_controls(
                base, {rel: case}, lib.LOGS_DIR)
            board["baseline"].update(extra)
            lib.save_board(lib.board_path(args.workdir), board)
            print("baseline (late parent): %s GREEN" % case["name"])

    if not board["pilot_import"]["reverified"]:
        for pid in board["pilot_import"]["ids"]:
            status, rec, _case = lib.reverify_pilot_via_spec(
                base, args.workdir, pid, parents)
            log_path = os.path.join(
                lib.LOGS_DIR, "board100-reverify-%s.log" % pid)
            if status == "kept":
                with open(log_path, "w", encoding="utf-8") as fh:
                    fh.write(rec.pop("log"))
                board["pilot_import"]["reverified"][pid] = {
                    "red": True,
                    "boot_log": os.path.relpath(log_path, lib.REPO),
                    "cycles": rec["cycles"],
                    "oracle_lines": rec["oracle_lines"],
                }
            else:
                board["pilot_import"]["reverified"][pid] = {
                    "red": False, "status": status, "record": rec,
                }
                print("PILOT DRIFT: %s re-verified as %s (was RED)"
                      % (pid, status))
        lib.save_board(lib.board_path(args.workdir), board)

    pilot_red = sum(1 for v in board["pilot_import"]["reverified"].values()
                    if v.get("red"))
    need_new = TARGET_NEW
    print("pilot re-verified: %d/5 RED; need %d new kept"
          % (pilot_red, need_new))

    exclude = lib.pilot_exclusion(pool, base)
    prior_lines, prior_texts = prior_bug_texts(base, lib.SPECS_DIR)
    for i, c in enumerate(pool):
        if (c["file"], c["line"]) in prior_lines:
            exclude.add(i)
    print("exclusion: %d pilot tuples + %d prior-family lines "
          "(pairs/bigN/strangers); %d pool indices total" % (
              len(lib.pilot_exclusion(pool, base)),
          len(prior_lines), len(exclude)))
    order, signs = lib.draw_order(len(pool), DRAW_SEED, exclude, DRAW_K)
    if not board.get("draw", {}).get("order"):
        board["draw"] = {
            "note": ("Random(%d).sample(pool, %d) over pool minus pilot "
                     "tuples AND all prior-family fault lines "
                     "(mut-pilot/bigN/pair/stranger, %d lines); signs "
                     "from the same stream (pilot convention)."
                     % (DRAW_SEED, DRAW_K, len(prior_lines))),
            "order": order, "signs": signs,
            "excluded_pilot": sorted(exclude),
        }
    else:
        # Resume: original draw record is provenance — keep it, record the
        # amendment (extended exclusion => new tail order for unvisited).
        board["draw"]["resumed_with_extended_exclusion"] = {
            "prior_family_lines": len(prior_lines),
            "excluded_pool_indices": len(exclude),
            "note": ("pre-exclusion keeps drawn under the original order; "
                     "unvisited positions continue under the extended "
                     "exclusion order; visited pool indices are skipped "
                     "via the done set in both cases."),
        }
    kept_keys = {(k["file"], int(k["line"]), k["after"].strip())
                 for k in board["kept"]}

    done = {d["pool_index"] for d in (
        board["discarded_equivalent"] + board["discarded_build_break"]
        + board["discarded_other"])}
    done |= {k["pool_index"] for k in board["kept"]}
    seen = set(done)  # visited: pre-run done + this run's attempts
    def process(pos, idx, sign, tag, pmap):
        """One (pool index, sign, oracle) attempt: verify + branch."""
        cand = pool[idx]
        status, rec = lib.verify_candidate(
            base, args.workdir, tag, cand, sign, pmap)
        entry = {"candidate": pos, "pool_index": idx, "sign": sign,
                 "record": {"file": cand["file"], "line": cand["line"],
                            "op": cand["op"], "site": cand["site"],
                            "before": rec.get("before", ""),
                            "after": rec.get("after", "")}}
        if status == "kept":
            after_text = str(rec.get("after", "")).strip()
            dup_key = (cand["file"], cand["line"], after_text)
            if dup_key in kept_keys or dup_key in prior_texts:
                entry["note"] = ("duplicate-fault: same (file, line, "
                                 "after-text) as an already-kept or "
                                 "prior-family fault; not fresh evidence")
                entry["oracle_lines"] = rec.get("oracle_lines")
                entry["cycles"] = rec.get("cycles")
                board["discarded_other"].append(entry)
                board["accounting"]["other"] += 1
                print("DUP  %s %s:%d [%s] already-known "
                      "after-text, discarded" % (
                          tag, cand["file"], cand["line"], cand["op"]),
                      flush=True)
            else:
                kept_n = board["accounting"]["kept_new"] + 1
                mut_id = "mut-100-%03d" % kept_n
                kept_tree = os.path.join(args.workdir, "kept", mut_id)
                if os.path.exists(kept_tree):
                    shutil.rmtree(kept_tree)
                shutil.move(os.path.join(args.workdir, tag), kept_tree)
                log_path = os.path.join(lib.LOGS_DIR, "%s-boot.log" % mut_id)
                with open(log_path, "w", encoding="utf-8") as fh:
                    fh.write(rec.pop("log"))
                spec_path = os.path.join(lib.SPECS_DIR, "%s.yaml" % mut_id)
                kept_entry = {
                    "mutant_id": mut_id, "candidate": pos,
                    "pool_index": idx, "seed": DRAW_SEED, "sign": sign,
                    "file": cand["file"], "line": cand["line"],
                    "op": cand["op"], "site": cand["site"],
                    "before": entry["record"]["before"].strip(),
                    "after": entry["record"]["after"].strip(),
                    "parent_case": pmap[cand["file"]]["name"],
                    "parent": copy.deepcopy(pmap[cand["file"]]),
                    "tree": kept_tree,
                    "red": {"boot_log": os.path.relpath(log_path, lib.REPO),
                            "cycles": rec["cycles"], "rc": rec["rc"],
                            "oracle_lines": rec["oracle_lines"]},
                    "spec": os.path.relpath(spec_path, lib.REPO),
                }
                kept_entry["parent"].pop("name", None)
                lib.write_spec(kept_entry, spec_path)
                board["kept"].append(kept_entry)
                kept_keys.add((cand["file"], cand["line"],
                               kept_entry["after"].strip()))
                board["accounting"]["kept_new"] += 1
                board["accounting"]["kept_total"] += 1
                print("KEPT %s %s:%d [%s] site%d cycles=%s" % (
                    mut_id, cand["file"], cand["line"], cand["op"],
                    cand["site"], rec["cycles"]), flush=True)
        elif status == "equivalent":
            entry["oracle_lines"] = rec["oracle_lines"]
            entry["cycles"] = rec["cycles"]
            board["discarded_equivalent"].append(entry)
            board["accounting"]["equivalent"] += 1
        elif status == "build_break":
            entry["note"] = rec["note"]
            board["discarded_build_break"].append(entry)
            board["accounting"]["build_break"] += 1
        else:
            entry.update(rec)
            board["discarded_other"].append(entry)
            board["accounting"]["other"] += 1
        board["accounting"]["drawn"] += 1
        lib.save_board(lib.board_path(args.workdir), board)
        return status

    for pos, (idx, sign) in enumerate(zip(order, signs)):
        if board["accounting"]["kept_new"] >= need_new:
            board["untried"] = order[pos:]
            break
        if idx in done:
            continue
        seen.add(idx)
        _tag = "cand-%04d" % pos
        _st = process(pos, idx, sign, _tag, parents)
        if _st == "equivalent" and pool[idx]["file"] in secondaries:
            # Second oracle, same sign: the fetch TB may simply not
            # check what this mutant breaks (decode checks differ).
            _sec = {pool[idx]["file"]:
                    secondaries[pool[idx]["file"]]}
            _st = process(pos, idx, sign, _tag + "D", _sec)
        if _st == "equivalent":
            # Opposite-sign retry: the drawn sign may simply be the
            # behavior-neutral direction (caught live: pre-patch
            # equivalents were never re-tried, stranding kills).
            process(pos, idx, -sign, _tag + "R", parents)

    # Phase 2: retry sweep over equivalents from before the matrix
    # existed. Those positions saw primary TB + one sign only; the
    # secondary oracle and opposite sign are untried dimensions.
    # Only novel lines (not prior-family lines — those are proven
    # barren-or-duplicate). Each attempt runs the same process()
    # branch logic (keep/dup/break accounting identical).
    if board["accounting"]["kept_new"] < need_new and not [
            i for i in order if i not in seen]:
        p2 = [d for d in board["discarded_equivalent"]
              if (d["record"]["file"], d["record"]["line"])
              not in prior_lines]
        print("phase 2: %d pre-matrix equivalents to retry" % len(p2),
              flush=True)
        board["phase2"] = {"note": ("retry sweep over pre-matrix "
                                    "equivalents on novel lines: secondary "
                                    "oracle (same sign) then opposite sign; "
                                    "same branch accounting."),
                           "count": len(p2)}
        for d in p2:
            if board["accounting"]["kept_new"] >= need_new:
                break
            idx, sign, pos = (d["pool_index"], d["sign"],
                              d["candidate"])
            if pool[idx]["file"] in secondaries:
                sec = {pool[idx]["file"]:
                       secondaries[pool[idx]["file"]]}
                if process(pos, idx, sign, "cand-%04dQ" % pos,
                           sec) == "kept":
                    continue
            process(pos, idx, -sign, "cand-%04dW" % pos, parents)

    board["pilot_import"]["red_count"] = pilot_red
    a = board["accounting"]
    assert a["drawn"] == (a["kept_new"] + a["equivalent"]
                          + a["build_break"] + a["other"]), \
        "reconciliation FAILED: %r" % (a,)
    lib.save_board(lib.board_path(args.workdir), board)
    print("drawn=%d kept_new=%d pilot_red=%d equiv=%d "
          "buildbreak=%d other=%d" % (
              a["drawn"], a["kept_new"], pilot_red,
              a["equivalent"], a["build_break"], a["other"]))
    if a["kept_new"] != TARGET_NEW:
        print("TARGET UNMET: kept_new=%d (want %d). Extend DRAW_K "
              "or investigate yield; board left incomplete for --resume."
              % (a["kept_new"], TARGET_NEW))
        return 1
    board["complete"] = True
    with open(lib.board_path(args.workdir), "w", encoding="utf-8") as fh:
        json.dump(board, fh, indent=2)
        fh.write("\n")
    shutil.copyfile(lib.board_path(args.workdir), OUT_JSON)
    print("board complete: %s" % OUT_JSON)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", default="/tmp/mutboard100")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    return run_campaign_100(args)


if __name__ == "__main__":
    sys.exit(main())
