#!/usr/bin/env python3
"""Task E7: Metastability injector (plan item 57, Wave E FIND BUGS).

Forces X on an unsynchronized clock-domain crossing in a COPY of a
design tree, then reports X reaching an observable signal.

Interfaces:
  inject(pristine_tree, crossing, workdir=None) -> scratch path
    crossing: {"signal", "driver_clock", "load_clock",
      "has_sync"?}. Copies pristine_tree via shutil.copytree to a
    fresh scratch dir (default: tmp via tempfile.mkdtemp) and drops
    an X-injection marker for the signal in the COPY. Refuses loudly:
      - synchronized crossings (truthy has_sync): nothing metastable
        to force -- RuntimeError.
      - same-clock pairs (driver == load): not a crossing -- ValueError.
      - missing keys / blank signal / bad clocks / missing tree.
    The input tree is NEVER written.
  detect(log, observables=None) -> [evidence]
    Scans static sim-trace text for X values on observable-signal
    lines. Each evidence is {"lineno" (1-based), "line" (verbatim),
    "signal"}. Empty result = no X reached any observable (RED).
  line_has_x(line) -> bool
    True iff a trace line carries an X value token (0x..x runs, bare
    x-runs, Verilog 'bx literals) -- signal names merely containing
    the letter x ("xfer", "max_addr") never match.

Honesty contract:
  - inject copies FIRST (shutil.copytree to tmp/workdir) and asserts
    the scratch path differs from the pristine tree; any build/run
    error path raises loudly, never recorded as clean.
  - detect returns [] on a clean trace; it never invents X.
  - Fixture-based proof: callers wire their own run_fn (or a static
    trace); this module performs no builds and no sim runs.

Default-off contract: runs ONLY on explicit call. No imports from
agent/loop.py or agent/guard.py, no guard-verdict changes, no repo
writes (markers land in the scratch copy only), no caller wired.

Pure stdlib; no API, no keys, no network.
"""

import os
import re
import shutil
import tempfile

__all__ = ["inject", "detect", "line_has_x"]

DEFAULT_OBSERVABLES = ("tohost",)

# X-value tokens (mirrors the xprop hunter shapes: the "0x" prefix's x
# is not a value; signal names containing x never match).
_X_HEX_RE = re.compile(r"0[xX][0-9a-fA-FxX]*[xX][0-9a-fA-FxX]*")
_X_RUN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?!0[xX])[0-9a-fA-F]*[xX][0-9a-fA-FxX]*"
    r"(?![A-Za-z0-9_])")
_X_LITERAL_RE = re.compile(r"'[bBhHdD][0-9a-fA-FxX]*[xX][0-9a-fA-FxX]*")


def line_has_x(line):
    """True iff a trace line carries an X value token."""
    if not isinstance(line, str):
        raise TypeError("log line must be text, got %r" % type(line))
    return bool(_X_HEX_RE.search(line)
                or _X_RUN_RE.search(line)
                or _X_LITERAL_RE.search(line))


def _validate_crossing(crossing):
    """Checked crossing dict; returns (signal, driver, load)."""
    if not isinstance(crossing, dict):
        raise TypeError(
            "crossing must be a dict, got %r: refusing to force X on "
            "an undescribed crossing" % (crossing,))
    for key in ("signal", "driver_clock", "load_clock"):
        if key not in crossing:
            raise KeyError(
                "crossing needs a %r key, got %r: refusing to force X "
                "without attribution" % (key, crossing))
    signal = crossing["signal"]
    if not isinstance(signal, str) or not signal.strip():
        raise ValueError(
            "crossing signal must be a non-blank string, got %r: "
            "refusing to force X on a nameless net" % (signal,))
    for key in ("driver_clock", "load_clock"):
        clock = crossing[key]
        if not isinstance(clock, str) or not clock:
            raise ValueError(
                "%s must be a non-empty str, got %r: refusing to judge "
                "a crossing on an undescribed domain" % (key, clock))
    driver = crossing["driver_clock"]
    load = crossing["load_clock"]
    if driver == load:
        raise ValueError(
            "driver_clock == load_clock (%r): same-clock pair is not a "
            "crossing, refusing to inject" % (driver,))
    if crossing.get("has_sync"):
        raise RuntimeError(
            "synchronized crossing %r (has_sync set): synchronizer "
            "blocks metastability, refusing to inject" % (signal,))
    return signal.strip(), driver, load


def _marker_name(signal):
    """Filesystem-safe marker filename for a signal."""
    safe = "".join(c if c.isalnum() or c in "._-" else "_"
                   for c in signal.strip())
    return "metainject_x_%s.txt" % (safe or "signal")


def inject(pristine_tree, crossing, workdir=None):
    """Force X on an unsynchronized crossing in a COPY of the tree.

    pristine_tree: clean design tree (read-only; never written).
    crossing: {"signal", "driver_clock", "load_clock", "has_sync"?}.
    workdir: parent dir for the scratch copy (default: fresh tmp).

    Returns the scratch-copy path carrying the X-injection marker.
    Synchronized crossings and same-clock pairs raise loudly.
    """
    signal, driver, load = _validate_crossing(crossing)
    src = os.path.abspath(pristine_tree)
    if not os.path.isdir(src):
        raise FileNotFoundError("missing pristine tree: %s"
                                % (pristine_tree,))

    def _ignore(_dir, names):
        return [n for n in names
                if n in (".git", "__pycache__")
                or n.startswith("obj_afterglow_")
                or n.endswith(".orig") or n.endswith(".rej")]

    parent = os.path.abspath(workdir) if workdir \
        else tempfile.mkdtemp(prefix="metainject-")
    os.makedirs(parent, exist_ok=True)
    dest = os.path.join(parent, "scratch")
    if os.path.exists(dest):
        raise RuntimeError("metainject scratch exists (refusing): %s"
                           % dest)
    shutil.copytree(src, dest, ignore=_ignore)
    if os.path.abspath(dest) == src:
        raise RuntimeError("scratch copy landed on pristine tree: "
                           "refusing")
    marker = os.path.join(dest, _marker_name(signal))
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("metainject X forced on unsynchronized crossing\n"
                 "signal=%s driver=%s load=%s\n"
                 % (signal, driver, load))
    return dest


def detect(log, observables=None):
    """Scan trace text for X values on observable-signal lines.

    log: sim trace text (static fixture or run_fn output).
    observables: signal names (default DEFAULT_OBSERVABLES).

    Returns [evidence, ...] with {"lineno", "line", "signal"}; []
    means no X reached any observable.
    """
    if not isinstance(log, str):
        raise TypeError("log must be text, got %r" % type(log))
    signals = tuple(observables) if observables is not None \
        else DEFAULT_OBSERVABLES
    if not signals:
        raise ValueError("observables must be a non-empty list of "
                         "signal names, got %r" % (observables,))
    for sig in signals:
        if not isinstance(sig, str) or not sig.strip():
            raise ValueError("observable must be a non-blank string, "
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
                })
                break  # one evidence entry per line (first signal wins)
    return evidence
