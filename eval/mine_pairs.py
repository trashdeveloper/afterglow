#!/usr/bin/env python3
"""Mine kept-RED single mutants into sanctioned dual-fault pair specs.

Draws PAIRS of kept-RED singles from eval/mutant_pilot/board.json
(kept entries carry {mutant_id, file, line, op, before, after}) into
sanctioned pair specs sim/targets/mut-pair-NN.yaml -- the same flat
schema as the committed mut-pair-01..05 pilots. This module only PAIRS
(chooses faults + proves them jointly RED); it never fires a model and
needs no credentials.

Pairing rules (all rejections are recorded loudly in the manifest):

- same-file pairs ONLY: one parent TB/oracle covers both faults, so a
  pair never mixes fault universes across testbenches;
- too_close: lines within 2 lines of each other are skipped. Adjacent
  Verilog statements can share one oracle effect, so the second fault
  may mask (or ride on) the first -- the joint RED would not prove two
  independent faults;
- dup_anchor: identical anchor text at DIFFERENT lines is skipped.
  This is the mut-pilot-02-class double-occurrence trap: e.g. the
  anchor "recv_state <= 0;" occurs at BOTH line 68 and line 93 of
  pristine picosoc/simpleuart.v (see agent/loop.py prepare_mutant_tree
  docs and tests/test_mutant_fire.py). Any line-anchored path
  (sim/sim.py inject replaces EVERY matching line) would build a
  DOUBLE fault out of one such spec -- the wrong fault distribution.
  The frozen prepare_mutant_tree is line-targeted and would apply the
  pair correctly, but pairing two textually identical anchors
  reintroduces exactly the ambiguity the pinned-line discipline was
  built to escape: the two faults are indistinguishable as text, so a
  later anchored re-fire or attribution pass cannot tell which line
  killed. Skip loudly instead of trusting it.

Joint-RED proof: every surviving pair is built as a combined tree via
the FROZEN agent/loop.py prepare_mutant_tree (imported, never
reimplemented) plus vbackend build + run against the parent oracle.
Single-RED status is NEVER trusted for the pair: masking pairs go
jointly GREEN and are discarded with their oracle lines kept in the
manifest.

Determinism: candidate enumeration is sorted, the draw order is
random.Random(seed).shuffle; same --seed + same board = same manifest.

Usage:
  python3 eval/mine_pairs.py --seed 1234 --count 5
  python3 eval/mine_pairs.py --seed 1234 --count 5 --start-index 6 \\
      --out-dir sim/targets --manifest eval/mine_pairs_manifest.json \\
      --workdir /tmp/minepairs --sim-seed 42
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "agent"), os.path.join(ROOT, "sim"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import loop  # noqa: E402  (frozen; used, not modified)
import vbackend  # noqa: E402

SPEC_DIR = os.path.join(ROOT, "sim", "targets")
DEFAULT_BOARD = os.path.join(ROOT, "eval", "mutant_pilot", "board.json")
RUN_TIMEOUT = 600

SPEC_HEADER = (
    "# SANCTIONED dual-fault pilot spec {pair_id} (mined pair).\n"
    "# Two pinned faults, one run: {rel}:{la} ({opa}) + "
    "{rel}:{lb} ({opb}). In-tree so the loop's in-tree-only\n"
    "# --spec rule is UNCHANGED. Firing: agent/loop.py --spec ONLY.\n"
    "# Combined RED is verified at mine time: if the pair is jointly GREEN\n"
    "# the run fails loudly (not RED), never silently.\n"
    "# Parent TB/oracle: {parent_case}.\n")


# --------------------------------------------------------------------------
# pure pairing (no sim): candidate enumeration, filters, draw order
# --------------------------------------------------------------------------

def load_kept(board_path: str = DEFAULT_BOARD) -> list:
    """Kept-RED singles from the board (never touches runs/ or trees)."""
    with open(board_path, encoding="utf-8") as fh:
        board = json.load(fh)
    kept = board["kept"]
    for entry in kept:
        for req in ("mutant_id", "file", "line", "op", "before", "after",
                    "parent_case", "parent"):
            if req not in entry:
                raise KeyError("board kept entry %r missing %r"
                               % (entry.get("mutant_id"), req))
    return kept


def candidate_pairs(kept: list) -> list:
    """All same-file unordered pairs, deterministic sorted order."""
    by_file: dict = {}
    for entry in kept:
        by_file.setdefault(entry["file"], []).append(entry)
    pairs = []
    for rel in sorted(by_file):
        members = sorted(by_file[rel],
                         key=lambda e: (int(e["line"]), str(e["mutant_id"])))
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.append((members[i], members[j]))
    return pairs


def classify_pair(a: dict, b: dict) -> str | None:
    """None when the pair may be tried, else the rejection reason.

    too_close: |lineA - lineB| <= 2 (fault-m masking risk, and it also
      covers two singles mined from the SAME line, e.g. two ops on one
      wire declaration, whose effects cannot be told apart).
    dup_anchor: identical anchor text at different lines
      (mut-pilot-02-class double-occurrence trap, see module docs).
    cross_parent: same file but different parent_case (latent on this
      board, which is file->parent 1:1 -- a future board could pair two
      same-file mutants covered by different TBs, and one TB would not
      exercise both faults).
    """
    if abs(int(a["line"]) - int(b["line"])) <= 2:
        return "too_close"
    if str(a["before"]).strip() == str(b["before"]).strip():
        return "dup_anchor"
    if str(a.get("parent_case", "")) != str(b.get("parent_case", "")):
        return "cross_parent"
    return None


def draw_order(pairs: list, seed: int) -> list:
    """Deterministic shuffle of the candidate pairs (new list)."""
    rng = random.Random(seed)
    order = list(pairs)
    rng.shuffle(order)
    return order


def plan_pairs(kept: list, seed: int) -> tuple:
    """Filter + order candidates without any sim.

    Returns (accepted_pairs, accounting) where accounting carries
    pairs_tried / discarded_too_close / discarded_dup_anchor /
    discarded_cross_parent.
    """
    pairs = draw_order(candidate_pairs(kept), seed)
    accounting = {"pairs_tried": len(pairs),
                  "discarded_too_close": 0,
                  "discarded_dup_anchor": 0,
                  "discarded_cross_parent": 0}
    accepted = []
    for (a, b) in pairs:
        reason = classify_pair(a, b)
        if reason == "too_close":
            accounting["discarded_too_close"] += 1
        elif reason == "dup_anchor":
            accounting["discarded_dup_anchor"] += 1
        elif reason == "cross_parent":
            accounting["discarded_cross_parent"] += 1
        else:
            accepted.append((a, b))
    return accepted, accounting


# --------------------------------------------------------------------------
# pair case + spec rendering (same schema as mut-pair-01.yaml)
# --------------------------------------------------------------------------

def _quote(text) -> str:
    return '"%s"' % str(text).replace('"', "'")


def _gate_for(rel: str) -> str:
    short = ("simpleuart" if "simpleuart" in rel
             else os.path.splitext(os.path.basename(rel))[0])
    return "dual-fault pilot %s pair" % short


def make_pair_case(pair_id: str, a: dict, b: dict, seed: int) -> dict:
    """Firing case for the pair (faults list + parent TB/oracle fields).

    Both members share one file and one parent by construction, so the
    parent's project/files/top/tb/vflags/oracle rows are the pair's.
    Faults are line-sorted for stable specs.
    """
    parent = a["parent"]
    lo, hi = ((a, b) if int(a["line"]) <= int(b["line"]) else (b, a))
    return {
        "name": pair_id,
        "project": parent["project"],
        "files": list(parent["files"]),
        "top": parent["top"],
        "tb": parent["tb"],
        "vflags": list(parent.get("vflags", [])),
        "gate": _gate_for(a["file"]),
        "oracle_pass": parent["oracle_pass"],
        "oracle_trap": parent.get("oracle_trap", "TRAP:"),
        "mutant_id": pair_id,
        "mutant_seed": int(seed),
        "mutant_op": "pair",
        "faults": [
            {"file": m["file"], "line": int(m["line"]),
             "anchor": str(m["before"]).strip(),
             "bug": str(m["after"]).strip()}
            for m in (lo, hi)
        ],
        "parent_case": a["parent_case"],
    }


def render_pair_spec(pair_id: str, a: dict, b: dict, seed: int) -> str:
    """Spec text in the exact committed mut-pair-01.yaml field order."""
    case = make_pair_case(pair_id, a, b, seed)
    lines = [SPEC_HEADER.format(
        pair_id=pair_id, rel=a["file"],
        la=min(int(a["line"]), int(b["line"])),
        lb=max(int(a["line"]), int(b["line"])),
        opa=a["op"], opb=b["op"], parent_case=a["parent_case"])]
    lines.append("project: %s" % _quote(case["project"]))
    lines.append("files: [%s]" % ", ".join(case["files"]))
    lines.append("top: %s" % _quote(case["top"]))
    lines.append("tb: %s" % _quote(case["tb"]))
    lines.append("vflags: [%s]"
                 % ", ".join(_quote(v) for v in case["vflags"]))
    lines.append("gate: %s" % _quote(case["gate"]))
    lines.append("oracle_pass: %s" % _quote(case["oracle_pass"]))
    lines.append("oracle_trap: %s" % _quote(case["oracle_trap"]))
    lines.append("mutant_id: %s" % _quote(case["mutant_id"]))
    lines.append("mutant_seed: %d" % case["mutant_seed"])
    lines.append("mutant_op: %s" % _quote(case["mutant_op"]))
    lines.append("faults:")
    for fault in case["faults"]:
        lines.append("  - file: %s" % _quote(fault["file"]))
        lines.append("    line: %d" % fault["line"])
        lines.append("    anchor: %s" % _quote(fault["anchor"]))
        lines.append("    bug: %s" % _quote(fault["bug"]))
    lines.append("parent_case: %s" % _quote(case["parent_case"]))
    return "\n".join(lines) + "\n"


def write_pair_spec(pair_id: str, a: dict, b: dict, seed: int,
                    path: str) -> str:
    """Write the spec file; returns the absolute path.

    Refuses to overwrite a pre-existing spec (explicit --start-index
    below the committed max would otherwise silently clobber).
    """
    if os.path.exists(path):
        raise SystemExit("refusing to overwrite existing pair spec "
                         + os.path.abspath(path))
    text = render_pair_spec(pair_id, a, b, seed)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return os.path.abspath(path)


# --------------------------------------------------------------------------
# joint-RED proof (frozen prepare + REAL build/run, never single-RED trust)
# --------------------------------------------------------------------------

def _oracle_lines(log: str) -> list:
    return [ln for ln in log.splitlines()
            if "tohost:" in ln or "TRAP:" in ln or "CYCLES=" in ln]


def _parse_cycles(log: str):
    match = re.search(r"CYCLES=(\d+)", log)
    return int(match.group(1)) if match else None


def verify_pair(case: dict, parent: dict, base: str, dest: str,
                sim_seed: int = 42) -> tuple:
    """Build the COMBINED-fault tree and judge it against the oracle.

    Returns (status, record). Statuses: joint_red (proven RED, oracle
    lines attached), joint_green (pair masks: oracle PASS present --
    discard loudly), build_break / other / prep_fail (discard loudly
    with the reason; never silent, never trusted as RED).

    joint_red keys on the hardcoded "tohost: FAIL" marker (conservative:
    a future parent whose failure mode is TRAP-only with no FAIL line
    falls to loud "other" discard, never to a false keep).
    """
    # vbackend.build names its obj dir from case["name"]; board parent
    # rows carry no name (draw_board pops it), so stamp the pair id.
    parent = dict(parent)
    parent.setdefault("name", case.get("name", "mut-pair"))
    try:
        loop.prepare_mutant_tree(case, base, dest, force=True)
    except Exception as exc:  # noqa: BLE001 (recorded, never hidden)
        return ("prep_fail", {
            "reason": "mutant prep failed loudly: %r" % (exc,)})
    try:
        binary = vbackend.build(dest, parent)
    except Exception as exc:  # noqa: BLE001 (verilator errors are text)
        return ("build_break", {
            "reason": "verilator build broke loudly: %s"
                      % str(exc)[-2000:]})
    try:
        rc, log = vbackend.sh([str(binary), "+seed=%d" % int(sim_seed)],
                              cwd=dest, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ("other", {
            "reason": "run timeout after %ds (no oracle verdict observed)"
                      % RUN_TIMEOUT})
    oracle_lines = _oracle_lines(log)
    cycles = _parse_cycles(log)
    if parent["oracle_pass"] in log:
        return ("joint_green", {
            "reason": ("pair jointly GREEN: oracle %r present in the "
                       "combined run although both singles were kept-RED "
                       "alone (fault masking); discarding, never emitting"
                       % parent["oracle_pass"]),
            "oracle_lines": oracle_lines, "cycles": cycles, "rc": rc})
    if "tohost: FAIL" in log:
        return ("joint_red", {"oracle_lines": oracle_lines,
                              "cycles": cycles, "rc": rc})
    return ("other", {
        "reason": "no oracle verdict and no FAIL marker (rc=%d)" % rc,
        "log_tail": log[-2000:], "oracle_lines": oracle_lines})


# --------------------------------------------------------------------------
# campaign driver
# --------------------------------------------------------------------------

def next_pair_index(out_dir: str) -> int:
    """One past the max mut-pair-NN index present (1 when none)."""
    best = 0
    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            match = re.fullmatch(r"mut-pair-(\d+)\.yaml", name)
            if match:
                best = max(best, int(match.group(1)))
    return best + 1


def mine(board_path: str = DEFAULT_BOARD, seed: int = 1234,
         count: int = 5, out_dir: str = SPEC_DIR,
         manifest_path: str | None = None,
         workdir: str = "/tmp/minepairs", sim_seed: int = 42,
         start_index: int | None = None,
         base_tree: str | None = None) -> dict:
    """Draw, filter, jointly-verify, and emit up to `count` pair specs.

    Returns the manifest dict (also written as JSON). Pair ids are dense
    (mut-pair-NN increments only on kept RED) starting at start_index
    (default: one past the max committed index, so existing specs are
    never overwritten).
    """
    with open(board_path, encoding="utf-8") as fh:
        board = json.load(fh)
    kept = load_kept(board_path)
    accepted, accounting = plan_pairs(kept, seed)
    base = base_tree or board["base"]["tree"]
    if start_index is None:
        start_index = next_pair_index(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(workdir, exist_ok=True)

    manifest = {
        "tool": "eval/mine_pairs.py",
        "board": os.path.relpath(os.path.abspath(board_path), ROOT),
        "draw_seed": int(seed),
        "sim_seed": int(sim_seed),
        "pairs_tried": accounting["pairs_tried"],
        "kept_red": 0,
        "discarded_joint_green": 0,
        "discarded_too_close": accounting["discarded_too_close"],
        "discarded_dup_anchor": accounting["discarded_dup_anchor"],
        "discarded_cross_parent": accounting["discarded_cross_parent"],
        "discarded_build_break": 0,
        "discarded_other": 0,
        "discarded_prep_fail": 0,
        "pairs": [],
    }
    index = int(start_index)
    for (a, b) in accepted:
        if manifest["kept_red"] >= int(count):
            manifest.setdefault("truncated",
                                "stopped at count=%d" % int(count))
            break
        pair_id = "mut-pair-%02d" % index
        case = make_pair_case(pair_id, a, b, seed)
        dest = os.path.join(workdir, pair_id)
        # vbackend.build names its obj dir from case["name"]; the board
        # parent rows carry no name (draw_board pops it), so stamp it.
        parent = dict(a["parent"])
        parent["name"] = pair_id
        status, rec = verify_pair(case, parent, base, dest,
                                  sim_seed=int(sim_seed))
        entry = {"pair_id": pair_id,
                 "members": [a["mutant_id"], b["mutant_id"]],
                 "member_ops": [a["op"], b["op"]],
                 "file": a["file"],
                 "lines": [int(a["line"]), int(b["line"])],
                 "status": status}
        if status == "joint_red":
            spec_path = os.path.join(out_dir, pair_id + ".yaml")
            write_pair_spec(pair_id, a, b, seed, spec_path)
            entry["spec"] = os.path.relpath(os.path.abspath(spec_path),
                                            ROOT)
            entry["cycles"] = rec.get("cycles")
            entry["oracle_lines"] = rec.get("oracle_lines")
            manifest["kept_red"] += 1
            index += 1
            print("KEPT %s %s:%s+%s %s/%s cycles=%s"
                  % (pair_id, a["file"], a["line"], b["line"],
                     a["mutant_id"], b["mutant_id"], rec.get("cycles")),
                  flush=True)
        elif status == "joint_green":
            entry["reason"] = rec.get("reason")
            entry["oracle_lines"] = rec.get("oracle_lines")
            entry["cycles"] = rec.get("cycles")
            manifest["discarded_joint_green"] += 1
            shutil.rmtree(dest, ignore_errors=True)
            print("DISCARD %s jointly GREEN (%s+%s): %s"
                  % (pair_id, a["mutant_id"], b["mutant_id"],
                     rec.get("reason")), flush=True)
        elif status == "build_break":
            entry["reason"] = rec.get("reason")
            manifest["discarded_build_break"] += 1
            shutil.rmtree(dest, ignore_errors=True)
            print("DISCARD %s build_break: %s" % (pair_id, entry["reason"]),
                  flush=True)
        elif status == "prep_fail":
            entry["reason"] = rec.get("reason")
            manifest["discarded_prep_fail"] += 1
            shutil.rmtree(dest, ignore_errors=True)
            print("DISCARD %s prep_fail: %s" % (pair_id, entry["reason"]),
                  flush=True)
        else:
            entry.update(rec)
            manifest["discarded_other"] += 1
            shutil.rmtree(dest, ignore_errors=True)
            print("DISCARD %s other: %s" % (pair_id, entry.get("reason")),
                  flush=True)
        manifest["pairs"].append(entry)
    manifest["complete"] = manifest["kept_red"] >= int(count)
    if manifest_path is None:
        manifest_path = os.path.join(ROOT, "eval",
                                     "mine_pairs_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print("pairs_tried=%d kept_red=%d joint_green=%d too_close=%d "
          "dup_anchor=%d cross_parent=%d build_break=%d other=%d "
          "prep_fail=%d manifest=%s"
          % (manifest["pairs_tried"], manifest["kept_red"],
             manifest["discarded_joint_green"],
             manifest["discarded_too_close"],
             manifest["discarded_dup_anchor"],
             manifest["discarded_cross_parent"],
             manifest["discarded_build_break"],
             manifest["discarded_other"],
             manifest["discarded_prep_fail"], manifest_path),
          flush=True)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", default=DEFAULT_BOARD)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--out-dir", default=SPEC_DIR)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--workdir", default="/tmp/minepairs")
    parser.add_argument("--sim-seed", type=int, default=42)
    parser.add_argument("--start-index", type=int, default=None)
    parser.add_argument("--base-tree", default=None)
    args = parser.parse_args(argv)
    manifest = mine(board_path=args.board, seed=args.seed,
                    count=args.count, out_dir=args.out_dir,
                    manifest_path=args.manifest, workdir=args.workdir,
                    sim_seed=args.sim_seed, start_index=args.start_index,
                    base_tree=args.base_tree)
    return 0 if manifest["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
