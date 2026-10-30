#!/usr/bin/env python3
"""Task E8: Stimulus minimizer (plan item 58, Wave E FIND BUGS).

Shrinks a failing stimulus vector to a 1-minimal subsequence that still
exposes the fault (ddmin-style delta debugging, Zeller 2002).

Interfaces:
  minimize(stimulus, still_red_fn) -> minimal
    stimulus: list/tuple of steps (any element type; order preserved).
    still_red_fn(vector) -> truthy means the vector still exposes the
      fault (True = RED). Called with a NEW list each time; must be a
      pure function of its argument (no wall-clock/network dependence).
    Returns a subsequence of the input (same order, same element
    objects) that still exposes the fault and is 1-minimal: removing
    ANY single element breaks the exposure.
  Alias spellings (same function, same semantics):
    minimize(vector, run_fn)  ==  minimize(stimulus, still_red_fn).
    Positional calls are identical; either keyword pair is accepted
    (but not mixed: passing both spellings raises TypeError).

Honesty contract:
  - The input MUST expose the fault: if still_red_fn(list(stimulus))
    is falsy (or the stimulus is empty), raises ValueError -- never
    returns a non-exposing vector.
  - Only vectors for which still_red_fn returns truthy are ever
    adopted; the returned vector always satisfies still_red_fn.
  - The returned vector is 1-minimal: every single-element removal is
    re-tested during the run (grain growth to singletons plus an
    explicit single-element polish pass), so no redundant step survives.
  - Non-sequence stimulus (str/bytes/scalars), missing arguments, or a
    non-callable predicate all raise TypeError loudly -- never silently
    minimized or skipped.
  - Predicate exceptions propagate unchanged (a sim explosion is never
    mistaken for a GREEN vector).
  - Inputs are never mutated; a NEW list (or tuple, matching a tuple
    input) is returned. Deterministic: same inputs -> same output.
  - Empty vectors carry no stimulus and are rejected even if a
    degenerate predicate claims they expose.

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing (not even stdlib
helpers beyond the language core) and nothing from agent/loop.py or
agent/guard.py, never changes guard verdicts, writes no files (returns
a subsequence only), and no caller is wired.

Pure computation; no API, no keys, no network.
"""

__all__ = ["minimize"]


def _resolve_args(stimulus, still_red_fn, vector, run_fn):
    """Map the two accepted spellings onto (stimulus, still_red_fn)."""
    if vector is not None:
        if stimulus is not None:
            raise TypeError(
                "pass stimulus OR vector, not both: refusing to "
                "minimize an ambiguous input (%r vs %r)"
                % (stimulus, vector))
        stimulus = vector
    if run_fn is not None:
        if still_red_fn is not None:
            raise TypeError(
                "pass still_red_fn OR run_fn, not both: refusing to "
                "minimize under an ambiguous predicate")
        still_red_fn = run_fn
    if stimulus is None:
        raise TypeError(
            "minimize() missing required stimulus/vector: refusing to "
            "minimize nothing")
    if still_red_fn is None:
        raise TypeError(
            "minimize() missing required still_red_fn/run_fn predicate: "
            "refusing to minimize without an exposure test")
    if not callable(still_red_fn):
        raise TypeError(
            "still_red_fn must be callable, got %r: refusing to "
            "minimize without an exposure test" % (still_red_fn,))
    if isinstance(stimulus, (str, bytes)) or not isinstance(
            stimulus, (list, tuple)):
        raise TypeError(
            "stimulus must be a list/tuple of steps, got %r: refusing "
            "to minimize a non-vector" % (stimulus,))
    return stimulus, still_red_fn


def _split_ranges(length, n):
    """Index ranges splitting length items into n roughly equal chunks.

    Returns [(start, end), ...] with empty chunks dropped, so the
    ranges always partition the sequence deterministically.
    """
    part, rest = divmod(length, n)
    ranges = []
    start = 0
    for i in range(n):
        size = part + (1 if i < rest else 0)
        if size:
            ranges.append((start, start + size))
            start += size
    return ranges


def minimize(stimulus=None, still_red_fn=None, vector=None, run_fn=None):
    """Shrink a failing stimulus to a 1-minimal still-exposing subsequence.

    stimulus (or vector): list/tuple of steps; must expose the fault
      (still_red_fn(list(stimulus)) truthy) or ValueError is raised.
    still_red_fn (or run_fn): predicate called as fn(list) -> truthy
      iff the candidate vector still exposes the fault.

    Returns a NEW list (tuple in -> tuple out) holding a subsequence of
    the input, in original order, that still exposes the fault and from
    which no single element can be removed without breaking exposure.
    """
    stimulus, still_red_fn = _resolve_args(stimulus, still_red_fn,
                                           vector, run_fn)
    want_tuple = isinstance(stimulus, tuple)
    current = list(stimulus)
    if not current:
        raise ValueError(
            "empty stimulus carries no fault: refusing to minimize "
            "nothing (never returns a non-exposing vector)")
    if not still_red_fn(list(current)):
        raise ValueError(
            "input stimulus does not expose the fault: refusing to "
            "minimize a GREEN vector (never returns a non-exposing "
            "vector)")

    # ddmin core: try complements (shrink around a chunk), then chunks
    # alone (descend into a chunk); else refine granularity up to
    # singletons. Every adopted candidate tested RED by still_red_fn.
    n = 2
    while len(current) >= 2:
        ranges = _split_ranges(len(current), n)
        if len(ranges) < 2:
            break
        reduced = False
        for (start, end) in ranges:
            candidate = current[:start] + current[end:]
            if candidate and still_red_fn(list(candidate)):
                current = candidate
                n = max(n - 1, 2)
                reduced = True
                break
        if reduced:
            continue
        for (start, end) in ranges:
            candidate = current[start:end]
            if still_red_fn(list(candidate)):
                current = candidate
                n = 2
                reduced = True
                break
        if reduced:
            continue
        if n >= len(current):
            break
        n = min(len(current), 2 * n)

    # Explicit single-element polish: guarantees 1-minimality even for
    # non-monotone predicates where chunking alone could strand a
    # redundant step. Only RED candidates are adopted.
    for i in reversed(range(len(current))):
        candidate = current[:i] + current[i + 1:]
        if candidate and still_red_fn(list(candidate)):
            current = candidate

    if want_tuple:
        return tuple(current)
    return current
