#!/usr/bin/env python3
"""Task E7: Coverage-hole prioritizer (plan item 57, Wave E FIND BUGS).

Ranks uncovered lines so the most bug-likely hole is mutated first.

Interfaces:
  rank(uncovered_lines, toggles, fanout) -> [line...]
    score(line) = fanout.get(line, 0) * (1 + toggles.get(line, 0));
    returns the uncovered lines in descending score order. Ties
    break by str(line) asc so the order is deterministic for the
    same inputs regardless of input permutation.

Honesty contract:
  - uncovered_lines must be a list/tuple; entries must be str/int
    (bool rejected: it is not a line id). Anything else raises
    loudly -- never silently ranked or skipped.
  - toggles/fanout are {line: count} dicts (None means all zeros:
    every line scores 0). Non-dict maps, non-int counts (bool
    rejected), and negative counts all raise loudly -- never
    silently ranked on a non-count.
  - Missing entries default to 0 (unknown toggle history or fanout
    sinks to the bottom; never fail-open to the top).
  - Inputs are never mutated; a NEW list is returned.

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts,
writes no files (returns a list only), and no caller is wired.

Pure stdlib; no API, no keys, no network.
"""

__all__ = ["rank"]


def _require_line(line):
    """Validate one uncovered-line id; returns the line."""
    if isinstance(line, bool) or not isinstance(line, (str, int)):
        raise TypeError(
            "uncovered lines must be str/int ids, got %r: refusing "
            "to rank a non-line" % (line,))
    if isinstance(line, str) and not line:
        raise ValueError(
            "uncovered line ids must be non-empty, got %r: refusing "
            "to rank an undescribed line" % (line,))
    return line


def _require_counts(mapping, name):
    """Validate a {line: count} map; returns a plain copy. None -> {}."""
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise TypeError(
            "%s must be a {line: count} dict, got %r: refusing to "
            "rank on a non-map" % (name, mapping))
    clean = {}
    for line, count in mapping.items():
        _require_line(line)
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError(
                "%s count for line %r must be an int, got %r: "
                "refusing to rank on a non-count" % (name, line, count))
        if count < 0:
            raise ValueError(
                "%s count for line %r must be >= 0, got %d: refusing "
                "to rank on a negative count" % (name, line, count))
        clean[line] = int(count)
    return clean


def rank(uncovered_lines, toggles, fanout):
    """Rank uncovered lines by fanout x (1 + toggle history).

    uncovered_lines: list/tuple of str/int line ids (the holes to
      order). toggles: {line: toggle-count} dict (None = all zero).
      fanout: {line: fanout-count} dict (None = all zero).

    Returns a NEW list of the same line ids, ordered by descending
    score = fanout * (1 + toggles); ties break by str(line) asc.
    Lines missing from either map score with 0 for that factor.
    """
    if not isinstance(uncovered_lines, (list, tuple)):
        raise TypeError(
            "uncovered_lines must be a list/tuple, got %r: refusing "
            "to rank without a line list" % (uncovered_lines,))
    lines = [_require_line(line) for line in uncovered_lines]
    toggle_map = _require_counts(toggles, "toggles")
    fanout_map = _require_counts(fanout, "fanout")
    scored = [
        (fanout_map.get(line, 0) * (1 + toggle_map.get(line, 0)), line)
        for line in lines
    ]
    scored.sort(key=lambda pair: (-pair[0], str(pair[1])))
    return [line for _, line in scored]
