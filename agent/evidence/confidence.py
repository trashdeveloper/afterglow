#!/usr/bin/env python3
"""Task B9: Confidence calibration (plan item 19, Wave B SEE MORE).

Model self-reports fix confidence 1-5 via a ``CONFIDENCE: N`` marker;
callers budget follow-up attempts proportionally (low confidence ->
more attempts, never fewer than 1 total attempt).

Interface::

  parse_confidence(text) -> int 1-5 | None
  budget_for_confidence(conf, base_attempts) -> int total attempts

``parse_confidence`` extracts the LAST ``CONFIDENCE: N`` marker from
model output (case-insensitive, flexible whitespace). Missing or
unparseable markers (prose without the marker, out-of-range digits,
decimals, bare keyword) return None -- the parser never invents a
level. Non-string input raises ValueError (caller bug, never a guess).

``budget_for_confidence`` maps 5->base, 4->base, 3->base+1, 2->base+2,
1->base+3, and None->base+1 (unknown confidence budgets conservatively:
one extra attempt). The result floors at 1 (never fewer than 1 total
attempt). Confidence outside {1..5, None} or a non-int/negative base
raises ValueError.

Default-off contract (Wave B header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py and no caller is wired: agent/loop.py is untouched.

Pure stdlib (re); no API, no keys, no network, no I/O.
"""
import re

_VERSION = "1"

# The marker instruction given to the model asks for exactly
# "CONFIDENCE: N". The digit class is one char [1-5] with a trailing
# guard (?![\d.]) so multi-digit runs ("42", "10") and decimals
# ("3.5") do NOT match a leading in-range digit -- those are
# unparseable and must yield None, not a truncated invention.
_MARKER = re.compile(r"CONFIDENCE\s*:\s*([1-5])(?![\d.])",
                     re.IGNORECASE)

# Extra attempts over base per reported confidence level.
_EXTRA = {5: 0, 4: 0, 3: 1, 2: 2, 1: 3}

# Conservative budget for unknown confidence (None): one extra attempt.
_UNKNOWN_EXTRA = 1


def parse_confidence(text):
    """Extract the model's self-reported confidence 1-5, or None.

    Returns the LAST ``CONFIDENCE: N`` marker in text (reasoning may
    mention an early guess; the final self-report is authoritative).
    Missing/unparseable input -> None. Non-string input -> ValueError.
    """
    if not isinstance(text, str):
        raise ValueError(
            "confidence: text must be a string, got %r"
            % (type(text),))
    found = _MARKER.findall(text)
    if not found:
        return None
    return int(found[-1])


def budget_for_confidence(conf, base_attempts):
    """Map reported confidence + base attempts -> total attempt budget.

    5/4 -> base, 3 -> base+1, 2 -> base+2, 1 -> base+3, None -> base+1.
    Floors at 1. Invalid conf or base raises ValueError.
    """
    if isinstance(base_attempts, bool) or \
            not isinstance(base_attempts, int) or base_attempts < 0:
        raise ValueError(
            "confidence: base_attempts must be a non-negative int, "
            "got %r" % (base_attempts,))
    if conf is None:
        extra = _UNKNOWN_EXTRA
    elif isinstance(conf, bool) or not isinstance(conf, int) \
            or conf not in _EXTRA:
        raise ValueError(
            "confidence: conf must be an int 1-5 or None, got %r"
            % (conf,))
    else:
        extra = _EXTRA[conf]
    return max(1, base_attempts + extra)
