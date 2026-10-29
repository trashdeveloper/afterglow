#!/usr/bin/env python3
"""Confidence calibration tracker (Task D2, plan item 32, Wave D).

Confidence numbers tracked against outcomes: every prediction records a
confidence in [0, 1] plus whether it was right, and this module bins the
records so a calibration table shows whether "80% confident" means ~80%
right (per-bin predicted mean vs empirical accuracy).

Interface::

  calibrate(records, n_bins=10) -> list of per-bin dicts
  ece(records, n_bins=10) -> float expected calibration error
  brier_score(records) -> float mean squared error of confidence
  summary(records, n_bins=10) -> dict with n/accuracy/mean_confidence/ece/brier
  calibration_table(records, n_bins=10) -> ASCII table string (the plot)
  Calibrator -> stateful tracker with add/records/bins/summary/table/ece/brier

A record is a ``(confidence, correct)`` pair: confidence is a real
number in [0, 1] (bools rejected -- ``True`` is not a confidence) and
correct is ``True``/``False`` or ``1``/``0``. Anything else raises
ValueError; the tracker never invents data. Bins are equal-width:
bin ``i`` covers ``[i/n, (i+1)/n)`` with the last bin closed at 1.0, so
a confidence of exactly 1.0 lands in the final bin.

``ece`` is sum over bins of ``(count/total) * |accuracy - mean_conf|``;
``brier_score`` is the mean of ``(confidence - outcome)^2``. Empty
input yields all-empty bins, ``ece`` 0.0, and a table reading
"no records"; ``brier_score`` on empty input raises ValueError (a
score over zero predictions would be invented).

Default-off contract (Wave D header: additive + default-off): this
module runs ONLY on explicit call. It is imported by nothing in
agent/loop.py, agent/guard.py, or eval/score.py -- those files are
untouched.

Pure stdlib-free core (no imports at all): no API, no keys, no
network, no file or device use.
"""

_VERSION = "1"

_BAR_WIDTH = 20

_MAX_BINS = 100


def _check_confidence(conf):
    """Return confidence as float, or raise ValueError."""
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ValueError(
            "calibrate: confidence must be a number in [0, 1], got %r"
            % (conf,))
    if not (0.0 <= conf <= 1.0):
        raise ValueError(
            "calibrate: confidence must be in [0, 1], got %r" % (conf,))
    return float(conf)


def _check_outcome(ok):
    """Return outcome as int 0/1, or raise ValueError."""
    if isinstance(ok, bool):
        return int(ok)
    if isinstance(ok, int) and ok in (0, 1):
        return ok
    raise ValueError(
        "calibrate: correct must be True/False or 1/0, got %r" % (ok,))


def _check_n_bins(n_bins):
    """Return n_bins as int, or raise ValueError."""
    if isinstance(n_bins, bool) or not isinstance(n_bins, int):
        raise ValueError(
            "calibrate: n_bins must be an int 1..%d, got %r"
            % (_MAX_BINS, n_bins))
    if not 1 <= n_bins <= _MAX_BINS:
        raise ValueError(
            "calibrate: n_bins must be an int 1..%d, got %r"
            % (_MAX_BINS, n_bins))
    return n_bins


def _checked_pairs(records):
    """Normalize records to a list of (float, int) pairs, or raise."""
    if isinstance(records, (str, bytes)) or not hasattr(records, "__iter__"):
        raise ValueError(
            "calibrate: records must be a sequence of "
            "(confidence, correct) pairs, got %r" % (records,))
    pairs = []
    for item in records:
        if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)):
            raise ValueError(
                "calibrate: each record must be a "
                "(confidence, correct) pair, got %r" % (item,))
        if len(item) != 2:
            raise ValueError(
                "calibrate: each record must be a "
                "(confidence, correct) pair, got %r" % (item,))
        pairs.append((_check_confidence(item[0]), _check_outcome(item[1])))
    return pairs


def calibrate(records, n_bins=10):
    """Bin records; return one dict per bin (empty bins included).

    Each dict holds ``bin`` (index), ``lo``/``hi`` (edges),
    ``count``, ``mean_conf`` (mean predicted confidence, None when
    empty), ``accuracy`` (fraction correct, None when empty), and
    ``gap`` (``|accuracy - mean_conf|``, None when empty).
    """
    n_bins = _check_n_bins(n_bins)
    pairs = _checked_pairs(records)
    sums = [0.0] * n_bins
    hits = [0] * n_bins
    counts = [0] * n_bins
    for conf, ok in pairs:
        idx = int(conf * n_bins)
        if idx >= n_bins:
            idx = n_bins - 1
        sums[idx] += conf
        hits[idx] += ok
        counts[idx] += 1
    rows = []
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        if counts[i] == 0:
            rows.append({"bin": i, "lo": lo, "hi": hi, "count": 0,
                         "mean_conf": None, "accuracy": None, "gap": None})
        else:
            mean = sums[i] / counts[i]
            acc = hits[i] / counts[i]
            rows.append({"bin": i, "lo": lo, "hi": hi, "count": counts[i],
                         "mean_conf": mean, "accuracy": acc,
                         "gap": abs(acc - mean)})
    return rows


def ece(records, n_bins=10):
    """Expected calibration error: count-weighted mean bin gap."""
    rows = calibrate(records, n_bins=n_bins)
    total = sum(r["count"] for r in rows)
    if total == 0:
        return 0.0
    return sum(r["count"] / total * r["gap"]
               for r in rows if r["count"] > 0)


def brier_score(records):
    """Mean squared error between confidence and 0/1 outcome."""
    pairs = _checked_pairs(records)
    if not pairs:
        raise ValueError("calibrate: brier_score needs at least one record")
    return sum((c - o) ** 2 for c, o in pairs) / len(pairs)


def summary(records, n_bins=10):
    """Whole-tracker rollup: n, accuracy, mean_confidence, ece, brier."""
    n_bins = _check_n_bins(n_bins)
    pairs = _checked_pairs(records)
    if not pairs:
        return {"n": 0, "accuracy": None, "mean_confidence": None,
                "ece": 0.0, "brier": None}
    return {"n": len(pairs),
            "accuracy": sum(o for _, o in pairs) / len(pairs),
            "mean_confidence": sum(c for c, _ in pairs) / len(pairs),
            "ece": ece(pairs, n_bins=n_bins),
            "brier": brier_score(pairs)}


def _bar(frac, mark):
    """Fixed-width bar: round(frac * width) marks, rest dots."""
    n = int(round(frac * _BAR_WIDTH))
    n = max(0, min(_BAR_WIDTH, n))
    return mark * n + "." * (_BAR_WIDTH - n)


def calibration_table(records, n_bins=10):
    """ASCII calibration table: per-bin predicted mean vs accuracy.

    Each non-empty row shows the bin edges, count, predicted mean,
    empirical accuracy, gap, and two fixed-width bars (``#`` for
    predicted, ``=`` for actual) so over/under-confidence is visible
    at a glance. Empty bins read ``(empty)``; empty input reads
    "no records".
    """
    n_bins = _check_n_bins(n_bins)
    rows = calibrate(records, n_bins=n_bins)
    total = sum(r["count"] for r in rows)
    head = "calibration (n=%d, bins=%d)" % (total, n_bins)
    if total == 0:
        return head + "\nno records\n"
    lines = [head, "bin | n | pred | actual | gap | predicted | actual"]
    for r in rows:
        label = "%.2f-%.2f" % (r["lo"], r["hi"])
        if r["count"] == 0:
            lines.append("%s |   0 | (empty)" % label)
        else:
            lines.append("%s | %3d | %.3f | %.3f | %.3f | %s | %s"
                         % (label, r["count"], r["mean_conf"],
                            r["accuracy"], r["gap"],
                            _bar(r["mean_conf"], "#"),
                            _bar(r["accuracy"], "=")))
    s = summary(records, n_bins=n_bins)
    lines.append("summary: n=%d acc=%.3f mean_conf=%.3f ece=%.3f brier=%.3f"
                 % (s["n"], s["accuracy"], s["mean_confidence"],
                    s["ece"], s["brier"]))
    return "\n".join(lines) + "\n"


class Calibrator:
    """Stateful confidence/outcome tracker with the same math as above."""

    def __init__(self, n_bins=10):
        self._n_bins = _check_n_bins(n_bins)
        self._rows = []

    def add(self, confidence, correct):
        """Record one prediction; validates like :func:`calibrate`."""
        self._rows.append((_check_confidence(confidence),
                           _check_outcome(correct)))
        return self

    def records(self):
        """Return a copy of the recorded (confidence, outcome) pairs."""
        return list(self._rows)

    def __len__(self):
        return len(self._rows)

    def bins(self):
        """Per-bin dicts; see :func:`calibrate`."""
        return calibrate(self._rows, n_bins=self._n_bins)

    def ece(self):
        """Expected calibration error over records so far."""
        return ece(self._rows, n_bins=self._n_bins)

    def brier(self):
        """Brier score over records so far (None when no records)."""
        if not self._rows:
            return None
        return brier_score(self._rows)

    def summary(self):
        """Whole-tracker rollup; see :func:`summary`."""
        return summary(self._rows, n_bins=self._n_bins)

    def table(self):
        """ASCII calibration table; see :func:`calibration_table`."""
        return calibration_table(self._rows, n_bins=self._n_bins)
