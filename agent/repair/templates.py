#!/usr/bin/env python3
"""Task C1: Repair templates (plan item 21, Wave C FIX BETTER).

Guarded sketch-fill for 3 bug shapes:

* ``reset-missing`` -- a sequential ``always @(posedge clk)`` block
  with no reset branch. Fill adds ``if (!rst_n) q <= 0; else`` around
  the block's first register driver.
* ``off-by-one`` -- a counter boundary compare ``cnt == N`` (N >= 1)
  inside an ``if`` condition. Fill proposes the decremented bound
  ``cnt == N-1``.
* ``width-mismatch`` -- an assignment truncating a wider rhs signal
  into a narrower lhs signal (widths read from ``[msb:lsb]``
  declarations in the same window). Fill proposes an explicit slice
  ``lhs <= rhs[W-1:0];`` sized to the lhs width.

Interface::

  guard_reset_missing(lines) -> (fires, reason)
  fill_reset_missing(lines) -> new_lines
  guard_off_by_one(lines) -> (fires, reason)
  fill_off_by_one(lines) -> new_lines
  guard_width_mismatch(lines) -> (fires, reason)
  fill_width_mismatch(lines) -> new_lines
  guard(name, lines) -> (fires, reason)      # dispatcher
  fill(name, lines) -> new_lines              # dispatcher

``lines`` is a list (or tuple) of source lines; anything else raises
ValueError. ``guard`` returns ``(True, fire_reason)`` only for its own
shape and ``(False, refusal_reason)`` otherwise -- refusal reasons are
pinned strings the tests assert verbatim. ``fill`` applies ONLY when
its guard fires; otherwise it raises ValueError carrying the refusal
reason (a template never fires on another template's fixture).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``re`` only); no API, no keys, no network, no I/O.
"""

import re

TEMPLATE_NAMES = ("reset-missing", "off-by-one", "width-mismatch")

# Pinned refusal reasons (tests assert these verbatim).
RESET_NO_BLOCK = (
    "reset-missing: no sequential always @(posedge ...) block found")
RESET_PRESENT = "reset-missing: reset branch already present"
OBO_NONE = "off-by-one: no counter equality compare (== N) found"
WIDTH_NONE = (
    "width-mismatch: no truncating assignment "
    "(wider rhs into narrower lhs) found")

_ALWAYS_POSEDGE = re.compile(r"always\s*@\s*\(\s*posedge\s+\w+")
_RESET_BRANCH = re.compile(r"if\s*\(\s*!?\s*(rst\w*|reset\w*)")
_IF_EQ_NUM = re.compile(r"if\s*\([^)]*==\s*([1-9]\d*)")
_EQ_NUM = re.compile(r"==\s*([1-9]\d*)")
_DECL_RANGE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*(\w+)")
_PLAIN_ASSIGN = re.compile(
    r"^\s*(?:else\s+)?(\w+)\s*(<=|=)\s*(\w+)\s*;\s*$")
_NUMERIC = re.compile(r"^\d")
_NB_ASSIGN = re.compile(r"(\w+)\s*<=")


def _check_lines(lines):
    """Validate the window; return a list copy or raise ValueError."""
    if not isinstance(lines, (list, tuple)) or not lines:
        raise ValueError(
            "templates: lines must be a non-empty list of strings, "
            "got %r" % (lines,))
    for ln in lines:
        if not isinstance(ln, str):
            raise ValueError(
                "templates: lines must be a non-empty list of strings, "
                "got element %r" % (ln,))
    return list(lines)


def guard_reset_missing(lines):
    """Fire iff a sequential always block lacks a reset branch."""
    lines = _check_lines(lines)
    block_at = None
    for i, ln in enumerate(lines):
        if _ALWAYS_POSEDGE.search(ln):
            block_at = i
            break
    if block_at is None:
        return (False, RESET_NO_BLOCK)
    for ln in lines:
        if _RESET_BRANCH.search(ln):
            return (False, RESET_PRESENT)
    return (True,
            "reset-missing: fires: sequential block at line %d "
            "without reset branch" % (block_at,))


def fill_reset_missing(lines):
    """Add ``if (!rst_n) q <= 0; else`` around the first driver."""
    lines = _check_lines(lines)
    fires, reason = guard_reset_missing(lines)
    if not fires:
        raise ValueError(reason)
    block_at = next(i for i, ln in enumerate(lines)
                    if _ALWAYS_POSEDGE.search(ln))
    target = None
    for i in range(block_at + 1, len(lines)):
        m = _NB_ASSIGN.search(lines[i])
        if m:
            target = (i, m.group(1))
            break
    if target is None:  # pragma: no cover - guard fired, so rare
        raise ValueError(RESET_NO_BLOCK)
    i, reg = target
    indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    out = list(lines)
    out[i] = "%selse %s" % (indent, lines[i].lstrip())
    out.insert(i, "%sif (!rst_n) %s <= 0;" % (indent, reg))
    return out


def guard_off_by_one(lines):
    """Fire iff an ``if (... == N)`` counter bound (N >= 1) exists."""
    lines = _check_lines(lines)
    for i, ln in enumerate(lines):
        m = _IF_EQ_NUM.search(ln)
        if m:
            return (True,
                    "off-by-one: fires: boundary compare == %s "
                    "at line %d" % (m.group(1), i))
    return (False, OBO_NONE)


def fill_off_by_one(lines):
    """Propose the decremented bound (``== N`` -> ``== N-1``)."""
    lines = _check_lines(lines)
    fires, reason = guard_off_by_one(lines)
    if not fires:
        raise ValueError(reason)

    def _dec(m):
        return "== %d" % (int(m.group(1)) - 1)

    return [_EQ_NUM.sub(_dec, ln) for ln in lines]


def _decl_widths(lines):
    """Map signal name -> bit width from ``[msb:lsb]`` declarations."""
    widths = {}
    for ln in lines:
        for m in _DECL_RANGE.finditer(ln):
            msb, lsb, name = int(m.group(1)), int(m.group(2)), m.group(3)
            widths.setdefault(name, abs(msb - lsb) + 1)
    return widths


def _first_truncation(lines):
    """Return (line_index, lhs, rhs, lhs_width) of the first truncating
    plain-identifier assignment, or None."""
    widths = _decl_widths(lines)
    for i, ln in enumerate(lines):
        m = _PLAIN_ASSIGN.match(ln)
        if not m:
            continue
        lhs, rhs = m.group(1), m.group(3)
        if _NUMERIC.match(rhs) or "'" in rhs:
            continue  # unsized/sized literal rhs: width unknown
        if lhs in widths and rhs in widths and widths[rhs] > widths[lhs]:
            return (i, lhs, rhs, widths[lhs])
    return None


def guard_width_mismatch(lines):
    """Fire iff a wider rhs is assigned into a narrower lhs."""
    lines = _check_lines(lines)
    hit = _first_truncation(lines)
    if hit is None:
        return (False, WIDTH_NONE)
    i, lhs, rhs, _ = hit
    return (True,
            "width-mismatch: fires: %d-bit %s assigned into "
            "%d-bit %s at line %d"
            % (_decl_widths(lines)[rhs], rhs,
               _decl_widths(lines)[lhs], lhs, i))


def fill_width_mismatch(lines):
    """Propose an explicit slice sized to the lhs width."""
    lines = _check_lines(lines)
    fires, reason = guard_width_mismatch(lines)
    if not fires:
        raise ValueError(reason)
    i, lhs, rhs, lhs_w = _first_truncation(lines)
    out = list(lines)
    out[i] = re.sub(r"\b%s\b" % re.escape(rhs),
                    "%s[%d:0]" % (rhs, lhs_w - 1), out[i], count=1)
    return out


_GUARDS = {
    "reset-missing": guard_reset_missing,
    "off-by-one": guard_off_by_one,
    "width-mismatch": guard_width_mismatch,
}

_FILLS = {
    "reset-missing": fill_reset_missing,
    "off-by-one": fill_off_by_one,
    "width-mismatch": fill_width_mismatch,
}


def guard(name, lines):
    """Dispatch to the named template's guard."""
    try:
        fn = _GUARDS[name]
    except KeyError:
        raise ValueError(
            "templates: unknown template %r (want one of %s)"
            % (name, ", ".join(TEMPLATE_NAMES)))
    return fn(lines)


def fill(name, lines):
    """Dispatch to the named template's fill (guarded)."""
    try:
        fn = _FILLS[name]
    except KeyError:
        raise ValueError(
            "templates: unknown template %r (want one of %s)"
            % (name, ", ".join(TEMPLATE_NAMES)))
    return fn(lines)
