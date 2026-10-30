#!/usr/bin/env python3
"""Task G5: Auto-bisect on regression (plan item 75).

Produces::

  bisect_regression(failing_entry, history, is_bad_fn=None)
    -> culprit_commit

A regguard alarm triggers a bisect over recent commits to find the
breaking change. ``failing_entry`` is one
``agent/trust/regguard.py`` registry entry (``{case, checksum, ...}``);
it is validated read-only -- never mutated, never persisted, and this
module never imports regguard (standalone reader; no regguard/loop
wiring). ``history`` is oldest -> newest commit ids (non-empty
strings). The return is the bare first-bad commit id (the history
element itself).

``is_bad_fn(commit)`` is the $0 test seam: truthy while that commit
reproduces the recorded failure, falsy once it passes. Probe
exceptions propagate unchanged -- never caught, never a verdict.

Algorithm: classic first-bad binary search. The baseline
``history[0]`` must be good and the tip ``history[-1]`` bad (both
probed); otherwise ValueError loudly (bad baseline = culprit predates
the window; good tip = vacuous, nothing reproduces). Total probes are
at most ``2 + ceil(log2(n-1))`` for ``n >= 2`` (1 for ``n == 1``).

Honesty contract (mirrors ``agent/discover/bisect.py``: vacuous input
raises, probe errors propagate, bounded probes -- adapted from
stimulus-ddmin to commit-bisect because ddmin's subset semantics
converge to the tip rather than the first-bad commit and take linear
probes, which would break the graded log2 bound).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py, agent/guard.py, or agent/trust/regguard.py, never
changes guard verdicts, and no caller is wired: agent/loop.py and
agent/guard.py are untouched.

Pure stdlib (no imports); no API, no keys, no network, no I/O.
"""


def _check_entry(failing_entry):
    """Validate the regguard-format entry shape (read-only)."""
    if not isinstance(failing_entry, dict):
        raise TypeError(
            "bisectreg: failing_entry must be a regguard registry dict "
            "with 'case' + 'checksum', got %r" % (failing_entry,))
    for key in ("case", "checksum"):
        value = failing_entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "bisectreg: failing_entry[%r] must be a non-empty "
                "string, got %r" % (key, value))


def _check_history(history):
    """Validate oldest -> newest commit-id list, return it as a list."""
    if not isinstance(history, (list, tuple)):
        raise TypeError(
            "bisectreg: history must be a list/tuple of commit ids "
            "(oldest -> newest), got %r" % (history,))
    commits = list(history)
    if not commits:
        raise ValueError("bisectreg: history is empty: nothing to bisect")
    for commit in commits:
        if not isinstance(commit, str) or not commit.strip():
            raise ValueError(
                "bisectreg: history commits must be non-empty strings, "
                "got %r" % (commit,))
    return commits


def bisect_regression(failing_entry, history, is_bad_fn=None):
    """Bisect a commit window for the first commit that reproduces it.

    Returns the culprit commit id (the ``history`` element itself).
    Raises ValueError on vacuous input (good tip, bad baseline,
    empty/malformed history or entry); probe exceptions propagate.
    """
    _check_entry(failing_entry)
    commits = _check_history(history)
    if not callable(is_bad_fn):
        raise TypeError(
            "bisectreg: is_bad_fn must be a callable probe "
            "taking a commit id, got %r" % (is_bad_fn,))

    if len(commits) == 1:
        if is_bad_fn(commits[0]):  # raises propagate, never a verdict
            return commits[0]
        raise ValueError(
            "bisectreg: single-commit history %r does not reproduce "
            "the failure: vacuous input, refusing to invent a culprit"
            % (commits[0],))

    if is_bad_fn(commits[0]):  # raises propagate, never a verdict
        raise ValueError(
            "bisectreg: baseline %r already reproduces the failure: "
            "the culprit predates this window" % (commits[0],))
    if not is_bad_fn(commits[-1]):  # raises propagate, never a verdict
        raise ValueError(
            "bisectreg: tip %r does not reproduce the failure: "
            "vacuous input, refusing to invent a culprit"
            % (commits[-1],))

    lo, hi = 0, len(commits) - 1  # commits[lo] good, commits[hi] bad
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if is_bad_fn(commits[mid]):  # raises propagate, never a verdict
            hi = mid
        else:
            lo = mid
    return commits[hi]
