#!/usr/bin/env python3
"""Task E10: Finder portfolio board (plan item 60, Wave E FIND BUGS).

Aggregates finder-task results into a ranked table plus a summary,
and (plan item-60 compat) attributes each found fault to the finder
that found it first.

Parent interfaces (binding):
  board(entries) -> [entry...]
    entries are {task_id, tool, seed, exposed, steps, tokens};
    ranking is exposed-first, then fewer steps, then fewer tokens.
    Ties after that break by str(task_id) asc so the order is
    deterministic regardless of input permutation.
  summary(entries) -> {exposed_count, total, tokens_spent}
    exposed_count = entries with exposed True; total = len(entries);
    tokens_spent = sum of tokens.

Plan interfaces (item-60 compat):
  attribute(fault_id, finder, board) -> board
    first-wins dedup: a fault already credited keeps its finder.
  leaders(board) -> [(finder, count)]
    ranked by count desc, ties by str(finder) asc.

Honesty contract:
  - Entries must be dicts with all six keys; bad shapes, bad types
    (bool rejected where int/str/bool is expected), and negative
    steps/tokens all raise loudly -- never silently ranked/skipped.
  - Inputs are never mutated; board() returns a NEW list of NEW dicts.
  - attribute() mutates the passed-in board dict in place (it IS the
    board) and returns it for chaining; it never re-credits a fault.

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing (not even
stdlib), never touches agent/loop.py or agent/guard.py, writes no
files (board/summary return values only; attribute writes only the
injected board dict), and no caller is wired.

Pure stdlib (zero imports); no API, no keys, no network.
"""

__all__ = ["board", "summary", "attribute", "leaders"]

_REQUIRED_KEYS = ("task_id", "tool", "seed", "exposed", "steps", "tokens")


def _require_task_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError(
            "task_id must be a str/int id, got %r: refusing to "
            "board a non-task" % (value,))
    if isinstance(value, str) and not value:
        raise ValueError(
            "task_id must be non-empty, got %r: refusing to board "
            "an undescribed task" % (value,))
    return value


def _require_tool(value):
    if not isinstance(value, str) or not value:
        raise TypeError(
            "tool must be a non-empty str, got %r: refusing to "
            "board a non-tool result" % (value,))
    return value


def _require_seed(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            "seed must be an int, got %r: refusing to board a "
            "non-seed result" % (value,))
    return value


def _require_exposed(value):
    if not isinstance(value, bool):
        raise TypeError(
            "exposed must be a bool, got %r: refusing to board an "
            "undecided result" % (value,))
    return value


def _require_count(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            "%s must be an int, got %r: refusing to board a "
            "non-count result" % (name, value))
    if value < 0:
        raise ValueError(
            "%s must be >= 0, got %d: refusing to board a negative "
            "count" % (name, value))
    return value


def _require_entry(entry):
    if not isinstance(entry, dict):
        raise TypeError(
            "entries must be dicts, got %r: refusing to board a "
            "non-entry" % (entry,))
    for key in _REQUIRED_KEYS:
        if key not in entry:
            raise KeyError(
                "entry missing required key %r: refusing to board "
                "an incomplete entry" % (key,))
    _require_task_id(entry["task_id"])
    _require_tool(entry["tool"])
    _require_seed(entry["seed"])
    _require_exposed(entry["exposed"])
    _require_count(entry["steps"], "steps")
    _require_count(entry["tokens"], "tokens")
    return entry


def _require_entries(entries):
    if not isinstance(entries, (list, tuple)):
        raise TypeError(
            "entries must be a list/tuple, got %r: refusing to "
            "board without an entry list" % (entries,))
    for entry in entries:
        _require_entry(entry)
    return entries


def board(entries):
    """Rank finder-task entries: exposed first, then steps, tokens.

    entries: list/tuple of {task_id, tool, seed, exposed, steps,
      tokens} dicts. Returns a NEW list of NEW dicts ordered by
    (unexposed-last, steps asc, tokens asc, str(task_id) asc).
    Inputs are never mutated.
    """
    _require_entries(entries)
    ranked = sorted(
        entries,
        key=lambda e: (0 if e["exposed"] else 1, e["steps"],
                       e["tokens"], str(e["task_id"])),
    )
    return [dict(e) for e in ranked]


def summary(entries):
    """Summarize finder-task entries.

    Returns {exposed_count, total, tokens_spent}: the number of
    entries with exposed True, the total entry count, and the sum
    of tokens. Accepts the raw entry list or board() output.
    """
    _require_entries(entries)
    return {
        "exposed_count": sum(1 for e in entries if e["exposed"]),
        "total": len(entries),
        "tokens_spent": sum(e["tokens"] for e in entries),
    }


def _require_board_dict(board_dict):
    if not isinstance(board_dict, dict):
        raise TypeError(
            "board must be a {fault_id: finder} dict, got %r: "
            "refusing to attribute without a board" % (board_dict,))
    return board_dict


def attribute(fault_id, finder, board_dict):
    """Credit fault_id to finder on the board (first-wins dedup).

    A fault already present keeps its original finder; the second
    report is dropped. Mutates board_dict in place and returns it.
    """
    _require_board_dict(board_dict)
    _require_task_id(fault_id)
    _require_tool(finder)
    if fault_id not in board_dict:
        board_dict[fault_id] = finder
    return board_dict


def leaders(board_dict):
    """Rank finders by credited fault count.

    board_dict: {fault_id: finder}. Returns [(finder, count)...]
    ordered by count desc, ties by str(finder) asc.
    """
    _require_board_dict(board_dict)
    counts = {}
    for _fault_id, finder in board_dict.items():
        _require_tool(finder)
        counts[finder] = counts.get(finder, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
