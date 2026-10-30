#!/usr/bin/env python3
"""Task F7: Diagnosis memory (plan item 67, Wave F EVIDENCE).

Remember failed diagnosis lines so the next proposal differs.

Interfaces:
  record(attempt_log, diagnosis) -> None (appends in place)
  avoid(attempt_log) -> [banned_lines] (deduped ints, first-seen order)

attempt_log: a caller-owned list of failed diagnosis attempts. Each
  entry is either a positive-int line number or a dict with a
  positive-int "line" key (extra keys such as "file" are preserved
  in the log and ignored by avoid()). Every recorded entry counts
  as FAILED by definition (plan anchor: a recorded line-1446
  diagnosis bans 1446 from the next proposal).

avoid() returns the deduped failed lines in first-seen order so the
  caller can filter the next proposal (next proposal must differ
  from every banned line). Empty log returns []. The log is never
  mutated by avoid() and no lines are ever invented.

Default-off contract (Wave F header: additive + default-off): this
  module runs ONLY on explicit call. It imports nothing and no caller
  is wired: agent/loop.py and agent/guard.py are untouched.

Honesty contract: banned lines come ONLY from the log entries --
  never guessed. Misshapen entries raise ValueError (caller bug,
  never a guess).

Pure stdlib (no imports); no API, no keys, no network, no I/O.
"""


def _as_line(diagnosis):
    """Positive-int line for an int or {'line': ...} diagnosis."""
    if isinstance(diagnosis, bool):
        raise ValueError("diagnosis line must be a positive int, "
                         "got %r" % (diagnosis,))
    if isinstance(diagnosis, int):
        if diagnosis <= 0:
            raise ValueError("diagnosis line must be a positive int, "
                             "got %r" % (diagnosis,))
        return diagnosis
    if isinstance(diagnosis, dict):
        if "line" not in diagnosis:
            raise ValueError("diagnosis dicts need a 'line' key, "
                             "got %r" % (diagnosis,))
        line = diagnosis["line"]
        if (isinstance(line, bool) or not isinstance(line, int)
                or line <= 0):
            raise ValueError("diagnosis line must be a positive int, "
                             "got %r" % (line,))
        return line
    raise ValueError("diagnosis must be a positive-int line or a "
                     "{'line': ...} dict, got %r" % (diagnosis,))


def record(attempt_log, diagnosis):
    """Append a FAILED diagnosis to attempt_log (in place, returns None).

    attempt_log must be a list; diagnosis must be a positive-int
    line or a {'line': positive-int, ...} dict. The entry object
    itself is appended (dicts by reference, like churnprior.py
    keeps item identity); nothing is copied or rewritten.
    """
    if not isinstance(attempt_log, list):
        raise ValueError("attempt_log must be a list, got %r"
                         % (attempt_log,))
    _as_line(diagnosis)  # validate before appending: no junk in log
    attempt_log.append(diagnosis)
    return None


def avoid(attempt_log):
    """Return [banned_lines] already tried and failed (first-seen order).

    Dedupes repeated lines to a single ban each. Raises ValueError
    on a non-list log or any misshapen entry. Never mutates the log.
    """
    if not isinstance(attempt_log, list):
        raise ValueError("attempt_log must be a list, got %r"
                         % (attempt_log,))
    banned = []
    seen = set()
    for entry in attempt_log:
        line = _as_line(entry)
        if line not in seen:
            seen.add(line)
            banned.append(line)
    return banned
