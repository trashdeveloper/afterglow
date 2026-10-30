#!/usr/bin/env python3
"""Task F6: Churn-prioritized diagnosis (plan item 66, Wave F EVIDENCE).

Recent git-churn lines get a tie-break bump over equally-scoring
diagnosis candidates.

Interfaces:
  break_ties(scored_lines, churn) -> ordered (new list, same items)

scored_lines: a list/tuple of dicts, each with:
  {"file" (optional non-empty str), "line" (positive int),
   "score" (int/float, not bool), ...extra keys preserved...}
churn: None (no churn info: every line recency 0) or a dict mapping
  a line identity to a numeric recency (higher = more recently
  churned = ranks first among equals). Accepted key shapes for an
  item with file F and line L (tried in this order):
    (F, L) tuple, "F:L" string, L int.
  A line with no entry ranks as recency 0 (never-churned, oldest).
  Line-identity keys come from agent/evidence/blame_ctx.py porcelain
  output entries {"file", "line", ...}; the recency VALUES are
  caller-provided (e.g. commit order rank, churn count) -- this
  module does no git, no I/O, and never invents recency.

Ordering: score DESC dominates; ties break by recency DESC; full
ties break deterministically by (file, line). Output is a NEW list
holding the SAME item objects (input order untouched, no copies,
no invented lines).

Default-off contract (Wave F header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing and no caller
is wired: agent/loop.py and agent/guard.py are untouched.

Honesty contract (mirrors pastfix.py: never invent evidence):
- Output items are exactly the input items, reordered -- never
  added, dropped, or rewritten.
- Missing churn entries rank 0 (a miss, not a guess).
- Misshapen items / non-numeric scores / non-numeric churn values
  raise ValueError (caller bug, never a guess).

Pure stdlib (no imports); no API, no keys, no network, no I/O.
"""


def _as_number(value, what):
    """Numeric (int/float, not bool) value or raise ValueError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a number (int/float, not bool), "
                         "got %r" % (what, value,))
    return value


def _item_identity(item):
    """(file_or_None, line) for a scored dict; ValueError when misshapen."""
    if not isinstance(item, dict):
        raise ValueError("scored_lines items must be dicts "
                         "({'file', 'line', 'score'}), got %r" % (item,))
    if "line" not in item or "score" not in item:
        raise ValueError("scored_lines items need 'line' and 'score' "
                         "keys, got %r" % (item,))
    line = item["line"]
    if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
        raise ValueError("line must be a positive int, got %r"
                         % (line,))
    fname = item.get("file")
    if fname is not None:
        if not isinstance(fname, str) or not fname:
            raise ValueError("file must be a non-empty string, got %r"
                             % (fname,))
    _as_number(item["score"], "score")
    return fname, line


def _recency(churn, fname, line):
    """Recency for (fname, line): tuple, 'file:line', int keys; else 0."""
    if churn is None:
        return 0
    if not isinstance(churn, dict):
        raise ValueError("churn must be a dict (or None), got %r"
                         % (churn,))
    candidates = []
    if fname is not None:
        candidates.append((fname, line))
        candidates.append("%s:%d" % (fname, line))
    candidates.append(line)
    for key in candidates:
        if key in churn:
            return _as_number(churn[key],
                              "churn recency for %r" % (key,))
    return 0


def break_ties(scored_lines, churn=None):
    """Reorder scored diagnosis lines, churn breaking equal-score ties.

    Highest score first; among equal scores the most recently
    churned line first; full ties in deterministic (file, line)
    order. Returns a NEW list (same item objects); inputs unmodified.
    Empty input returns []. None churn means no bump (all recency 0).
    """
    if scored_lines is None:
        raise ValueError("scored_lines must be a list of "
                         "{'file', 'line', 'score'} dicts, got None")
    try:
        items = list(scored_lines)
    except TypeError:
        raise ValueError("scored_lines must be a list/tuple of "
                         "{'file', 'line', 'score'} dicts, got %r"
                         % (scored_lines,))
    if churn is not None and not isinstance(churn, dict):
        raise ValueError("churn must be a dict (or None), got %r"
                         % (churn,))
    keyed = []
    for item in items:
        fname, line = _item_identity(item)
        recency = _recency(churn, fname, line)
        keyed.append((-item["score"], -recency, fname or "", line, item))
    keyed.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    return [t[4] for t in keyed]
