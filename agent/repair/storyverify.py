#!/usr/bin/env python3
"""Task G8: Story-vs-diff verifier (plan item 78).

Produces::

  verify(story, patch) -> True | {"lies": [...]}

A ``diffstory`` sentence (``agent/trust/diffstory.py``) is checked
against the actual diff semantics: each named signal + direction is
verified against the real hunk re-derived from ``patch``. A story
claiming an inversion the diff does not contain is rejected naming
the lie; a true story passes.

``story`` is a diffstory-format dict (the shape returned by
``diffstory.story_from_patch``): ``{"files", "hunks", "sentences",
"text"}`` where each hunk is ``{"file", "line", "signals",
"direction"}``. Extra top-level keys (``sentences``, ``text``) are
accepted and ignored -- only ``hunks`` is checked. A claimed hunk
may omit ``line`` (then the line is not checked); when present as
an int it must equal the actual hunk line.

``patch`` is the unified diff string the story claims to explain.
The truth is re-derived with ``diffstory.story_from_patch(patch)``
(no hand-typed oracle); a patch with no hunks raises (propagated
from diffstory) -- a story is never verified against a vacuous diff.

Checks, in order:

1. hunk count: claimed ``len(story["hunks"])`` must equal the actual
   hunk count, else a single count lie.
2. per hunk ``i`` (1-based in lie strings):
   - ``file`` must equal the actual file, else a file lie;
   - ``line`` (when present) must equal the actual line, else a line
     lie;
   - ``signals`` must be a non-empty list of non-blank strings and
     every named signal must appear in the actual hunk signals
     (claimed subset of actual, exact case-sensitive match --
     Verilog is): a ghost signal is a lie naming it; an empty list
     is a lie of omission (a story naming no signals explains
     nothing, so it never passes vacuously);
   - ``direction`` must match the actual direction: exact match after
     strip, or category-level (one normalized string contains the
     other, case-folded), so a summarized ``"inverted"`` matches
     ``"inverted (`==` -> `!=`)"`` but never matches
     ``"set to constant ..."``.

Returns the singleton ``True`` when every check passes, else
``{"lies": [str, ...]}`` with one entry per mismatch, each naming
the hunk number plus the claimed vs actual values.

Vacuous input raises loudly before any verdict (never an invented
pass): non-dict story, missing/non-list/empty hunks, malformed hunk
entries (missing file/signals/direction, non-string signals, blank
signals, non-int line), non-string/blank patch. ``diffstory``
errors on hunk-less patches propagate unchanged.

Honesty contract (mirrors ``agent/repair/repairrace.py``: vacuous
input raises, deterministic, read-only -- adapted from
strategy-race to story-check because a story is verified against
its own diff, not raced against alternatives, so the graded shape
here is "lying inversion rejected naming the lie, true story
passes" instead of first-green portfolio order).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and
no caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib + one repo import (``agent/trust/diffstory.py``); no
API, no keys, no network, no I/O.
"""

try:
    from agent.trust import diffstory
except ImportError:  # pragma: no cover - fallback when agent/ is sys.path[0]
    from trust import diffstory  # noqa: F401


def _norm_direction(text):
    """Normalize a direction string for category-level comparison."""
    return " ".join(str(text).strip().casefold().split())


def _direction_matches(claimed, actual):
    """True when the claimed direction describes the actual hunk."""
    norm_claimed = _norm_direction(claimed)
    norm_actual = _norm_direction(actual)
    return (norm_claimed == norm_actual
            or norm_claimed in norm_actual
            or norm_actual in norm_claimed)


def _check_hunk_entry(entry, index):
    """Validate one claimed hunk entry, returning (file, line, signals,
    direction). ``line`` is None when omitted. Raises TypeError /
    ValueError on malformed entries."""
    label = "storyverify: story['hunks'][%d]" % index
    if not isinstance(entry, dict):
        raise TypeError(
            "%s must be a diffstory hunk dict with "
            "'file' + 'signals' + 'direction', got %r" % (label, entry))
    for key in ("file", "signals", "direction"):
        if key not in entry:
            raise ValueError(
                "%s is missing %r (diffstory hunk schema: "
                "'file' + 'line' + 'signals' + 'direction')" % (label, key))
    file_name = entry["file"]
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError(
            "%s['file'] must be a non-blank string, got %r"
            % (label, file_name))
    signals = entry["signals"]
    if not isinstance(signals, (list, tuple)):
        raise TypeError(
            "%s['signals'] must be a list of signal-name strings, "
            "got %r" % (label, signals))
    for signal in signals:
        if not isinstance(signal, str):
            raise TypeError(
                "%s['signals'] entries must be strings, got %r"
                % (label, signal))
        if not signal.strip():
            raise ValueError(
                "%s['signals'] entries must be non-blank, got %r"
                % (label, signal))
    direction = entry["direction"]
    if not isinstance(direction, str) or not direction.strip():
        raise ValueError(
            "%s['direction'] must be a non-blank string, got %r"
            % (label, direction))
    line = entry.get("line", None)
    if line is not None and (not isinstance(line, int)
                             or isinstance(line, bool)):
        raise TypeError(
            "%s['line'] must be an int hunk line (or omitted), "
            "got %r" % (label, line))
    return file_name, line, list(signals), direction


def verify(story, patch):
    """Verify a diffstory-format story against its patch diff.

    Returns True when every named signal + direction checks out
    against the actual hunks, else {"lies": [...]} with each
    mismatch named. Vacuous input raises TypeError / ValueError.
    Inputs are never mutated.
    """
    if not isinstance(story, dict):
        raise TypeError(
            "storyverify: story must be a diffstory dict with "
            "'hunks', got %r" % (story,))
    if "hunks" not in story:
        raise ValueError(
            "storyverify: story has no 'hunks' key (diffstory schema: "
            "'files' + 'hunks' + 'sentences' + 'text')")
    claimed_hunks = story["hunks"]
    if not isinstance(claimed_hunks, (list, tuple)):
        raise TypeError(
            "storyverify: story['hunks'] must be a list of hunk "
            "dicts, got %r" % (claimed_hunks,))
    if not claimed_hunks:
        raise ValueError(
            "storyverify: story['hunks'] is empty: a story naming no "
            "hunks verifies nothing (never a vacuous pass)")
    if not isinstance(patch, str):
        raise TypeError(
            "storyverify: patch must be a unified diff string, "
            "got %r" % (patch,))
    if not patch.strip():
        raise ValueError(
            "storyverify: patch is blank: a story cannot be verified "
            "against an empty diff")

    # Re-derive the truth from the actual diff (diffstory raises
    # ValueError on hunk-less patches -- propagated, never a verdict).
    truth = diffstory.story_from_patch(patch)
    actual_hunks = truth["hunks"]

    if len(claimed_hunks) != len(actual_hunks):
        return {"lies": [
            "hunk count mismatch: story claims %d hunk(s) but patch "
            "has %d hunk(s)" % (len(claimed_hunks),
                                len(actual_hunks))]}

    lies = []
    for index, (claimed, actual) in enumerate(zip(claimed_hunks,
                                                 actual_hunks)):
        hunk_no = index + 1
        file_name, line, signals, direction = _check_hunk_entry(
            claimed, index)
        if file_name != actual["file"]:
            lies.append(
                "hunk %d: file lie: story claims %r but patch hunk "
                "is in %r" % (hunk_no, file_name, actual["file"]))
        if line is not None and line != actual["line"]:
            lies.append(
                "hunk %d: line lie: story claims line %d but patch "
                "hunk is at line %d" % (hunk_no, line,
                                        actual["line"]))
        if not signals:
            lies.append(
                "hunk %d: story names no signals for a patch hunk "
                "touching %s (a story naming nothing explains "
                "nothing)" % (hunk_no,
                              ", ".join(actual["signals"])))
        else:
            for signal in signals:
                if signal not in actual["signals"]:
                    lies.append(
                        "hunk %d: signal lie: story names %r but "
                        "patch hunk signals are %s" % (
                            hunk_no, signal,
                            ", ".join(actual["signals"])))
        if not _direction_matches(direction, actual["direction"]):
            lies.append(
                "hunk %d: direction lie: story claims %r but patch "
                "hunk is %r" % (hunk_no, direction,
                                actual["direction"]))
    if lies:
        return {"lies": lies}
    return True
