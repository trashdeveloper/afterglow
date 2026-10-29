#!/usr/bin/env python3
"""Task B10: Counterexample minimizer (plan item 20, Wave B SEE MORE).

Shrinks a failing trace window to minimal lines while preserving the
FAIL signature (oracle marker, e.g. ``tohost: FAIL`` or a ``TRAP:``
line).

Interface::

  signature_present(text, signature) -> bool
  minimize_trace(text, signature) -> (minimized_text, kept_indices)

``signature_present`` reports whether the exact substring ``signature``
occurs in ``text`` (case-sensitive; may span lines via an embedded
newline). Non-string inputs or an empty signature raise ValueError.

``minimize_trace`` delta-minimizes the trace: starting from all lines,
it repeatedly tries removing a chunk of lines and keeps the removal
whenever the joined remainder still contains the signature (ddmin
style: chunk count starts at 2, shrinks on success, doubles on
failure). The result is 1-minimal: dropping any single kept line
loses the signature. It returns the minimized text (same trailing
newline convention as the input) plus the sorted list of kept
0-based original line numbers, so callers can map back to the
source window.

When the signature depends on the fault line region, that region
survives: a line without which the signature vanishes can never be
removed. Lines the signature does not need are dropped.

Errors are loud: a signature absent from the original trace, empty
text, an empty signature, or non-string inputs raise ValueError.
Minimizing a passing trace never silently returns an empty window.

Default-off contract (Wave B header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py and no caller is wired: agent/loop.py is untouched.

Pure stdlib (no imports at all); no API, no keys, no network, no I/O.
"""

_VERSION = "1"


def signature_present(text, signature):
    """Check whether the exact FAIL signature occurs in the trace.

    Case-sensitive substring match on the raw text. A signature
    containing a newline matches only when those lines are adjacent.
    Non-string inputs or an empty signature raise ValueError.
    """
    if not isinstance(text, str):
        raise ValueError(
            "minimize: text must be a string, got %r" % (type(text),))
    if not isinstance(signature, str) or signature == "":
        raise ValueError(
            "minimize: signature must be a non-empty string, "
            "got %r" % (signature,))
    return signature in text


def _join(lines, trailing):
    """Rebuild window text from lines, mirroring input convention."""
    text = "\n".join(lines)
    if trailing and text:
        text += "\n"
    return text


def minimize_trace(text, signature):
    """Shrink a failing trace to a 1-minimal window keeping signature.

    Returns ``(minimized_text, kept_indices)`` where kept_indices
    are the ascending 0-based original line numbers still present.
    Raises ValueError when the signature is absent from the input
    or inputs are invalid.
    """
    if not isinstance(text, str):
        raise ValueError(
            "minimize: text must be a string, got %r" % (type(text),))
    if not isinstance(signature, str) or signature == "":
        raise ValueError(
            "minimize: signature must be a non-empty string, "
            "got %r" % (signature,))
    if signature not in text:
        raise ValueError(
            "minimize: signature %r absent from trace "
            "(refusing to minimize a passing trace)" % (signature,))
    lines = text.splitlines()
    trailing = text.endswith("\n")

    def keeps(indices):
        return signature in _join([lines[i] for i in indices], trailing)

    kept = list(range(len(lines)))
    if not keeps(kept):  # pragma: no cover - guarded above, kept for safety
        raise ValueError(
            "minimize: signature %r absent from trace" % (signature,))
    chunks = 2
    while len(kept) >= 2:
        width = -(-len(kept) // chunks)  # ceil: partition into chunks
        shrunk = False
        for start in range(0, len(kept), width):
            candidate = kept[:start] + kept[start + width:]
            if not candidate:
                continue
            if keeps(candidate):
                kept = candidate
                chunks = max(chunks - 1, 2)
                shrunk = True
                break
        if not shrunk:
            if chunks >= len(kept):
                break
            chunks = min(2 * chunks, len(kept))
    return _join([lines[i] for i in kept], trailing), kept
