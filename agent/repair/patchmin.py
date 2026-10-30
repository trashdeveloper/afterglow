#!/usr/bin/env python3
"""Task G3: Patch minimality enforcer (plan item 73).

Produces::

  minimize(patch, still_green_fn) -> smaller_patch

Delta-debugging minimizer (ddmin, Zeller 2002): given a GREEN ``patch``
(a list of patch lines/items, order significant) and a predicate
``still_green_fn(candidate)`` returning truthy while a candidate
sub-patch still passes, returns a 1-minimal sub-patch (a subset of the
input, order preserved) that is still GREEN. No single remaining item
can be dropped without going RED.

``patch`` may be a list or tuple; the return value is always a new
list. Items are opaque (compared by equality); the predicate receives
a plain list per probe. Raises ValueError when ``patch`` is not a
list/tuple, when ``still_green_fn`` is not callable, or when the full
input ``patch`` is itself not GREEN (nothing to minimize from).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib; no API, no keys, no network, no I/O.
"""

import math


def minimize(patch, still_green_fn):
    """Minimize a GREEN patch to a 1-minimal still-GREEN sub-patch.

    Returns ``smaller_patch``: a new list holding a subset of ``patch``
    in input order such that ``still_green_fn(smaller_patch)`` is
    truthy and removing any single item turns the predicate falsy.
    """
    if not isinstance(patch, (list, tuple)):
        raise ValueError(
            "patchmin: patch must be a list/tuple of patch items, "
            "got %r" % (patch,))
    if not callable(still_green_fn):
        raise ValueError(
            "patchmin: still_green_fn must be callable, got %r"
            % (still_green_fn,))
    current = list(patch)
    if not current:
        return []
    if not still_green_fn(list(current)):
        raise ValueError(
            "patchmin: input patch itself is not GREEN; "
            "nothing to minimize")
    # ddmin: try dropping ever-finer complements until removing any
    # single item breaks GREEN (1-minimality).
    granularity = 2
    while len(current) >= 2:
        chunk_size = int(math.ceil(len(current) / granularity))
        reduced = False
        for start in range(0, len(current), chunk_size):
            candidate = (current[:start]
                         + current[start + chunk_size:])
            if still_green_fn(candidate):
                current = candidate
                granularity = max(granularity - 1, 2)
                reduced = True
                break
        if not reduced:
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity * 2)
    return current
