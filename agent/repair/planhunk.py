#!/usr/bin/env python3
"""Task C2: Multi-hunk planner (plan item 22, Wave C FIX BETTER).

Plan-then-diff validation: the model emits a PLANNED HUNKS list
(file:line pairs) plus a unified diff; the loop validates every
planned hunk landed via ``validate_landed``.

Interface::

  parse_plan(text) -> list[str]
  validate_landed(plan, applied_diff) -> {"landed_all": bool,
                                          "missing": [hunks...]}

``parse_plan`` scans ``text`` for a ``PLANNED HUNKS`` section and
returns the ``file:line`` pairs in encounter order (e.g.
``["picorv32.v:373", "picorv32.v:376"]``). Accepted entry shapes::

  PLANNED HUNKS:
  - picorv32.v:373
  - picorv32.v:376

(leading ``-``/``*`` bullets optional, ``file:line`` may be embedded
in a longer line such as ``hunk picorv32.v:373 -- mem_xfer fix``).
Anything without a ``PLANNED HUNKS`` marker, with a marker but no
``file:line`` pairs, empty/whitespace-only, or a non-string raises
ValueError.

``validate_landed`` checks each planned ``file:line`` hunk against
the applied unified diff by @@-header range coverage: the target file
is resolved from ``diff --git``/``+++`` lines and each
``@@ -a,b +c,d @@`` header contributes its new-side range
``[c, c+d-1]`` (``d`` defaults to 1 when omitted); a hunk counts as
landed iff its file matches a diff file section and its line falls in
one of that file's new-side ranges. Returns
``{"landed_all": bool, "missing": [...]}`` where ``missing`` holds
exactly the planned hunks with no covering range, in plan order.
An empty/malformed plan, or applied text that is not a unified diff
(no ``diff --git`` / ``@@`` hunk header), raises ValueError.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``re`` only); no API, no keys, no network, no I/O.
"""

import re

# "PLANNED HUNKS" section marker (case-insensitive, optional colon).
_SECTION = re.compile(r"planned\s+hunks\s*:?", re.IGNORECASE)

# A hunk entry: path with an extension, colon, 1-based line number.
# e.g. "picorv32.v:373", "rtl/uart.v:42", "./sim/x.sv:7".
_HUNK = re.compile(r"([A-Za-z0-9_][\w\-./]*\.\w+):(\d+)")

# Start of the attached diff body: the plan section ends where the
# diff begins, so pairs inside the diff must not be re-collected.
_DIFF_START = re.compile(
    r"diff\s+--git\s+|@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")

# Minimal unified-diff fingerprint: a git diff header or an @@ hunk
# header. Plain prose (even prose repeating "file:line") is rejected.
_DIFF_GIT = re.compile(r"^\s*diff\s+--git\s+", re.MULTILINE)
_HUNK_HEADER = re.compile(r"@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")

# Range-coverage parsing: target file per diff section + new-side
# ranges from @@ headers.
_DIFF_GIT_FILE = re.compile(r"^\s*diff\s+--git\s+\S+\s+(\S+)\s*$")
_PLUSPLUS_FILE = re.compile(r"^\+\+\+\s+(\S+)")
_HUNK_RANGE = re.compile(
    r"@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def _require_plan_list(plan):
    """Validate the plan argument; return a list copy or raise."""
    if not isinstance(plan, (list, tuple)) or not plan:
        raise ValueError(
            "planhunk: plan must be a non-empty list of "
            "'file:line' strings, got %r" % (plan,))
    out = []
    for entry in plan:
        if not isinstance(entry, str) or not _HUNK.search(entry):
            raise ValueError(
                "planhunk: plan entries must be 'file:line' strings, "
                "got %r" % (entry,))
        out.append(entry)
    # Normalise entries to the bare "file:line" pair so callers may
    # pass either bare pairs or longer lines embedding a pair.
    return [_HUNK.search(entry).group(0) for entry in out]


def parse_plan(text):
    """Extract the PLANNED HUNKS (file:line) list from model text."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "planhunk: text must be a non-empty string, got %r" % (text,))
    marker = _SECTION.search(text)
    if marker is None:
        raise ValueError(
            "planhunk: no PLANNED HUNKS section found")
    tail = text[marker.end():]
    cut = _DIFF_START.search(tail)
    if cut is not None:
        tail = tail[:cut.start()]
    pairs = []
    for match in _HUNK.finditer(tail):
        pair = match.group(0)
        if pair not in pairs:  # keep encounter order, drop repeats
            pairs.append(pair)
    if not pairs:
        raise ValueError(
            "planhunk: PLANNED HUNKS section holds no file:line pairs")
    return pairs


def validate_landed(plan, applied_diff):
    """Check every planned hunk is covered by a diff @@ new-side range."""
    hunks = _require_plan_list(plan)
    if not isinstance(applied_diff, str) or not applied_diff.strip():
        raise ValueError(
            "planhunk: applied_diff must be a non-empty unified diff "
            "string, got %r" % (applied_diff,))
    if not (_DIFF_GIT.search(applied_diff)
            or _HUNK_HEADER.search(applied_diff)):
        raise ValueError(
            "planhunk: applied text is not a unified diff "
            "(no 'diff --git' or '@@' hunk header)")
    wanted = []
    for h in hunks:
        m = _HUNK.search(h)
        wanted.append((m.group(1), int(m.group(2))))
    sections = _parse_diff_sections(applied_diff)
    missing = [
        h for h, (fname, lineno) in zip(hunks, wanted)
        if not _is_covered(fname, lineno, sections)
    ]
    return {"landed_all": not missing, "missing": missing}


def _norm_path(path):
    """Strip a/ b/ prefixes and ./ so plan and diff paths compare."""
    p = path.strip()
    if p.startswith("./"):
        p = p[2:]
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    if p == "/dev/null":
        return p
    return p


def _files_match(planned, section):
    """Exact normalized match, or suffix match on a path boundary."""
    a = _norm_path(planned)
    b = _norm_path(section)
    if a == b:
        return True
    return (b.endswith("/" + a) or a.endswith("/" + b))


def _parse_diff_sections(applied_diff):
    """Split a unified diff into [(file_or_None, [(start, end), ...])].

    Each ``@@ -a,b +c,d @@`` header contributes the new-side range
    ``[c, c+d-1]`` (``d`` defaults to 1; ``d == 0`` covers nothing)
    to the most recent ``diff --git``/``+++`` file section. Headers
    before any file header go under ``None`` (file-agnostic, so a
    bare ``@@``-only diff still validates by line range).
    """
    sections = []  # list of [file_or_None, [(start, end), ...]]
    current = None  # index into sections, or None if no section yet

    def _ensure(slot):
        if slot is None or sections[slot][0] != current:
            sections.append([current, []])
            return len(sections) - 1
        return slot

    slot = None
    for line in applied_diff.splitlines():
        m = _DIFF_GIT_FILE.match(line)
        if m:
            target = _norm_path(m.group(1))
            current = None if target == "/dev/null" else target
            slot = None
            continue
        m = _PLUSPLUS_FILE.match(line.lstrip())
        if m:
            target = _norm_path(m.group(1))
            # "/dev/null" (deletion) carries no new-side file; keep the
            # current section file so @@ ranges are not misattributed.
            if target != "/dev/null":
                current = target
            slot = None
            continue
        m = _HUNK_RANGE.search(line)
        if m:
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            if new_count <= 0:
                continue  # pure deletion hunk: no new-side lines
            slot = _ensure(slot)
            sections[slot][1].append(
                (new_start, new_start + new_count - 1))
    return [(fname, ranges) for fname, ranges in sections]


def _is_covered(fname, lineno, sections):
    """A hunk is landed iff its line falls in a matching file's range."""
    for section_file, ranges in sections:
        if section_file is not None and not _files_match(fname, section_file):
            continue
        for start, end in ranges:
            if start <= lineno <= end:
                return True
    return False
