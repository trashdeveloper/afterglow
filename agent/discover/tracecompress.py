#!/usr/bin/env python3
"""Task E9: Trace compressor (plan item 59, Wave E FIND BUGS).

Shrinks a long sim trace so it fits a token budget while keeping the
failing window exactly reproducible.

Interfaces:
  compress(trace=None, budget=2500, trace_lines=None, keep_window=5)
    -> {"text", "lines", "tokens", "checksum"}
    trace: original trace as text (newline-separated str) OR as a
      list/tuple of lines (positional list accepted: the parent brief
      spelling ``compress(trace_lines, keep_window)``).
    trace_lines: keyword alias for the list/tuple spelling (pass
      trace OR trace_lines, never both).
    budget: max estimated tokens for the compressed text (default
      2500, the collector cap). Whitespace-split estimate, identical
      to agent/collect.py::approx_tokens.
    keep_window: number of head lines AND tail (failing-window) lines
      kept byte-verbatim (default 5). The middle is run-length
      collapsed: each run of L >= 2 consecutive identical lines keeps
      ONE line verbatim plus a "- " chatter marker carrying the run
      length (``- [tracecompress x{L} identical lines collapsed]``);
      transitions (non-duplicate neighbours) are always kept verbatim.
      If RLE alone still exceeds the budget, the middle is truncated
      from the FRONT (oldest first, tail never touched) with a
      truncation marker until it fits.
    Returns {"text" (compressed str), "lines" (compressed list),
      "tokens" (estimate of text), "checksum" (sha256 of the ORIGINAL
      trace via agent/repair/equiv.py::checksum_log, so the compressed
      trace replays to the identical checksum downstream)}.

Honesty contract:
  - The checksum is ALWAYS checksum_log of the original trace: the
    compressed text replays to the identical checksum by construction
    (verified by the plan-gate test), never recomputed over the
    collapsed text.
  - The first keep_window and last keep_window lines are byte-exact
    copies of the input (the oracle marker in the failing tail always
    survives); the middle collapse is deterministic run-length
    encoding with the run length pinned in the marker.
  - Budget is enforced loudly: if even head + tail alone exceed the
    budget, raises ValueError instead of silently breaching the cap.
  - Empty traces, non-string lines, missing/ambiguous inputs, and bad
    budget/keep_window values all raise loudly (TypeError/ValueError);
    predicate-style silent fallbacks are never used.
  - Inputs are never mutated; a NEW list is returned. Deterministic:
    same inputs -> same output.
  - Truncation markers start with "- " (the equiv chatter convention),
    never with signal text, so they read as compressor metadata.

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, writes
no files (returns a dict only), and no caller is wired.

Imports: stdlib only, plus agent/repair/equiv.py::checksum_log (with a
local fallback of identical semantics if the repair module is not
importable); no API, no keys, no network.
"""

import hashlib

__all__ = ["compress", "DEFAULT_BUDGET", "DEFAULT_KEEP_WINDOW"]

DEFAULT_BUDGET = 2500
DEFAULT_KEEP_WINDOW = 5


def _repo_checksum_log(log):
    """Local checksum_log fallback (identical semantics to equiv's).

    Verilator chatter lines (starting with "- ") are excluded; every
    other line feeds sha256 as "\\n".join + "\\n".
    """
    if not isinstance(log, str):
        raise TypeError("log must be text, got %r" % type(log))
    det = [ln for ln in log.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def _load_checksum_log():
    """Repo checksum via repair.equiv when importable, else fallback."""
    for module in ("repair.equiv", "agent.repair.equiv"):
        try:
            mod = __import__(module, fromlist=["checksum_log"])
            fn = getattr(mod, "checksum_log", None)
            if callable(fn):
                return fn
        except ImportError:
            continue
    return _repo_checksum_log


_checksum_log = _load_checksum_log()


def _approx_tokens(text):
    """Whitespace-split token estimate (== collect.approx_tokens)."""
    return len(text.split())


def _resolve_trace(trace, trace_lines):
    """Map the accepted spellings onto a NEW list of lines."""
    if trace is not None and trace_lines is not None:
        raise TypeError(
            "pass trace OR trace_lines, not both: refusing to "
            "compress an ambiguous input")
    raw = trace if trace is not None else trace_lines
    if raw is None:
        raise TypeError(
            "compress() missing required trace/trace_lines: refusing "
            "to compress nothing")
    if isinstance(raw, str):
        lines = raw.splitlines()
    elif isinstance(raw, (list, tuple)):
        lines = list(raw)
    else:
        raise TypeError(
            "trace must be newline text or a list/tuple of lines, got "
            "%r: refusing to compress a non-trace" % (raw,))
    if not lines:
        raise ValueError(
            "empty trace carries no signal: refusing to compress "
            "nothing (never invents a trace)")
    for ln in lines:
        if not isinstance(ln, str):
            raise TypeError(
                "trace lines must be str, got %r: refusing to "
                "compress a non-text line" % (ln,))
    return lines


def _require_budget(budget):
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise TypeError(
            "budget must be an int token cap, got %r: refusing to "
            "compress against a non-budget" % (budget,))
    if budget < 1:
        raise ValueError(
            "budget must be >= 1, got %d: refusing to compress to "
            "an empty allowance" % (budget,))
    return budget


def _require_keep_window(keep_window):
    if keep_window is None:
        return DEFAULT_KEEP_WINDOW
    if isinstance(keep_window, bool) or not isinstance(keep_window, int):
        raise TypeError(
            "keep_window must be an int, got %r: refusing to guard "
            "a non-window" % (keep_window,))
    if keep_window < 0:
        raise ValueError(
            "keep_window must be >= 0, got %d: refusing a negative "
            "verbatim window" % (keep_window,))
    return keep_window


def _collapse_runs(middle):
    """Run-length collapse: 1 kept line + count marker per run."""
    out = []
    i = 0
    n = len(middle)
    while i < n:
        j = i + 1
        while j < n and middle[j] == middle[i]:
            j += 1
        run = j - i
        out.append(middle[i])
        if run >= 2:
            out.append("- [tracecompress x%d identical lines "
                       "collapsed]" % run)
        i = j
    return out


def _fit_budget(head, middle, tail, budget):
    """Truncate the middle from the FRONT until tokens fit.

    Head/tail are never touched. Returns the middle lines (with a
    truncation marker when lines were dropped). Raises ValueError if
    head + tail alone already exceed the budget.
    """
    dropped = 0
    marker = []
    text = "\n".join(head + marker + middle + tail)
    while _approx_tokens(text) > budget and middle:
        avg = max(1, _approx_tokens("\n".join(middle)) // max(1, len(middle)))
        excess = _approx_tokens(text) - budget
        chunk = max(1, min(len(middle), (excess + avg - 1) // avg))
        del middle[:chunk]
        dropped += chunk
        marker = ["- [tracecompress: truncated %d lines to fit "
                  "budget %d]" % (dropped, budget)]
        text = "\n".join(head + marker + middle + tail)
    if _approx_tokens(text) > budget:
        raise ValueError(
            "head + tail windows alone exceed budget %d tokens: "
            "refusing to silently breach the cap (shrink keep_window "
            "or raise the budget)" % (budget,))
    return marker + middle


def compress(trace=None, budget=DEFAULT_BUDGET, trace_lines=None,
             keep_window=None):
    """Compress a sim trace to a budgeted, checksum-stable excerpt.

    trace (str, or list/tuple of lines) XOR trace_lines (list/tuple):
      the original trace. budget: max tokens (default 2500).
      keep_window: head/tail lines kept verbatim (default 5).

    Returns {"text", "lines", "tokens", "checksum"} where checksum is
    checksum_log of the ORIGINAL trace.
    """
    lines = _resolve_trace(trace, trace_lines)
    budget = _require_budget(budget)
    keep = _require_keep_window(keep_window)
    original_text = "\n".join(lines)
    checksum = _checksum_log(original_text)
    if len(lines) <= 2 * keep:
        out = list(lines)
    else:
        head = lines[:keep] if keep else []
        tail = lines[len(lines) - keep:] if keep else []
        middle = _collapse_runs(lines[len(head):len(lines) - keep]
                                if keep else list(lines))
        middle = _fit_budget(head, middle, tail, budget)
        out = head + middle + tail
    text = "\n".join(out)
    tokens = _approx_tokens(text)
    if tokens > budget:
        raise ValueError(
            "compressed trace is %d tokens over budget %d: refusing "
            "to silently breach the cap" % (tokens, budget))
    return {"text": text, "lines": list(out), "tokens": tokens,
            "checksum": checksum}
