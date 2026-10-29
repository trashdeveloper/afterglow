#!/usr/bin/env python3
"""Uncertainty abstention gate (Task D3, plan item 33, Wave D).

Loop abstains instead of patching when confidence is below threshold:
a low-confidence input produces ``abstain``, never a patch.

Interface::

  should_abstain(confidence, threshold=DEFAULT_THRESHOLD) -> bool
  decide(confidence, threshold=DEFAULT_THRESHOLD, patch=None) -> dict
  AbstentionPolicy -> stateful gate with check/decide/stats

``confidence`` is a real number in [0, 1] (bools rejected -- ``True``
is not a confidence). ``threshold`` is a real number in (0, 1]: the
gate abstains iff ``confidence < threshold`` (strict -- confidence
exactly AT the threshold proceeds). Anything else raises ValueError;
the gate never invents data.

:func:`decide` returns a dict with ``action`` (``"abstain"`` or
``"proceed"``), ``confidence``, ``threshold``, ``patch``, and
``reason``. The core guarantee: when ``action`` is ``"abstain"``,
``patch`` is ALWAYS None -- even if the caller passed a patch in, it
is dropped. When ``action`` is ``"proceed"``, ``patch`` passes
through untouched (None stays None). The loop branches on
``action``: abstain skips patch/apply entirely.

Default-off contract (Wave D header: additive + default-off): this
module runs ONLY on explicit call. It is imported by nothing in
agent/loop.py, agent/guard.py, or eval/score.py -- those files are
untouched.

Pure stdlib-free core (no imports at all): no API, no keys, no
network, no file or device use.
"""

DEFAULT_THRESHOLD = 0.5


def _check_confidence(conf):
    """Return confidence as float, or raise ValueError."""
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ValueError(
            "abstain: confidence must be a number in [0, 1], got %r"
            % (conf,))
    if not (0.0 <= conf <= 1.0):
        raise ValueError(
            "abstain: confidence must be in [0, 1], got %r" % (conf,))
    return float(conf)


def _check_threshold(th):
    """Return threshold as float, or raise ValueError."""
    if isinstance(th, bool) or not isinstance(th, (int, float)):
        raise ValueError(
            "abstain: threshold must be a number in (0, 1], got %r"
            % (th,))
    if not (0.0 < th <= 1.0):
        raise ValueError(
            "abstain: threshold must be in (0, 1], got %r" % (th,))
    return float(th)


def should_abstain(confidence, threshold=DEFAULT_THRESHOLD):
    """True iff confidence is below threshold (strict <): abstain.

    Confidence exactly AT the threshold proceeds (returns False).
    """
    conf = _check_confidence(confidence)
    th = _check_threshold(threshold)
    return conf < th


def decide(confidence, threshold=DEFAULT_THRESHOLD, patch=None):
    """Abstain-or-proceed decision; abstain NEVER carries a patch.

    Returns ``{"action", "confidence", "threshold", "patch",
    "reason"}``. On abstain ``patch`` is None regardless of input;
    on proceed the caller's ``patch`` passes through.
    """
    conf = _check_confidence(confidence)
    th = _check_threshold(threshold)
    if conf < th:
        return {"action": "abstain",
                "confidence": conf,
                "threshold": th,
                "patch": None,
                "reason": ("confidence %.4g < threshold %.4g: "
                           "abstain, no patch emitted" % (conf, th))}
    return {"action": "proceed",
            "confidence": conf,
            "threshold": th,
            "patch": patch,
            "reason": ("confidence %.4g >= threshold %.4g: "
                       "proceed" % (conf, th))}


class AbstentionPolicy:
    """Stateful abstention gate with a fixed threshold + counts."""

    def __init__(self, threshold=DEFAULT_THRESHOLD):
        self.threshold = _check_threshold(threshold)
        self.n_abstained = 0
        self.n_proceeded = 0

    def check(self, confidence):
        """Bool gate: True means abstain (no patch). Counts the call."""
        abstained = should_abstain(confidence, threshold=self.threshold)
        if abstained:
            self.n_abstained += 1
        else:
            self.n_proceeded += 1
        return abstained

    def decide(self, confidence, patch=None):
        """Dict decision via :func:`decide`; abstain drops any patch."""
        out = decide(confidence, threshold=self.threshold, patch=patch)
        if out["action"] == "abstain":
            self.n_abstained += 1
        else:
            self.n_proceeded += 1
        return out

    def stats(self):
        """Rollup: threshold, n_abstained, n_proceeded, n."""
        return {"threshold": self.threshold,
                "n_abstained": self.n_abstained,
                "n_proceeded": self.n_proceeded,
                "n": self.n_abstained + self.n_proceeded}
