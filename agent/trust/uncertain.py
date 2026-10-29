#!/usr/bin/env python3
"""Uncertainty display (Task D6, plan item 36, Wave D).

Verdicts carry explicit uncertainty ranges instead of bare PASS/FAIL:
every verdict renders as ``VERDICT [lo, hi] (n=...)`` where the range
is a Wilson score interval over the supporting observations. Range
widths reflect evidence strength: more agreeing observations give a
narrower interval, thin evidence gives a wide one, and zero evidence
gives the full ``[0, 1]`` (maximal ignorance, never a bare claim).

Interface::

  wilson_interval(k, n, z=DEFAULT_Z) -> (lo, hi)
  verdict_interval(passed, n, z=DEFAULT_Z) -> (lo, hi)
  uncertain_verdict(passed, n, z=DEFAULT_Z) -> dict
  format_verdict(passed, n, z=DEFAULT_Z) -> "PASS [0.7225, 1.0000] (n=10)"
  UncertainVerdict -> stateful box with add/counts/interval/summary/display

``passed`` is a real bool (``True``/``False`` only; ``1``/``"PASS"``
are rejected so callers must be explicit). ``n``/``k`` are counts:
``n`` is a non-negative int, ``k`` an int with ``0 <= k <= n``.
``z`` is the interval scale (default 1.96, the usual 95% scale);
it must be a positive finite number. Anything else raises
ValueError; the module never invents data.

Wilson detail: with ``p = k/n`` and ``d = 1 + z^2/n`` the centre is
``(p + z^2/2n)/d`` and the half-width is
``z*sqrt(p(1-p)/n + z^2/4n^2)/d``, clamped to the unit interval.
At the unanimous ends the algebra touches a boundary exactly
(``k=n`` gives ``hi=1``, ``k=0`` gives ``lo=0``), so those edges are
snapped exact instead of left to float dust. ``n=0`` carries no
evidence and returns ``(0.0, 1.0)``.

Default-off contract (Wave D header: additive + default-off): this
module runs ONLY on explicit call. It is imported by nothing in
agent/loop.py, agent/guard.py, or eval/score.py -- those files are
untouched.

Pure stdlib-free core (no imports at all): no API, no keys, no
network, no file or device use. Roots use ``** 0.5``.
"""

DEFAULT_Z = 1.96


def _check_passed(passed):
    """Return passed, or raise ValueError (must be a real bool)."""
    if type(passed) is not bool:
        raise ValueError(
            "uncertain: passed must be True/False, got %r" % (passed,))
    return passed


def _check_n(n):
    """Return n, or raise ValueError (must be a non-negative int)."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError(
            "uncertain: n must be a non-negative int, got %r" % (n,))
    if n < 0:
        raise ValueError(
            "uncertain: n must be a non-negative int, got %r" % (n,))
    return n


def _check_k(k, n):
    """Return k, or raise ValueError (must satisfy 0 <= k <= n)."""
    if isinstance(k, bool) or not isinstance(k, int):
        raise ValueError(
            "uncertain: k must satisfy 0 <= k <= n, got %r" % (k,))
    if not 0 <= k <= n:
        raise ValueError(
            "uncertain: k must satisfy 0 <= k <= n, got %r vs n=%r"
            % (k, n))
    return k


def _check_z(z):
    """Return z as float, or raise ValueError (positive finite)."""
    if isinstance(z, bool) or not isinstance(z, (int, float)):
        raise ValueError(
            "uncertain: z must be a positive finite number, got %r"
            % (z,))
    zf = float(z)
    if zf != zf:
        raise ValueError(
            "uncertain: z must be a positive finite number, got %r"
            % (z,))
    if zf in (float("inf"), float("-inf")):
        raise ValueError(
            "uncertain: z must be a positive finite number, got %r"
            % (z,))
    if zf <= 0.0:
        raise ValueError(
            "uncertain: z must be a positive finite number, got %r"
            % (z,))
    return zf


def wilson_interval(k, n, z=DEFAULT_Z):
    """Wilson score interval for k successes out of n observations.

    Returns ``(lo, hi)`` inside ``[0, 1]``. ``n=0`` returns
    ``(0.0, 1.0)``: no evidence, full width. Width shrinks as ``n``
    grows and as agreement hardens (unanimous ends are narrowest).
    """
    z = _check_z(z)
    n = _check_n(n)
    k = _check_k(k, n)
    if n == 0:
        return (0.0, 1.0)
    z2 = z * z
    denom = 1.0 + z2 / n
    phat = k / n
    mid = (phat + z2 / (2.0 * n)) / denom
    half = (z * ((phat * (1.0 - phat) / n
                  + z2 / (4.0 * n * n)) ** 0.5)) / denom
    lo = mid - half
    hi = mid + half
    if lo < 0.0:
        lo = 0.0
    if hi > 1.0:
        hi = 1.0
    if k == n:
        hi = 1.0
    if k == 0:
        lo = 0.0
    return (lo, hi)


def verdict_interval(passed, n, z=DEFAULT_Z):
    """Interval for one verdict backed by n agreeing observations.

    ``passed=True`` is the ``k=n`` end (``hi`` is 1.0);
    ``passed=False`` is the ``k=0`` end (``lo`` is 0.0).
    """
    passed = _check_passed(passed)
    n = _check_n(n)
    z = _check_z(z)
    if passed:
        return wilson_interval(n, n, z=z)
    return wilson_interval(0, n, z=z)


def uncertain_verdict(passed, n, z=DEFAULT_Z):
    """Verdict dict with an explicit range (never a bare PASS/FAIL).

    Returns ``{"verdict", "p_hat", "lo", "hi", "width", "n", "z"}``
    where ``verdict`` is ``"PASS"``/``"FAIL"``, ``p_hat`` is ``k/n``
    (``None`` when ``n=0``), and ``width`` is ``hi - lo``.
    """
    passed = _check_passed(passed)
    n = _check_n(n)
    z = _check_z(z)
    lo, hi = verdict_interval(passed, n, z=z)
    return {"verdict": "PASS" if passed else "FAIL",
            "p_hat": (1.0 if passed else 0.0) if n > 0 else None,
            "lo": lo,
            "hi": hi,
            "width": hi - lo,
            "n": n,
            "z": z}


def _render(verdict, lo, hi, n):
    """Fixed display form: ``VERDICT [lo, hi] (n=...)``."""
    return "%s [%.4f, %.4f] (n=%d)" % (verdict, lo, hi, n)


def format_verdict(passed, n, z=DEFAULT_Z):
    """Display string with the range built in (never bare PASS/FAIL)."""
    d = uncertain_verdict(passed, n, z=z)
    return _render(d["verdict"], d["lo"], d["hi"], d["n"])


class UncertainVerdict:
    """Stateful verdict box: feed observations, read off range + label."""

    def __init__(self, z=DEFAULT_Z):
        self._z = _check_z(z)
        self._pass = 0
        self._total = 0

    def add(self, passed):
        """Record one observation; validates like :func:`verdict_interval`."""
        _check_passed(passed)
        if passed:
            self._pass += 1
        self._total += 1
        return self

    def counts(self):
        """Return ``(k, n)``: passes observed out of total observed."""
        return (self._pass, self._total)

    def __len__(self):
        return self._total

    def interval(self):
        """Wilson interval over the observations so far."""
        return wilson_interval(self._pass, self._total, z=self._z)

    def summary(self):
        """Rollup: verdict label, counts, estimate, range, width."""
        lo, hi = self.interval()
        n = self._total
        k = self._pass
        if k > n - k:
            label = "PASS"
        elif k < n - k:
            label = "FAIL"
        else:
            label = "UNKNOWN"
        return {"verdict": label,
                "k": k,
                "n": n,
                "p_hat": (k / n) if n > 0 else None,
                "lo": lo,
                "hi": hi,
                "width": hi - lo,
                "z": self._z}

    def display(self):
        """Display string with the range built in (never bare)."""
        s = self.summary()
        return _render(s["verdict"], s["lo"], s["hi"], s["n"])
