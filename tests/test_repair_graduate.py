"""Task G10 (full-auto graduation, plan item 80) tests:
agent/repair/graduate.py.

Interface (plan text, verbatim, Task G10 lines 613-631):

  Consumes: agent/trust/abstain.py policy shape.
  Produces: recommend(history, threshold, window=10) ->
    "promote"|"hold"|"demote" (standalone recommender; no loop wiring --
    wiring needs a later explicit approval).

  Step 1 test: 8/10 above threshold -> promote;
  2 consecutive below -> demote; else hold.

Interpretation of the plan text (flagged, plan is terse):

- ``history`` is a list/tuple of confidence numbers in [0, 1] (same
  validation as agent/trust/abstain.py: bools, non-numbers,
  out-of-range, and NaN raise ValueError -- the gate never invents data).
- ``threshold`` is a number in (0, 1] (same as abstain: strict
  less-than is "below"; confidence exactly AT the threshold counts as
  above/proceed).
- ``window`` is a positive int (default 10); only the last ``window``
  entries of ``history`` are considered.
- "8/10 above" generalizes to ``above / window >= 0.8`` for other
  window sizes; promotion additionally requires a full window of data
  (short history can never promote -- conservative, never invents).
- "2 consecutive below" means the LAST TWO entries of the considered
  window are both strictly below threshold; it needs only 2 entries,
  so it fires even on short histories.
- Precedence (flagged): demote is checked FIRST. A window with 8/10
  above whose last two are below returns "demote" (the loop is
  currently failing -- promoting would be unsafe). E.g.
  [0.9]*8 + [0.1]*2 with threshold 0.5 -> "demote", not "promote".
- Empty history -> "hold" (no data, never invents).
- Standalone: returns a plain string; no loop/guard/score wiring.

Contract:

- 8/10 (or >=80% of a full window) above threshold -> "promote".
- Last two below threshold -> "demote" (takes precedence over promote).
- Anything else -> "hold".
- Junk input (bad confidence, bad threshold, bad window, non-sequence
  history) raises ValueError loudly.
- Deterministic, stdlib-free core (no imports at all, like
  abstain.py); no API, no keys, no network. Default-off: explicit
  call only; agent/loop.py, agent/guard.py, eval/score.py untouched.

RED-first TDD: this file was written BEFORE
agent/repair/graduate.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_graduate.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import graduate  # noqa: E402  (RED before implementation)


class PromoteRule(unittest.TestCase):
    def test_eight_of_ten_above_promotes(self):
        # Canonical 8/10: eight above, two below, below NOT consecutive
        # at the end (interleaved so demote does not fire).
        hist = [0.9, 0.1, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.1]
        # last two are (0.9, 0.1): not both below -> promote on 8/10.
        self.assertEqual(graduate.recommend(hist, 0.5), "promote")

    def test_nine_and_ten_of_ten_promote(self):
        self.assertEqual(graduate.recommend([0.9] * 9 + [0.1], 0.5),
                         "promote")
        self.assertEqual(graduate.recommend([0.9] * 10, 0.5), "promote")

    def test_seven_of_ten_holds(self):
        # True 7/10 with a non-below tail: 7 above, 3 below,
        # tail ends (0.9, 0.1) so demote does not fire -> hold.
        hist = [0.1, 0.9, 0.1, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.1]
        # 7 above (positions of 0.9), last two (0.9, 0.1) not both below.
        self.assertEqual(graduate.recommend(hist, 0.5), "hold")

    def test_custom_threshold_respected(self):
        hist = [0.8] * 8 + [0.2, 0.8]  # 9/10 above 0.5
        self.assertEqual(graduate.recommend(hist, 0.5), "promote")
        # Same history against threshold 0.9: only ... 0.8 < 0.9 below.
        self.assertEqual(graduate.recommend(hist, 0.9), "demote")

    def test_custom_window_scales(self):
        # window=5: 4/5 = 0.8 -> promote.
        self.assertEqual(
            graduate.recommend([0.9, 0.9, 0.9, 0.9, 0.1], 0.5, window=5),
            "promote")
        # window=5: 3/5 = 0.6 -> hold (tail not both below).
        self.assertEqual(
            graduate.recommend([0.9, 0.1, 0.9, 0.9, 0.1], 0.5, window=5),
            "hold")

    def test_long_history_uses_last_window_only(self):
        # 20 stale failures followed by a full good window -> promote
        # (old data outside the window is ignored).
        hist = [0.1] * 20 + [0.9] * 10
        self.assertEqual(graduate.recommend(hist, 0.5), "promote")
        # 20 stale successes followed by a bad window -> not promote.
        hist = [0.9] * 20 + [0.1] * 10
        self.assertEqual(graduate.recommend(hist, 0.5), "demote")


class DemoteRule(unittest.TestCase):
    def test_two_consecutive_below_demotes(self):
        hist = [0.9] * 8 + [0.1, 0.1]  # last two below
        self.assertEqual(graduate.recommend(hist, 0.5), "demote")

    def test_demote_takes_precedence_over_promote(self):
        # 8/10 above AND last two below: demote wins (flagged reading:
        # the loop is currently failing, promoting would be unsafe).
        hist = [0.9] * 8 + [0.1, 0.1]
        self.assertEqual(graduate.recommend(hist, 0.5), "demote")

    def test_demote_on_short_history(self):
        self.assertEqual(graduate.recommend([0.1, 0.1], 0.5), "demote")
        self.assertEqual(graduate.recommend([0.9, 0.1, 0.1], 0.5),
                         "demote")

    def test_single_trailing_below_is_not_demote(self):
        # Only the last one below, 9/10 above -> promote, not demote.
        self.assertEqual(graduate.recommend([0.9] * 9 + [0.1], 0.5),
                         "promote")

    def test_split_below_is_not_demote(self):
        # Two belows but not consecutive at the tail -> no demote.
        hist = [0.1, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.1]
        self.assertEqual(graduate.recommend(hist, 0.5), "promote")


class HoldRule(unittest.TestCase):
    def test_empty_history_holds(self):
        self.assertEqual(graduate.recommend([], 0.5), "hold")

    def test_short_history_never_promotes(self):
        # Even a perfect short streak is hold: promotion needs a full
        # window (conservative -- never invents confidence).
        self.assertEqual(graduate.recommend([0.9] * 9, 0.5), "hold")
        self.assertEqual(graduate.recommend([0.9], 0.5), "hold")

    def test_middling_history_holds(self):
        hist = [0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.1, 0.9, 0.9]
        # 6/10 above, tail (0.9, 0.9) not below -> hold.
        self.assertEqual(graduate.recommend(hist, 0.5), "hold")

    def test_all_below_long_ago_but_tail_ok_holds(self):
        hist = [0.1] * 8 + [0.9, 0.9]  # 2/10 above, tail fine
        self.assertEqual(graduate.recommend(hist, 0.5), "hold")


class ThresholdBoundary(unittest.TestCase):
    def test_at_threshold_counts_as_above(self):
        # Matches abstain.py strict-<: confidence == threshold proceeds,
        # so here it counts as above, not below.
        hist = [0.5] * 10
        self.assertEqual(graduate.recommend(hist, 0.5), "promote")

    def test_at_threshold_breaks_demote_streak(self):
        # Last two are (below, at-threshold): not BOTH below -> no demote.
        hist = [0.1] * 8 + [0.1, 0.5]
        self.assertEqual(graduate.recommend(hist, 0.5), "hold")


class ValidationRejectsJunk(unittest.TestCase):
    def test_bad_confidence_raises(self):
        base = [0.9] * 10
        for bad in (-0.1, 1.1, 2.0, float("nan"), float("inf"),
                    float("-inf"), "0.8", None, True, False,
                    [0.8], (0.8,), {"c": 0.8}):
            with self.subTest(bad=bad):
                hist = list(base)
                hist[3] = bad
                with self.assertRaises(ValueError):
                    graduate.recommend(hist, 0.5)

    def test_bad_threshold_raises(self):
        hist = [0.9] * 10
        for bad in (0, -0.5, 1.1, 2.0, float("nan"), float("inf"),
                    "0.5", None, True, [0.5], (0.5,)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    graduate.recommend(hist, bad)

    def test_bad_window_raises(self):
        hist = [0.9] * 10
        for bad in (0, -1, -10, 2.5, "10", None, True, False, [10]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    graduate.recommend(hist, 0.5, window=bad)

    def test_bad_history_type_raises(self):
        for bad in ("0.9,0.9", 0.9, None, True, {"h": [0.9]}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    graduate.recommend(bad, 0.5)

    def test_tuple_history_ok(self):
        self.assertEqual(graduate.recommend((0.9,) * 10, 0.5), "promote")
        self.assertEqual(graduate.recommend((0.1, 0.1), 0.5), "demote")

    def test_returns_plain_string(self):
        out = graduate.recommend([0.9] * 10, 0.5)
        self.assertIs(type(out), str)
        self.assertIn(out, ("promote", "hold", "demote"))


class DefaultOffContract(unittest.TestCase):
    def test_loop_guard_score_do_not_import_graduate(self):
        # Standalone recommender: no loop wiring without later explicit
        # approval -- loop.py, guard.py, eval/score.py must not import it.
        for rel in ("agent/loop.py", "agent/guard.py", "eval/score.py"):
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path) as f:
                src = f.read()
            self.assertNotIn("graduate", src,
                             "%s must not import graduate" % rel)

    def test_no_network_or_file_imports(self):
        # No API, no keys, no network: pure core with no imports at
        # all (like abstain.py).
        path = os.path.join(ROOT, "agent", "repair", "graduate.py")
        with open(path) as f:
            src = f.read()
        self.assertNotIn("import ", src)
        for token in ("urllib", "socket", "http", "open(",
                      "API_KEY", "request"):
            self.assertNotIn(token, src)


if __name__ == "__main__":
    unittest.main()
