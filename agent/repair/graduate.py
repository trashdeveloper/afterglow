#!/usr/bin/env python3
"""Full-auto graduation recommender (Task G10, plan item 80, Wave G).

Standalone recommender over a confidence history: the last ``window``
entries decide ``"promote"`` (sustained good), ``"demote"`` (currently
failing), or ``"hold"`` (anything else).

Interface (plan text, verbatim)::

  recommend(history, threshold, window=10) ->
    "promote"|"hold"|"demote" (standalone recommender; no loop wiring --
    wiring needs a later explicit approval).

Rules (plan Step 1 test):

- 8/10 above threshold -> ``"promote"`` (generalized: fraction of the
  considered window at/above threshold >= 0.8, with a full window of
  data -- short histories can never promote).
- 2 consecutive below (the last two entries both strictly below
  threshold) -> ``"demote"``. Checked FIRST: a window that is 8/10
  above but ends in two failures demotes (the loop is currently
  failing -- promoting would be unsafe).
- Anything else -> ``"hold"``, including empty history (no data: the
  gate never invents a verdict).

Consumes the ``agent/trust/abstain.py`` policy shape: ``history``
entries are confidences in [0, 1] and ``threshold`` is in (0, 1] with
the same strict less-than semantics (confidence exactly AT the
threshold counts as above, matching abstain's "at threshold
proceeds"). Bools, non-numbers, out-of-range values, and NaN raise
ValueError; ``window`` must be a positive int (bools rejected);
``history`` must be a list or tuple (strings/dicts rejected).

Default-off contract (Wave G additive): this module runs ONLY on
explicit call. It is imported by nothing in agent/loop.py,
agent/guard.py, or eval/score.py -- those files are untouched.

Pure stdlib-free core (no imports at all): no API, no keys, no
network, no file or device use.
"""

PROMOTE_FRACTION = 0.8

DEMOTE_STREAK = 2


def _check_confidence(conf):
    """Return confidence as float, or raise ValueError."""
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ValueError(
            "graduate: confidence must be a number in [0, 1], got %r"
            % (conf,))
    if not (0.0 <= conf <= 1.0):
        raise ValueError(
            "graduate: confidence must be in [0, 1], got %r" % (conf,))
    return float(conf)


def _check_threshold(th):
    """Return threshold as float, or raise ValueError."""
    if isinstance(th, bool) or not isinstance(th, (int, float)):
        raise ValueError(
            "graduate: threshold must be a number in (0, 1], got %r"
            % (th,))
    if not (0.0 < th <= 1.0):
        raise ValueError(
            "graduate: threshold must be in (0, 1], got %r" % (th,))
    return float(th)


def _check_window(window):
    """Return window as int, or raise ValueError."""
    if isinstance(window, bool) or not isinstance(window, int):
        raise ValueError(
            "graduate: window must be a positive int, got %r" % (window,))
    if window <= 0:
        raise ValueError(
            "graduate: window must be a positive int, got %r" % (window,))
    return window


def recommend(history, threshold, window=10):
    """Promote/hold/demote verdict over the last ``window`` confidences.

    Returns the plain string ``"promote"``, ``"hold"``, or
    ``"demote"``. Never wired to any loop; never invents data (empty
    or short histories hold/demote only on what they show).
    """
    if not isinstance(history, (list, tuple)):
        raise ValueError(
            "graduate: history must be a list or tuple of confidences, "
            "got %r" % (history,))
    th = _check_threshold(threshold)
    win = _check_window(window)
    confs = [_check_confidence(c) for c in history]
    recent = confs[-win:]
    if len(recent) >= DEMOTE_STREAK and all(c < th for c in recent[-2:]):
        return "demote"
    if len(recent) == win and (
            sum(1 for c in recent if c >= th) / win >= PROMOTE_FRACTION):
        return "promote"
    return "hold"
