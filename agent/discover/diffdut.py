#!/usr/bin/env python3
"""Task E1: Differential DUT (plan item 51, Wave E FIND BUGS).

Runs two RTL trees (e.g. two pristine picorv32 checkouts, or pristine
vs tampered) on the IDENTICAL NOP stimulus, then byte-compares tohost
+ trace: identical runs report match True; any divergence reports
match False with the first-differing line pinned.

Interfaces:
  run_pair(tree_a, tree_b, stimulus, case=None, build_fn=None,
           run_fn=None)
    -> {"match", "stimulus", "case", "tree_a", "tree_b",
        "tohost_a", "tohost_b", "trace_a", "trace_b",
        "checksum_a", "checksum_b", "divergence"}
  divergence_report(result) -> str (pinned format; names the
    first-differing line on divergence)

Build/run path (mirrors seeddiff/equiv: no forked machinery):
  - Each tree is built exactly ONCE via build_fn (default
    vbackend.build). Callers obtain pristine trees via
    vbackend.ensure_tree (never inside the timed comparison).
  - Each binary runs once with +seed=<stimulus> via run_fn (default:
    vbackend.sh sh-style run; a nonzero exit means the trace is
    incomplete, so it raises instead of judging a crash fragment).
  - stimulus is an int seed (bool rejected); the NOP program is the
    case TB's default program, identical on both sides by construction.

Honesty contract (mirrors seeddiff/equiv):
  - Blank tree paths, non-int stimulus, n/a case, build/run errors,
    non-string logs, or empty logs raise loudly -- NEVER reported as
    match True/False from errors.
  - Checksum equality alone is not agreement: the full trace must also
    match -- EXCEPT Verilator walltime/speed tokens on "- " chatter
    lines, which vary run-to-run for identical seeds (proven in
    seeddiff) and are normalized before comparison. Normalization is
    pinned by test, applies to chatter lines only, and raw traces are
    always preserved verbatim in the result.
  - divergence kind priority: "tohost" (tohost lines differ -- the
    verdict-relevant signal) > "checksum" > "trace".

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, writes
no files (returns dicts/strings only), and no caller is wired.

Pure stdlib (hashlib/re) plus the existing sim/vbackend.py build/run
primitives; no API, no keys, no network.
"""

import hashlib
import os
import re
import sys

_DISCOVER_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_DISCOVER_DIR)
_ROOT = os.path.dirname(_AGENT_DIR)
sys.path.insert(0, os.path.join(_ROOT, "sim"))
sys.path.insert(0, _ROOT)

import vbackend  # noqa: E402

SIM_TIMEOUT_S = 300

DEFAULT_CASE = "picorv32-fetch-misalign"

# Verilator $finish chatter carries host timing noise
# ("- Verilator: $finish at 20us; walltime 0.001 s; speed 31.422 ms/s")
# that varies run-to-run for the SAME seed (proven in seeddiff: seed 42
# twice -> speed 31.422 vs 31.359 ms/s). It is measurement noise, not
# DUT signal, so trace comparison normalizes these tokens on "- "
# chatter lines only. DUT lines compare byte-exactly; raw traces are
# preserved unmodified in every result. Any OTHER chatter difference
# (seed echo, model strings, ...) is still flagged.
_WALLTIME_RE = re.compile(r"walltime [\d.]+ s; speed [\d.]+[^ ]*")

__all__ = ["run_pair", "divergence_report", "checksum_log"]


def _normalize_lines(lines):
    """Normalize known-benign timing noise on "- " chatter lines."""
    out = []
    for ln in lines:
        if ln.startswith("- "):
            ln = _WALLTIME_RE.sub("walltime <t> s; speed <t>", ln)
        out.append(ln)
    return out


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
    instead of blessing two matching crash fragments as a match.
    A timeout raises via vbackend.sh (never agreement).
    """
    rc, log = vbackend.sh([str(binary), "+seed=%d" % int(seed)],
                          cwd=str(_tree_of(binary)), timeout=SIM_TIMEOUT_S)
    if rc:
        raise RuntimeError(
            "sim binary exited %d for binary %r seed %r: trace "
            "incomplete, refusing differential verdict:\n%s"
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
            "run_pair needs a non-blank %s tree path, got %r: "
            "refusing to judge an unnamed tree" % (side, tree))
    return str(tree)


def _require_stimulus(stimulus):
    if isinstance(stimulus, bool) or not isinstance(stimulus, int):
        raise TypeError(
            "stimulus must be an int NOP-run seed, got %r: refusing "
            "to invent stimulus from a non-seed" % (stimulus,))
    return int(stimulus)


def _require_log(log, tree, seed):
    """Validate one sim log; raise loudly instead of judging garbage."""
    if not isinstance(log, str):
        raise TypeError(
            "run_fn must return sim log text, got %r for tree %r "
            "seed %r" % (type(log), tree, seed))
    if not log.strip():
        raise RuntimeError(
            "sim produced no log output for tree %r seed %r: cannot "
            "judge agreement without traces -- refusing to report"
            % (tree, seed))
    return log


def _extract_tohost(log):
    """First tohost verdict line of a sim log, or None if absent."""
    for ln in log.splitlines():
        if "tohost:" in ln:
            return ln.strip()
    return None


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


def run_pair(tree_a, tree_b, stimulus, case=None, build_fn=None,
             run_fn=None):
    """Build both trees once, run both on the same stimulus, diff traces.

    tree_a/tree_b: RTL tree paths (non-blank strings; pristine trees
      come from vbackend.ensure_tree). stimulus: int NOP-run seed,
      passed as +seed to both binaries. case: vbackend case dict (or
      case-name string loaded via vbackend.load_case; default
      "picorv32-fetch-misalign"). build_fn(tree, case) -> binary path
      (default vbackend.build). run_fn(binary, seed) -> sim log text
      (default: sh-style run of the binary with +seed via vbackend.sh).

    Returns {"match", "stimulus", "case", "tree_a", "tree_b",
      "tohost_a", "tohost_b", "trace_a", "trace_b", "checksum_a",
      "checksum_b", "divergence"} where divergence is None on match,
      else {"tohost_match", "checksum_match", "trace_match", "kind",
      "first_diff"} with kind "tohost"|"checksum"|"trace" and
      first_diff {"line", "tree_a_line", "tree_b_line"}.

    Every failure mode raises loudly; a verdict is returned ONLY for
    completed runs on both sides (never a fake verdict over errors).
    """
    tree_a = _require_tree(tree_a, "tree_a")
    tree_b = _require_tree(tree_b, "tree_b")
    seed = _require_stimulus(stimulus)
    if case is None:
        case = DEFAULT_CASE
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
    trace_a = _require_log(run(binary_a, seed), tree_a, seed)
    trace_b = _require_log(run(binary_b, seed), tree_b, seed)
    tohost_a = _extract_tohost(trace_a)
    tohost_b = _extract_tohost(trace_b)
    checksum_a = checksum_log(trace_a)
    checksum_b = checksum_log(trace_b)
    tohost_match = tohost_a == tohost_b
    checksum_match = checksum_a == checksum_b
    raw_a = trace_a.splitlines()
    raw_b = trace_b.splitlines()
    first_diff = None
    if not (checksum_match and trace_a == trace_b):
        first_diff = _first_diff_lines(
            _normalize_lines(raw_a), _normalize_lines(raw_b),
            raw_a, raw_b)
    trace_match = first_diff is None
    if tohost_match and checksum_match and trace_match:
        divergence = None
    else:
        if not tohost_match:
            kind = "tohost"
        elif not checksum_match:
            kind = "checksum"
        else:
            kind = "trace"
        divergence = {
            "tohost_match": tohost_match,
            "checksum_match": checksum_match,
            "trace_match": trace_match,
            "kind": kind,
            "first_diff": first_diff,
        }
    return {
        "match": divergence is None,
        "stimulus": seed,
        "case": case.get("name", "?"),
        "tree_a": tree_a,
        "tree_b": tree_b,
        "tohost_a": tohost_a,
        "tohost_b": tohost_b,
        "trace_a": trace_a,
        "trace_b": trace_b,
        "checksum_a": checksum_a,
        "checksum_b": checksum_b,
        "divergence": divergence,
    }


def divergence_report(result):
    """Render a run_pair result as a pinned human-readable report.

    MATCH names both trees, the stimulus seed, and the shared tohost;
    DIVERGENCE names the kind (tohost|checksum|trace) plus the
    first-differing 1-based line with both sides' contents quoted
    verbatim, so the differing line is greppable from the report.
    """
    verdict = "MATCH" if result.get("match") else "DIVERGENCE"
    lines = [
        "Differential DUT: %s" % verdict,
        "- tree_a: %s" % result.get("tree_a"),
        "- tree_b: %s" % result.get("tree_b"),
        "- case: %s stimulus: seed %s (NOP program)"
        % (result.get("case"), result.get("stimulus")),
        "- tohost_a: %s" % result.get("tohost_a"),
        "- tohost_b: %s" % result.get("tohost_b"),
        "- checksum_a: %s" % result.get("checksum_a"),
        "- checksum_b: %s" % result.get("checksum_b"),
    ]
    div = result.get("divergence")
    if div is None:
        lines.append("MATCH: identical tohost + byte-identical traces "
                     "on the same stimulus.")
    else:
        fd = div.get("first_diff") or {}
        lines.append("DIVERGENCE: %s mismatch (tohost_match=%s "
                     "checksum_match=%s trace_match=%s)."
                     % (div.get("kind"), div.get("tohost_match"),
                        div.get("checksum_match"), div.get("trace_match")))
        lines.append("- first differing line %s: %r vs %r"
                     % (fd.get("line"), fd.get("tree_a_line"),
                        fd.get("tree_b_line")))
    return "\n".join(lines) + "\n"
