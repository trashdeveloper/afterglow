#!/usr/bin/env python3
"""Task F2: Cone-intersection diagnosis (plan item 62).

Intersection of per-signal/per-cone suspect sets: the lines blamed
by EVERY cone rank first (they are the whole result).

Interface::

  intersect(cones) -> [line, ...]
  coverage(cones) -> float  # 0.0-1.0

``cones`` is a list/tuple of iterables of suspect lines (or a dict
mapping cone id -> iterable of lines; dict insertion order defines
the first-cone order). ``intersect`` returns the lines present in
ALL cones, ordered by first appearance in the first cone, with
duplicates dropped. ``coverage`` is ``|intersection| / |union|``
(Jaccard-style: the fraction of union lines present in ALL cones);
0.0 when the union is empty, 1.0 when every line is shared.

Errors are loud: a non-list/non-dict ``cones``, or a cone entry
that is a string or non-iterable, raises TypeError. Empty ``cones``
returns [] / 0.0.

Default-off contract: this module runs ONLY on explicit call. It
imports nothing from agent/loop.py and no caller is wired:
agent/loop.py is untouched.

Pure stdlib (no imports at all); no API, no keys, no network, no I/O.
"""

_VERSION = "1"


def _cone_lists(cones):
    """Normalize cones to a list of lists; raise TypeError on bad input."""
    if isinstance(cones, dict):
        raw = list(cones.values())
    elif isinstance(cones, (list, tuple)):
        raw = list(cones)
    else:
        raise TypeError(
            "conecut: cones must be a list/tuple/dict, got %r"
            % (type(cones),))
    out = []
    for i, cone in enumerate(raw):
        if isinstance(cone, (str, bytes)):
            raise TypeError(
                "conecut: cone %d must be an iterable of lines, "
                "not a string" % (i,))
        try:
            out.append(list(cone))
        except TypeError:
            raise TypeError(
                "conecut: cone %d must be an iterable, got %r"
                % (i, type(cone)))
    return out


def intersect(cones):
    """Return lines present in ALL cones, in first-cone order, deduped."""
    lists = _cone_lists(cones)
    if not lists:
        return []
    members = [set(lines) for lines in lists]
    shared = set.intersection(*members)
    ranked = []
    for line in lists[0]:
        if line in shared and line not in ranked:
            ranked.append(line)
    return ranked


def coverage(cones):
    """Return |intersection| / |union| (0.0 when the union is empty)."""
    lists = _cone_lists(cones)
    union = set()
    for lines in lists:
        union.update(lines)
    if not union:
        return 0.0
    return len(set.intersection(*[set(lines) for lines in lists])) / len(union)
