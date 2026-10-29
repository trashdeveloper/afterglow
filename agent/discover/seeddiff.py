#!/usr/bin/env python3
"""Task A4: cross-seed differ (plan item 4, Wave A FIND BUGS).

Runs the same DUT+TB over N seeds (build ONCE via sim/vbackend.py
primitives, run once per seed), collects per-seed checksums/traces,
and diffs them: identical seeds must yield no diff; any divergence
(checksum or trace mismatch) is flagged with the divergent seed pair
and the first-differing artifact.

Interfaces:
  checksum_log(log) -> sha256 hex over deterministic lines
    (mirrors vbackend.run_case: lines starting with "- " are Verilator
    chatter and are excluded from the checksum, but kept in the trace)
  diff_seeds(tree, case, seeds, build_fn=None, run_fn=None) -> report
  write_discovery_report(path, case_name, report) -> path

Honesty contract (mirrors fuzzer/blindspot/coverage: never invent signal):
  - Fewer than 2 seed entries raises ValueError (nothing to diff).
    Duplicate seeds are ALLOWED (same-seed-twice must yield no diff).
  - The build runs exactly ONCE; every build error propagates loudly.
  - Any run error (exception), non-string log, or empty log raises
    loudly -- NEVER reported as clean agreement.
  - Checksum equality alone is not agreement: the full trace must also
    match (a "- " chatter difference is a trace mismatch with matching
    checksums and is flagged) -- EXCEPT Verilator walltime/speed tokens
    on "- " chatter lines, which vary run-to-run for identical seeds
    (proven locally) and are normalized before comparison. The
    normalization is pinned by test, applies to chatter lines only,
    and raw traces are always preserved verbatim in records.
  - Discovery reports NEVER carry verdicts; they report agreement or
    the divergent pairs + first-differing lines.
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

# Verilator $finish chatter carries host timing noise
# ("- Verilator: $finish at 20us; walltime 0.001 s; speed 31.422 ms/s")
# that varies run-to-run for the SAME seed (proven: seed 42 twice ->
# speed 31.422 vs 31.359 ms/s). It is measurement noise, not DUT
# signal, so trace comparison normalizes these tokens on "- " chatter
# lines only. DUT lines compare byte-exactly; raw traces are preserved
# unmodified in every record. Any OTHER chatter difference (seed echo,
# model strings, ...) is still flagged.
_WALLTIME_RE = re.compile(r"walltime [\d.]+ s; speed [\d.]+[^ ]*")


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

    Exit code is deliberately ignored (oracle markers decide, per the
    fuzzer/blindspot contract); a timeout raises (never agreement).
    """
    rc, log = vbackend.sh([str(binary), "+seed=%d" % int(seed)],
                          cwd=str(tree_of(binary)), timeout=SIM_TIMEOUT_S)
    del rc  # exit code never decides agreement; checksums/traces do
    return log


def tree_of(binary):
    """Cwd for running a vbackend-built binary: the tree holding obj dir."""
    parent = os.path.dirname(os.path.abspath(str(binary)))
    if os.path.basename(parent).startswith("obj_afterglow_"):
        return os.path.dirname(parent)
    return parent


def _first_diff_lines(lines_a, lines_b, raw_a=None, raw_b=None):
    """First differing line (1-based) between two line lists, or None.

    Comparison runs on the (possibly normalized) lists; the reported
    seed_a_line/seed_b_line come from the raw lists at the same index
    so the artifact is quoted verbatim. None marks a missing line
    (one log is a prefix of the other).
    """
    raw_a = raw_a if raw_a is not None else lines_a
    raw_b = raw_b if raw_b is not None else lines_b
    for idx, (la, lb) in enumerate(zip(lines_a, lines_b), start=1):
        if la != lb:
            return {"line": idx, "seed_a_line": raw_a[idx - 1],
                    "seed_b_line": raw_b[idx - 1]}
    if len(lines_a) != len(lines_b):
        n = min(len(lines_a), len(lines_b)) + 1
        a_extra = raw_a[n - 1] if len(raw_a) >= n else None
        b_extra = raw_b[n - 1] if len(raw_b) >= n else None
        return {"line": n, "seed_a_line": a_extra, "seed_b_line": b_extra}
    return None


def diff_seeds(tree, case, seeds, build_fn=None, run_fn=None):
    """Build once, run once per seed, diff checksums + traces.

    tree: RTL tree path. case: vbackend case dict (or case-name string
      loaded via vbackend.load_case). seeds: list of >= 2 int seeds
      (duplicates allowed: same-seed-twice must agree).
    build_fn(tree, case) -> binary path (default vbackend.build).
    run_fn(binary, seed) -> sim log text (default: run the binary with
      +seed via vbackend.sh).

    Returns {"case", "seeds", "n", "agree", "records": [{seed,
      checksum, trace}], "divergences": [{seed_a, seed_b,
      checksum_match, trace_match, kind, first_diff}]} where kind is
    "checksum" (checksums differ -- traces necessarily differ too) or
    "trace" (checksums match but traces differ). Agreeing pairs are
    never listed. agree is True iff divergences is empty.

    Every failure mode raises loudly; a report is returned ONLY for
    completed runs (never a fake "agreement" over errors).
    """
    seed_list = [int(s) for s in seeds]
    if len(seed_list) < 2:
        raise ValueError(
            "diff_seeds needs >= 2 seed entries to diff, got %d "
            "(%r): refusing to report agreement from nothing"
            % (len(seed_list), seeds))
    if isinstance(case, str):
        case = vbackend.load_case(case)
    if not isinstance(case, dict):
        raise ValueError("case must be a dict, got %r" % (case,))
    build = build_fn or _default_build
    run = run_fn or _default_run
    binary = build(tree, case)  # raises loudly on build failure; once only
    records = []
    for seed in seed_list:
        log = run(binary, seed)  # raises loudly on sim error
        if not isinstance(log, str):
            raise TypeError("run_fn must return sim log text, got %r"
                            % type(log))
        if not log.strip():
            raise RuntimeError(
                "sim produced no log output for seed %d: cannot judge "
                "agreement without traces -- refusing to report"
                % seed)
        records.append({
            "seed": seed,
            "checksum": checksum_log(log),
            "trace": log,
        })
    divergences = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            ra, rb = records[i], records[j]
            csum_match = ra["checksum"] == rb["checksum"]
            raw_a = ra["trace"].splitlines()
            raw_b = rb["trace"].splitlines()
            fd = None
            if not (csum_match and ra["trace"] == rb["trace"]):
                fd = _first_diff_lines(
                    _normalize_lines(raw_a), _normalize_lines(raw_b),
                    raw_a, raw_b)
            trace_match = fd is None
            if csum_match and trace_match:
                continue  # agreeing pair stays silent
            divergences.append({
                "seed_a": ra["seed"],
                "seed_b": rb["seed"],
                "checksum_match": csum_match,
                "trace_match": trace_match,
                "kind": "checksum" if not csum_match else "trace",
                "first_diff": fd,
            })
    return {
        "case": case.get("name", "?"),
        "seeds": seed_list,
        "n": len(seed_list),
        "agree": not divergences,
        "records": records,
        "divergences": divergences,
    }


def write_discovery_report(path, case_name, report):
    """Write eval/discovery_seeddiff.md-style report: agreement only.

    Discovery reports NEVER carry verdicts; they state whether N seeds
    agreed and, on divergence, name each divergent pair with its
    first-differing line.
    """
    lines = [
        "# Discovery: cross-seed differ (Task A4)",
        "",
        "- case: %s" % case_name,
        "- seeds: %s" % (", ".join(str(s) for s in report.get("seeds", []))),
        "- runs: %d" % report.get("n", 0),
        "- agree: %s" % ("YES" if report.get("agree") else "NO"),
        "",
    ]
    if report.get("agree"):
        lines.append("All %d seed runs produced identical checksums and "
                     "traces: deterministic under these seeds. Seeds "
                     "above reproduce this exact comparison."
                     % report.get("n", 0))
    else:
        lines.append("DIVERGENCE: seed runs disagree (nondeterminism or "
                     "seed-sensitive behavior). Divergent pairs:")
        for div in report.get("divergences", []):
            fd = div.get("first_diff") or {}
            lines.append("- seeds %s vs %s (%s mismatch; first diff at "
                         "line %s: %r vs %r)"
                         % (div.get("seed_a"), div.get("seed_b"),
                            div.get("kind"), fd.get("line"),
                            fd.get("seed_a_line"), fd.get("seed_b_line")))
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
