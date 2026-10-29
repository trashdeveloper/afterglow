"""Task C3: Equivalence check (plan item 23, Wave C FIX BETTER).

After GREEN, runs the fixed tree vs the pristine tree on the same
seeded random stimulus and demands identical traces: an equivalent
pair passes, a behavior-changed pair is flagged. Sim-only (no API,
no keys).

Interface::

  generate_stimulus(master_seed, n=8) -> list[int]
  checksum_log(log) -> sha256 hex (vbackend.run_case convention)
  check_equivalence(tree_a, tree_b, case, master_seed=..., n=...,
                    build_fn=None, run_fn=None)
    -> {"equivalent": bool, "evidence": {...}}

``check_equivalence`` builds each tree exactly ONCE via the existing
sim/build path (``vbackend.build`` by default -- no forked build
machinery), runs each binary once per stimulus seed (``+seed=<s>``
via ``vbackend.sh`` by default), and compares per-seed checksums plus
byte-exact traces. Returns ``equivalent True`` iff EVERY seed matches
on both sides; any mismatch yields ``equivalent False`` with the
divergent seeds + first-differing lines pinned in ``evidence``.

Honesty contract (mirrors fuzzer/blindspot/seeddiff/timing):
- Deterministic stimulus: ``random.Random(master_seed)``; the same
  (master_seed, n) reproduces the exact same seed list. ``n < 1``
  raises ValueError (nothing to compare).
- Either side fails to build or run, a log is non-string, or a log is
  empty/whitespace-only -> raises loudly (REFUSES the verdict; never
  fabricates equivalent True/False from errors).
- Checksum equality alone is not agreement: the full trace must also
  match -- EXCEPT Verilator walltime/speed tokens on "- " chatter
  lines, which vary run-to-run for identical seeds (proven in
  seeddiff) and are normalized before comparison. Normalization is
  pinned by test, applies to chatter lines only, and raw traces are
  always preserved verbatim in evidence.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``hashlib``/``random``/``re``) plus the existing
sim/vbackend.py build/run primitives; no API, no keys, no network.
"""

import hashlib
import os
import random
import re
import sys

_REPAIR_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_REPAIR_DIR)
_ROOT = os.path.dirname(_AGENT_DIR)
sys.path.insert(0, os.path.join(_ROOT, "sim"))
sys.path.insert(0, _ROOT)

import vbackend  # noqa: E402

SIM_TIMEOUT_S = 300

DEFAULT_N = 8
STIMULUS_BITS = 31  # stimulus seeds are 31-bit non-negative ints

# Verilator $finish chatter carries host timing noise
# ("- Verilator: $finish at 20us; walltime 0.001 s; speed 31.422 ms/s")
# that varies run-to-run for the SAME seed (proven in seeddiff: seed 42
# twice -> speed 31.422 vs 31.359 ms/s). It is measurement noise, not
# DUT signal, so trace comparison normalizes these tokens on "- "
# chatter lines only. DUT lines compare byte-exactly; raw traces are
# preserved unmodified in every record. Any OTHER chatter difference
# (seed echo, model strings, ...) is still flagged.
_WALLTIME_RE = re.compile(r"walltime [\d.]+ s; speed [\d.]+[^ ]*")


def _normalize_lines(lines):
    """Normalize known-benign timing noise on "- " chatter lines."""
    out = []
    for ln in lines:
        if ln.startswith("- "):
            ln = _WALLTIME_RE.sub("walltime <t> s; speed <t>", ln)
        out.append(ln)
    return out


def generate_stimulus(master_seed, n=DEFAULT_N):
    """Deterministic stimulus seed list: same inputs, byte-identical list.

    ``master_seed`` must be an int (bool rejected: not a seed);
    ``n`` must be an int >= 1. Returns ``n`` 31-bit ints drawn from
    ``random.Random(master_seed)``.
    """
    if isinstance(master_seed, bool) or not isinstance(master_seed, int):
        raise TypeError(
            "generate_stimulus needs an int master_seed, got %r: "
            "refusing to invent stimulus from a non-seed"
            % (master_seed,))
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError(
            "generate_stimulus needs an int n, got %r" % (n,))
    if n < 1:
        raise ValueError(
            "generate_stimulus needs n >= 1, got %d: nothing to "
            "compare on zero stimuli -- refusing to report" % n)
    rng = random.Random(master_seed)
    return [rng.getrandbits(STIMULUS_BITS) for _ in range(n)]


def checksum_log(log):
    """Deterministic checksum of a sim log (vbackend.run_case convention).

    Verilator chatter lines (starting with "- ") are excluded; every
    other line (oracle markers, CYCLES=, trace rows) feeds the sha256.
    """
    if not isinstance(log, str):
        raise TypeError("log must be text, got %r" % type(log))
    det = [ln for ln in log.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def _default_build(tree, case):
    return vbackend.build(tree, case)


def _default_run(binary, seed):
    """Run the ALREADY-BUILT sim binary with +seed; return log text.

    A nonzero exit means the sim never completed its trace (crash,
    abort): the log is not a comparable trace, so refuse loudly
    instead of blessing two matching crash fragments "equivalent".
    A timeout raises via vbackend.sh (never agreement).
    """
    rc, log = vbackend.sh([str(binary), "+seed=%d" % int(seed)],
                          cwd=str(_tree_of(binary)), timeout=SIM_TIMEOUT_S)
    if rc:
        raise RuntimeError(
            "sim binary exited %d for binary %r seed %r: trace "
            "incomplete, refusing equivalence verdict:\n%s"
            % (rc, binary, seed, log))
    return log


def _tree_of(binary):
    """Cwd for running a vbackend-built binary: the tree holding obj dir."""
    parent = os.path.dirname(os.path.abspath(str(binary)))
    if os.path.basename(parent).startswith("obj_afterglow_"):
        return os.path.dirname(parent)
    return parent


def _require_tree(tree, side):
    if isinstance(tree, os.PathLike):
        tree = os.fspath(tree)
    if not isinstance(tree, (str, bytes)) or not str(tree).strip():
        raise ValueError(
            "check_equivalence needs a non-blank %s tree path, got %r: "
            "refusing to judge an unnamed tree" % (side, tree))
    return str(tree)


def _first_diff_lines(lines_a, lines_b, raw_a=None, raw_b=None):
    """First differing line (1-based) between two line lists, or None.

    Comparison runs on the (possibly normalized) lists; the reported
    tree_a_line/tree_b_line come from the raw lists at the same index
    so the artifact is quoted verbatim. None marks a missing line
    (one log is a prefix of the other).
    """
    raw_a = raw_a if raw_a is not None else lines_a
    raw_b = raw_b if raw_b is not None else lines_b
    for idx, (la, lb) in enumerate(zip(lines_a, lines_b), start=1):
        if la != lb:
            return {"line": idx,
                    "tree_a_line": raw_a[idx - 1],
                    "tree_b_line": raw_b[idx - 1]}
    if len(lines_a) != len(lines_b):
        n = min(len(lines_a), len(lines_b)) + 1
        a_extra = raw_a[n - 1] if len(raw_a) >= n else None
        b_extra = raw_b[n - 1] if len(raw_b) >= n else None
        return {"line": n, "tree_a_line": a_extra, "tree_b_line": b_extra}
    return None


def _require_log(log, binary, seed):
    """Validate one sim log; raise loudly instead of judging garbage."""
    if not isinstance(log, str):
        raise TypeError(
            "run_fn must return sim log text, got %r for binary %r "
            "seed %r" % (type(log), binary, seed))
    if not log.strip():
        raise RuntimeError(
            "sim produced no log output for binary %r seed %r: cannot "
            "judge equivalence without traces -- refusing to report"
            % (binary, seed))
    return log


def check_equivalence(tree_a, tree_b, case, master_seed=1234, n=DEFAULT_N,
                       build_fn=None, run_fn=None):
    """Build both trees once, run both on the same stimulus, diff traces.

    tree_a/tree_b: RTL tree paths (non-blank strings). case: vbackend
    case dict (or case-name string loaded via vbackend.load_case).
    master_seed/n: deterministic stimulus (see generate_stimulus).
    build_fn(tree, case) -> binary path (default vbackend.build).
    run_fn(binary, seed) -> sim log text (default: run the binary with
    +seed via vbackend.sh).

    Returns {"equivalent": bool, "evidence": {"case", "master_seed",
    "seeds", "n", "records": [{seed, checksum_a, checksum_b,
    "trace_a", "trace_b"}], "divergences": [{seed, checksum_match,
    "trace_match", "kind", "checksum_a", "checksum_b", "first_diff"}]}}
    where kind is "checksum" (checksums differ -- traces necessarily
    differ too) or "trace" (checksums match but traces differ).
    Agreeing seeds are never listed. equivalent is True iff
    divergences is empty.

    Every failure mode raises loudly; a verdict is returned ONLY for
    completed runs on both sides (never a fake verdict over errors).
    """
    tree_a = _require_tree(tree_a, "fixed (tree_a)")
    tree_b = _require_tree(tree_b, "pristine (tree_b)")
    seeds = generate_stimulus(master_seed, n)  # raises loudly on bad input
    if isinstance(case, str):
        case = vbackend.load_case(case)
    if not isinstance(case, dict):
        raise ValueError("case must be a vbackend case dict, got %r"
                         % (case,))
    build = build_fn or _default_build
    run = run_fn or _default_run
    # Build each side exactly ONCE; every build error propagates loudly.
    binary_a = build(tree_a, case)
    binary_b = build(tree_b, case)
    records = []
    for seed in seeds:
        log_a = _require_log(run(binary_a, seed), binary_a, seed)
        log_b = _require_log(run(binary_b, seed), binary_b, seed)
        records.append({
            "seed": seed,
            "checksum_a": checksum_log(log_a),
            "checksum_b": checksum_log(log_b),
            "trace_a": log_a,
            "trace_b": log_b,
        })
    divergences = []
    for rec in records:
        csum_match = rec["checksum_a"] == rec["checksum_b"]
        raw_a = rec["trace_a"].splitlines()
        raw_b = rec["trace_b"].splitlines()
        fd = None
        if not (csum_match and rec["trace_a"] == rec["trace_b"]):
            fd = _first_diff_lines(
                _normalize_lines(raw_a), _normalize_lines(raw_b),
                raw_a, raw_b)
        trace_match = fd is None
        if csum_match and trace_match:
            continue  # agreeing seed stays silent
        divergences.append({
            "seed": rec["seed"],
            "checksum_match": csum_match,
            "trace_match": trace_match,
            "kind": "checksum" if not csum_match else "trace",
            "checksum_a": rec["checksum_a"],
            "checksum_b": rec["checksum_b"],
            "first_diff": fd,
        })
    return {
        "equivalent": not divergences,
        "evidence": {
            "case": case.get("name", "?"),
            "master_seed": master_seed,
            "seeds": seeds,
            "n": len(seeds),
            "records": records,
            "divergences": divergences,
        },
    }
