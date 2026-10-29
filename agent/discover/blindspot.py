#!/usr/bin/env python3
"""Task A2: TB blind-spot hunter (plan item 2, Wave A FIND BUGS).

Given a list of kept single mutants (file/line/before/after -- the same
shape as `eval/mutant_pilot/board.json` kept entries) and a case TB,
injects each mutant into a scratch tree, runs the EXISTING TB
unmodified, and reports per-mutant killed/survived.

  kill = the case `oracle_pass` marker is ABSENT from the sim log.
  survived = the marker is present (the TB still passes: blind spot).

Interfaces:
  hunt_blindspots(pristine_tree, case, mutants, build_fn=None,
                  run_fn=None, workdir=None, seed=42) -> dict

Honesty contract (mirrors the fuzzer: never invent a verdict):
  - Empty mutant list raises ValueError. Zero mutants produce zero
    signal, so none is invented.
  - The TB is NEVER modified: mutants targeting the TB file are refused
    (ValueError), the TB file hash is snapshotted before the hunt and
    re-verified after every mutant (RuntimeError on mismatch).
  - The pristine tree is NEVER written: every mutant is applied inside
    a scratch copy; the post-apply walk asserts every non-pinned line
    of every file is byte-identical to pristine.
  - Any build/run error, empty sim log, or anchor mismatch raises
    loudly -- NEVER recorded as a kill or a survival.
  - Default build/run reuse sim/vbackend.py primitives
    (vbackend.build + vbackend.sh); injectable fns exist for tests.
"""

import hashlib
import os
import shutil
import sys
import tempfile

_DISCOVER_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_DISCOVER_DIR)
_ROOT = os.path.dirname(_AGENT_DIR)
sys.path.insert(0, os.path.join(_ROOT, "sim"))
sys.path.insert(0, _ROOT)

import vbackend  # noqa: E402

SIM_TIMEOUT_S = 300


def tb_path_for(case):
    """Absolute path of the case's EXISTING testbench (never written)."""
    try:
        tb = case["tb"]
    except (KeyError, TypeError):
        raise KeyError("case is missing required key 'tb'")
    if not str(tb).strip():
        raise ValueError("case tb is blank; refusing to run without a TB")
    path = os.path.join(os.path.dirname(os.path.abspath(vbackend.__file__)),
                        "tb", str(tb))
    if not os.path.isfile(path):
        raise FileNotFoundError("missing testbench: %s" % path)
    return path


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _mutant_id(mutant, idx):
    mid = mutant.get("mutant_id")
    if mid is not None and str(mid).strip():
        return str(mid)
    return "%s:%s" % (mutant.get("file", "?"), mutant.get("line", idx))


def _validate_mutant(mutant, case_tb):
    """Loud checks BEFORE anything is copied or built."""
    if not isinstance(mutant, dict):
        raise ValueError("mutant must be a dict with "
                         "file/line/before/after, got %r" % (mutant,))
    for key in ("file", "line", "before", "after"):
        if key not in mutant:
            raise KeyError("mutant %r is missing required key %r"
                           % (mutant, key))
    rel = str(mutant["file"])
    if not rel.strip():
        raise ValueError("mutant file is blank: %r" % (mutant,))
    if os.path.isabs(rel):
        raise ValueError("mutant file must be tree-relative, got %r" % rel)
    # The TB is never a mutation target: refuse loudly.
    if (os.path.basename(rel) == os.path.basename(str(case_tb))
            or rel == str(case_tb)
            or rel.endswith("/" + str(case_tb).lstrip("/"))):
        raise ValueError(
            "refusing mutant targeting the testbench %r (mutant %r): "
            "the hunt runs the EXISTING TB unmodified, never mutates it"
            % (case_tb, mutant))
    try:
        lineno = int(mutant["line"])
    except (TypeError, ValueError):
        raise ValueError("mutant line must be an integer, got %r"
                         % (mutant.get("line"),))
    if lineno < 1:
        raise ValueError("mutant line out of range: %d" % lineno)
    before = str(mutant["before"])
    after = str(mutant["after"])
    if not before.strip() or not after.strip():
        raise ValueError("mutant needs non-blank before+after, got %r"
                         % (mutant,))
    if before.strip() == after.strip():
        raise ValueError("mutant before == after (no fault to apply): %r"
                         % (mutant,))
    return rel, lineno, before.strip(), after.strip()


def _copy_pristine(pristine_tree, dest):
    """Copy pristine_tree -> dest. The pristine tree is only read."""
    if os.path.exists(dest):
        raise RuntimeError("mutant scratch exists (refusing): %s" % dest)

    def _ignore(_dir, names):
        return [n for n in names
                if n in (".git", "__pycache__")
                or n.startswith("obj_afterglow_")
                or n.endswith(".orig") or n.endswith(".rej")]

    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)
    shutil.copytree(pristine_tree, dest, ignore=_ignore)


def _apply_mutant(scratch, pristine_tree, rel, lineno, before, after):
    """Apply one pinned-line mutant inside the scratch tree only.

    Verifies the pinned line's stripped text equals `before` (wrong
    revision refuses loudly) and that EVERY other line of EVERY file
    is byte-identical to pristine (no double-fault, no leakage).
    """
    path = os.path.join(scratch, rel)
    if not os.path.isfile(path):
        raise FileNotFoundError("mutant file missing in tree: %s" % rel)
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    # Preserve a trailing newline exactly like the pristine file had.
    with open(path, "rb") as fh:
        raw = fh.read()
    trailing_nl = raw.endswith(b"\n")
    if lineno > len(lines):
        raise RuntimeError("mutant line %d out of range (%d lines in %s)"
                           % (lineno, len(lines), rel))
    cur = lines[lineno - 1]
    if cur.strip() != before:
        raise RuntimeError(
            "pristine %s:%d is %r, not mutant before %r "
            "(wrong pristine revision? refusing to mis-fire)"
            % (rel, lineno, cur.strip(), before))
    indent = cur[:len(cur) - len(cur.lstrip())]
    lines[lineno - 1] = "%s%s" % (indent, after)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + ("\n" if trailing_nl else ""))
    # Verify: EXACTLY the pinned line differs, everything else identical.
    for dirpath, dirnames, filenames in os.walk(scratch):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in (".git", "__pycache__")
            and not d.startswith("obj_afterglow_"))
        for fn in filenames:
            if fn.endswith(".orig") or fn.endswith(".rej"):
                continue
            full = os.path.join(dirpath, fn)
            tree_rel = os.path.relpath(full, scratch)
            with open(full, "rb") as fh:
                got = fh.read()
            pristine_peer = os.path.join(pristine_tree, tree_rel)
            if not os.path.isfile(pristine_peer):
                # Build outputs created inside scratch (obj dirs are
                # ignored above, but be strict about sources).
                raise RuntimeError("scratch holds a file with no pristine "
                                   "peer (leakage?): %s" % tree_rel)
            with open(pristine_peer, "rb") as fh:
                want = fh.read()
            if tree_rel != rel:
                if got != want:
                    raise RuntimeError(
                        "scratch file differs from pristine outside the "
                        "pinned mutant: %s" % tree_rel)
                continue
            got_lines = got.splitlines()
            want_lines = want.splitlines()
            if len(got_lines) != len(want_lines):
                raise RuntimeError("mutant apply changed line count in %s"
                                   % tree_rel)
            diffs = [i for i, (a, b) in
                     enumerate(zip(want_lines, got_lines)) if a != b]
            if diffs != [lineno - 1]:
                raise RuntimeError(
                    "mutant apply left unexpected diff in %s "
                    "(want exactly line %d)" % (tree_rel, lineno))


def _default_build(tree, case):
    return vbackend.build(tree, case)


def _default_run(binary, seed, mutant):
    """Run the ALREADY-BUILT sim binary; verdict comes from the log only.

    Exit code is deliberately ignored (afterglow_core.types contract):
    the oracle marker decides. A timeout raises (never a verdict).
    """
    del mutant  # reserved: future TBs consuming per-mutant +args
    parent = os.path.dirname(os.path.abspath(str(binary)))
    if os.path.basename(parent).startswith("obj_afterglow_"):
        cwd = os.path.dirname(parent)
    else:
        cwd = parent
    rc, log = vbackend.sh([str(binary), "+seed=%d" % int(seed)],
                          cwd=cwd, timeout=SIM_TIMEOUT_S)
    del rc  # exit code never decides the verdict; the oracle marker does
    return log


def hunt_blindspots(pristine_tree, case, mutants,
                    build_fn=None, run_fn=None, workdir=None, seed=42):
    """Inject each mutant into a scratch tree, run the EXISTING TB.

    pristine_tree: clean RTL tree (read-only; never written).
    case: vbackend case dict (or case-name string loaded via
      vbackend.load_case) carrying top/tb/oracle_pass.
    mutants: non-empty list of {file, line, before, after}
      (+ optional mutant_id), board.json kept-entry shape.
    build_fn(tree, case) -> binary path (default vbackend.build).
    run_fn(binary, seed, mutant) -> sim log text (default: run the
      binary with +seed via vbackend.sh).
    workdir: scratch parent (a temp dir when omitted; kept on disk
      for inspection).
    seed: sim `+seed=` value for every mutant run.

    Returns {"case", "tb", "oracle_pass", "seed", "n", "killed": [...],
      "survived": [...], "kill_rate", "results": [{mutant_id, file,
      line, verdict, oracle_present}]}.

    Errors at ANY stage raise loudly and are never recorded as kills
    or survivals (no partial report is returned on failure).
    """
    mutants = list(mutants)
    if not mutants:
        raise ValueError(
            "hunt_blindspots refused empty mutants: zero mutants "
            "produce zero signal; refusing to invent a 0/0 report.")
    if isinstance(case, str):
        case = vbackend.load_case(case)
    try:
        oracle = case["oracle_pass"]
    except (KeyError, TypeError):
        raise KeyError("case is missing required key 'oracle_pass'")
    if not str(oracle).strip():
        raise ValueError("case oracle_pass is blank; the kill verdict "
                         "would be meaningless -- refusing to run.")
    oracle = str(oracle)
    case_tb = case.get("tb")
    tb_path = tb_path_for(case)
    tb_hash_before = sha256_file(tb_path)
    if not os.path.isdir(pristine_tree):
        raise ValueError("pristine tree absent: %s" % pristine_tree)
    build = build_fn or _default_build
    run = run_fn or _default_run

    def _check_tb_untouched(stage):
        if sha256_file(tb_path) != tb_hash_before:
            raise RuntimeError(
                "testbench MODIFIED during the hunt (%s): %s -- "
                "refusing to report; restore the TB and re-run"
                % (stage, tb_path))

    root = os.path.abspath(workdir or tempfile.mkdtemp(prefix="blindspot-"))
    pris = os.path.abspath(pristine_tree)
    if root == pris or root.startswith(pris + os.sep):
        raise ValueError(
            "workdir must not live inside pristine_tree (%s): the "
            "scratch copy would recurse into itself" % pristine_tree)
    os.makedirs(root, exist_ok=True)
    results = []
    for idx, mutant in enumerate(mutants):
        mid = _mutant_id(mutant, idx)
        rel, lineno, before, after = _validate_mutant(mutant, case_tb)
        scratch = os.path.join(root, "mut-%02d" % idx)
        _copy_pristine(pristine_tree, scratch)
        _apply_mutant(scratch, pristine_tree, rel, lineno, before, after)
        _check_tb_untouched("before mutant %s run" % mid)
        binary = build(scratch, case)  # raises loudly on build failure
        log = run(binary, int(seed), mutant)  # raises loudly on sim error
        if not isinstance(log, str):
            raise TypeError("run_fn must return sim log text, got %r"
                            % type(log))
        if not log.strip():
            raise RuntimeError(
                "sim produced no log output for mutant %s: cannot judge "
                "kill vs survival without an oracle signal -- refusing "
                "to classify" % mid)
        oracle_present = oracle in log
        results.append({
            "mutant_id": mid,
            "file": rel,
            "line": lineno,
            "verdict": "survived" if oracle_present else "killed",
            "oracle_present": oracle_present,
        })
        _check_tb_untouched("after mutant %s" % mid)
    _check_tb_untouched("end of hunt")
    killed = [r["mutant_id"] for r in results if not r["oracle_present"]]
    survived = [r["mutant_id"] for r in results if r["oracle_present"]]
    return {
        "case": case.get("name", "?"),
        "tb": str(case_tb),
        "oracle_pass": oracle,
        "seed": int(seed),
        "n": len(results),
        "killed": killed,
        "survived": survived,
        "kill_rate": len(killed) / len(results),
        "results": results,
    }
