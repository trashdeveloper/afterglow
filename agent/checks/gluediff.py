#!/usr/bin/env python3
"""Task F5: Glue-logic vs RTL diff (plan item 65).

Line-set comparison ignoring whitespace-only differences and blank
lines.

Interface:
  diff(glue_lines, rtl_lines) -> {"glue_only": [...], "rtl_only": [...],
    "common": [...]}
    glue_lines / rtl_lines: list/tuple of str, one entry per line.
    Returns a dict of three sorted lists of normalized line text:
      "glue_only": normalized lines present in glue_lines but not in
        rtl_lines (e.g. an added wrapper line).
      "rtl_only": normalized lines present in rtl_lines but not in
        glue_lines (e.g. a line dropped by the glue).
      "common": normalized lines present in both.
    Normalization: strip leading/trailing whitespace, collapse each
    internal whitespace run to a single space. Lines that are empty
    after stripping (blank lines) are skipped entirely and never
    appear in any bucket. Comparison is set-based: duplicates
    collapse. Inputs are never mutated.
    Malformed inputs (non-list/tuple, non-string entries) raise
    TypeError loudly.

Notes: stdlib only, in-memory only (never writes files), no live API.
Deterministic: outputs are sorted lists.
"""
import re

_WS_RUN = re.compile(r"\s+")


def _normalize(line):
    """Normalize one line; return None for blank lines."""
    if not isinstance(line, str):
        raise TypeError("each line must be a str, got %r"
                        % type(line).__name__)
    collapsed = _WS_RUN.sub(" ", line.strip())
    if not collapsed:
        return None
    return collapsed


def _normalized_set(lines, name):
    if isinstance(lines, (str, bytes)) or not isinstance(lines, (list, tuple)):
        raise TypeError("%s must be a list/tuple of str, got %r"
                        % (name, type(lines).__name__))
    result = set()
    for line in lines:
        norm = _normalize(line)
        if norm is not None:
            result.add(norm)
    return result


def diff(glue_lines, rtl_lines):
    """Diff glue logic lines against RTL lines as whitespace-insensitive sets."""
    glue = _normalized_set(glue_lines, "glue_lines")
    rtl = _normalized_set(rtl_lines, "rtl_lines")
    return {
        "glue_only": sorted(glue - rtl),
        "rtl_only": sorted(rtl - glue),
        "common": sorted(glue & rtl),
    }
