#!/usr/bin/env python3
"""Task A8: reset/X-prop hunter (plan item 8, Wave A FIND BUGS).

Forces X on chosen regs at reset release in sim and reports X reaching
committed state (tohost / mem_valid / TB check outputs).

Interfaces:
  line_has_x(line) -> bool
  scan_log_for_commit_x(log, commit_signals=None) -> [evidence]
  prepare_scratch_tree(pristine_tree, workdir=None) -> scratch path
  build_with_x(pristine_tree, case, target, workdir=None,
               build_fn=None) -> binary path
  hunt(pristine_tree, case, targets, build_fn=None, run_fn=None,
       seed=42, commit_signals=None, workdir=None) -> report
  write_discovery_report(path, case_name, report) -> path

How X is forced (default build path): the pristine tree is COPIED to a
scratch dir (never written) and built as a Verilator --x-initial-edge
variant (X-edge propagation enabled), so X driven onto a target reg at
reset release propagates instead of being optimized into 0/1. Targeted
forcing of a specific reg is done by callers via the build_fn seam
(e.g. a scratch-tree patch driving 'x onto the target + the variant
build); hunt attributes any commit-X observed during a target's run to
that target.

Honesty contract (mirrors fuzzer/blindspot/seeddiff: never invent signal):
  - Empty/blank/duplicate targets raise ValueError loudly. Zero targets
    produce zero signal, so none is invented.
  - The pristine tree is NEVER written: the default build copies it to
    scratch first; build_with_x asserts the build ran outside pristine.
  - Any build/run error, non-string log, or empty/whitespace-only log
    raises loudly -- NEVER recorded as clean or leak.
  - Per-target status is one of:
      leak: X observed on a commit-signal line (evidence cites lineno,
        line, signal, and cycle when the line carries one).
      clean: X observed SOMEWHERE in the trace (forcing demonstrably
        took) but none on any commit-signal line.
      inconclusive: NO X anywhere in the trace AND no leak. Forcing may
        not have taken; reporting clean would fake agreement, so the
        run is inconclusive with a reason, never clean.
  - all_clean is True ONLY when every target is clean. Any leak or
    inconclusive makes it False.
  - Discovery reports NEVER carry verdicts; they list per-target
    clean/leak/inconclusive with evidence.
"""

import os
import re
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

# Committed-state signals: an X on any of these lines means the reset
# value leaked into architecturally visible state. TB check outputs
# surface through tohost/TRAP: lines.
COMMIT_SIGNALS = ("tohost", "mem_valid", "TRAP:")

# Variant-build flags enabling X-edge propagation in Verilator.
X_VARIANT_FLAGS = ["--x-initial-edge"]

# X-value tokens in sim text output. Three shapes:
#   0x-literal with an x in the digits (the "0x" prefix alone never
#     matches: 0xdead/0x00000040 are clean by construction).
#   hex-digit run containing >= 1 x (covers bare "xxxxxxxx" %h prints
#     and digit-attached runs); bounded by non-word chars on BOTH sides
#     and rejecting a leading "0x" (the hex PREFIX's x is not a value;
#     genuine 0x...+x prints are caught by _X_HEX_RE) so signal names
#     ("xfer", "max_addr") and clean hex ("0x00000040") never match --
#     "xfer" fails the trailing boundary at 'r' under every backtrack.
#   Verilog literal with an x ('bx, 32'hx).
_X_HEX_RE = re.compile(r"0[xX][0-9a-fA-FxX]*[xX][0-9a-fA-FxX]*")
_X_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?!0[xX])[0-9a-fA-F]*[xX][0-9a-fA-FxX]*"
    r"(?![A-Za-z0-9_])")
_X_LITERAL_RE = re.compile(r"'[bBhHdD][0-9a-fA-FxX]*[xX][0-9a-fA-FxX]*")

_CYCLE_RES = (
    re.compile(r"CYCLES=(\d+)"),
    re.compile(r"@(\d+)"),
    re.compile(r"[Cc][Yy][Cc][Ll][Ee]\b[ =:]*(\d+)"),
)


def line_has_x(line):
    """True iff a sim log line carries an X value token (not a signal
    name merely containing the letter x)."""
    if not isinstance(line, str):
        raise TypeError("log line must be text, got %r" % type(line))
    return bool(_X_HEX_RE.search(line)
                or _X_RUN_RE.search(line)
                or _X_LITERAL_RE.search(line))


def _cycle_of(line):
    """Cycle number carried by a log line, or None."""
    for rx in _CYCLE_RES:
        m = rx.search(line)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def scan_log_for_commit_x(log, commit_signals=None):
    """Scan sim log text for X values on committed-state signal lines.

    Returns [evidence, ...] where each evidence is {"lineno" (1-based),
    "line" (verbatim), "signal" (the matched commit signal), "cycle"
    (int or None)}. Lines naming no commit signal -- even with X --
    are not evidence. Empty log yields [] (hunt refuses it loudly).
    """
    if not isinstance(log, str):
        raise TypeError("log must be text, got %r" % type(log))
    signals = tuple(commit_signals) if commit_signals is not None \
        else COMMIT_SIGNALS
    if not signals:
        raise ValueError("commit_signals must be a non-empty list of "
                         "signal names, got %r" % (commit_signals,))
    for sig in signals:
        if not isinstance(sig, str) or not sig.strip():
            raise ValueError("commit signal must be a non-blank string, "
                             "got %r" % (sig,))
    evidence = []
    for lineno, line in enumerate(log.splitlines(), start=1):
        if not line_has_x(line):
            continue
        for sig in signals:
            if sig in line:
                evidence.append({
                    "lineno": lineno,
                    "line": line,
                    "signal": sig,
                    "cycle": _cycle_of(line),
                })
                break  # one evidence entry per line (first signal wins)
    return evidence


def _validate_targets(targets):
    """Non-empty list of unique non-blank reg names; raises loudly."""
    if not isinstance(targets, list) or not targets:
        raise ValueError("targets must be a non-empty list of reg names, "
                         "got %r: refusing to report agreement from "
                         "nothing" % (targets,))
    seen = set()
    clean = []
    for tgt in targets:
        if not isinstance(tgt, str) or not tgt.strip():
            raise ValueError("target reg must be a non-blank string, got "
                             "%r" % (tgt,))
        name = tgt.strip()
        if name in seen:
            raise ValueError("duplicate target %r: refusing to judge the "
                             "same reg twice" % (name,))
        seen.add(name)
        clean.append(name)
    return clean


def prepare_scratch_tree(pristine_tree, workdir=None):
    """Copy pristine_tree -> fresh scratch dir. Pristine is only read."""
    src = os.path.abspath(pristine_tree)
    if not os.path.isdir(src):
        raise FileNotFoundError("missing pristine tree: %s" % pristine_tree)

    def _ignore(_dir, names):
        return [n for n in names
                if n in (".git", "__pycache__")
                or n.startswith("obj_afterglow_")
                or n.endswith(".orig") or n.endswith(".rej")]

    parent = os.path.abspath(workdir) if workdir \
        else tempfile.mkdtemp(prefix="xprop-")
    os.makedirs(parent, exist_ok=True)
    dest = os.path.join(parent, "scratch")
    if os.path.exists(dest):
        raise RuntimeError("xprop scratch exists (refusing): %s" % dest)
    shutil.copytree(src, dest, ignore=_ignore)
    return dest


def build_with_x(pristine_tree, case, target, workdir=None, build_fn=None):
    """Build the --x-initial-edge variant on a scratch copy.

    Copies pristine_tree (never writes it), verifies every case file
    exists in scratch, appends X_VARIANT_FLAGS to a COPIED vflags list
    (the caller's case dict is never mutated), and delegates to
    build_fn or vbackend.build. Returns the sim binary path; build
    errors raise loudly.
    """
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target reg must be a non-blank string, got %r"
                         % (target,))
    if not isinstance(case, dict):
        raise ValueError("case must be a dict, got %r" % (case,))
    try:
        dut_files = list(case["files"])
    except (KeyError, TypeError):
        raise KeyError("case is missing required key 'files'")
    if not dut_files:
        raise ValueError("case files list is empty; nothing to build")
    scratch = prepare_scratch_tree(pristine_tree, workdir=workdir)
    if os.path.abspath(scratch) == os.path.abspath(pristine_tree):
        raise RuntimeError("scratch copy landed on pristine tree: refusing")
    for rel in dut_files:
        if not os.path.isfile(os.path.join(scratch, str(rel))):
            raise FileNotFoundError(
                "case file %r missing in scratch tree (pristine tree "
                "lacks it?)" % (rel,))
    try:
        vflags = list(case.get("vflags", []) or [])
    except AttributeError:
        raise ValueError("case vflags must be a list, got %r"
                         % (case.get("vflags"),))
    x_case = dict(case)
    x_case["vflags"] = vflags
    for flag in X_VARIANT_FLAGS:
        if flag not in vflags:
            vflags.append(flag)
    build = build_fn or vbackend.build
    return build(scratch, x_case)


def _default_build(tree, case, target):
    return build_with_x(tree, case, target)


def _default_run(binary, target, seed):
    """Run the ALREADY-BUILT sim binary with +seed; return log text.

    Exit code is deliberately ignored (oracle markers decide, per the
    fuzzer/blindspot contract); a timeout raises (never clean/leak).
    """
    del target  # per-target forcing is baked in at build time
    parent = os.path.dirname(os.path.abspath(str(binary)))
    if os.path.basename(parent).startswith("obj_afterglow_"):
        cwd = os.path.dirname(parent)
    else:
        cwd = parent
    rc, log = vbackend.sh([str(binary), "+seed=%d" % int(seed)],
                          cwd=cwd, timeout=SIM_TIMEOUT_S)
    del rc  # exit code never decides; the trace scan does
    return log


def hunt(pristine_tree, case, targets, build_fn=None, run_fn=None,
         seed=42, commit_signals=None, workdir=None):
    """Force X per target reg, run, and report X reaching commit state.

    pristine_tree: clean RTL tree (read-only; never written).
    case: vbackend case dict (or case-name string loaded via
      vbackend.load_case).
    targets: non-empty list of unique non-blank reg names.
    build_fn(tree, case, target) -> binary path (default: scratch-copy
      + --x-initial-edge variant via vbackend.build).
    run_fn(binary, target, seed) -> sim log text (default: run the
      binary with +seed via vbackend.sh).
    commit_signals: committed-state signal names (default
      COMMIT_SIGNALS).

    Returns {"case", "seed", "targets", "commit_signals", "results",
      "n_leak", "n_clean", "n_inconclusive", "all_clean"} where each
    result is {"target", "status" (leak|clear... leak|clean|
      inconclusive), "x_injected", "evidence", "reason"}. Every failure
    mode raises loudly; a report is returned ONLY for completed runs.
    """
    names = _validate_targets(targets)
    if isinstance(case, str):
        case = vbackend.load_case(case)
    if not isinstance(case, dict):
        raise ValueError("case must be a dict, got %r" % (case,))
    signals = tuple(commit_signals) if commit_signals is not None \
        else COMMIT_SIGNALS
    if not signals:
        raise ValueError("commit_signals must be non-empty, got %r"
                         % (commit_signals,))
    build = build_fn or _default_build
    run = run_fn or _default_run
    results = []
    for name in names:
        binary = build(pristine_tree, case, name)  # raises on build failure
        log = run(binary, name, int(seed))  # raises on sim error
        if not isinstance(log, str):
            raise TypeError("run_fn must return sim log text for target "
                            "%r, got %r" % (name, type(log)))
        if not log.strip():
            raise RuntimeError(
                "sim produced no log output for target %r: cannot judge "
                "X-propagation without a trace -- refusing to report"
                % (name,))
        evidence = scan_log_for_commit_x(log, commit_signals=list(signals))
        x_anywhere = any(line_has_x(ln) for ln in log.splitlines())
        if evidence:
            status = "leak"
            reason = ("X reached committed state for target %r (%d "
                      "commit-signal line(s))" % (name, len(evidence)))
        elif x_anywhere:
            status = "clean"
            reason = ("X observed in trace for target %r but none on any "
                      "commit-signal line" % (name,))
        else:
            status = "inconclusive"
            reason = ("no X observed anywhere in the trace for target %r "
                      "and none at commit: forcing may not have taken -- "
                      "refusing to claim clean" % (name,))
        results.append({
            "target": name,
            "status": status,
            "x_injected": bool(x_anywhere),
            "evidence": evidence,
            "reason": reason,
        })
    n_leak = sum(1 for r in results if r["status"] == "leak")
    n_clean = sum(1 for r in results if r["status"] == "clean")
    n_incon = sum(1 for r in results if r["status"] == "inconclusive")
    return {
        "case": case.get("name", "?"),
        "seed": int(seed),
        "targets": names,
        "commit_signals": list(signals),
        "results": results,
        "n_leak": n_leak,
        "n_clean": n_clean,
        "n_inconclusive": n_incon,
        "all_clean": bool(results) and n_clean == len(results),
    }


def write_discovery_report(path, case_name, report):
    """Write eval/discovery_xprop.md-style report: per-target status.

    Discovery reports NEVER carry verdicts; they list clean/leak/
    inconclusive per target with cycle + signal evidence for leaks.
    """
    results = report.get("results", [])
    if not results:
        raise ValueError(
            "refusing to write an empty xprop report: zero targets "
            "would fake signal from nothing")
    lines = [
        "# Discovery: reset/X-prop hunter (Task A8)",
        "",
        "- case: %s" % case_name,
        "- seed: %s" % report.get("seed", "?"),
        "- targets: %d (leak %d / clean %d / inconclusive %d)"
        % (len(results), report.get("n_leak", 0),
           report.get("n_clean", 0), report.get("n_inconclusive", 0)),
        "- all_clean: %s" % ("YES" if report.get("all_clean") else "NO"),
        "",
    ]
    for res in results:
        status = res.get("status", "?").upper()
        if res.get("status") == "leak":
            lines.append("- LEAK %s: X reached committed state:"
                         % res.get("target"))
            for ev in res.get("evidence", []):
                cyc = ("cycle %s" % ev["cycle"]
                       if ev.get("cycle") is not None else "cycle unknown")
                lines.append("  - line %d [%s, %s]: %s"
                             % (ev.get("lineno"), ev.get("signal"),
                                cyc, (ev.get("line") or "").strip()))
        else:
            lines.append("- %s %s: %s"
                         % (status, res.get("target"),
                            res.get("reason", "")))
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
