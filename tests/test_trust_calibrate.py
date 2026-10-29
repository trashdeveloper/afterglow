"""Task D2 (confidence calibration, plan item 32, Wave D) tests:
agent/trust/calibrate.py.

Confidence numbers tracked against outcomes: every prediction records a
confidence in [0,1] plus whether it was right, and the module bins them
so a calibration table shows whether "80% confident" means ~80% right
(per-bin predicted mean vs empirical accuracy, ECE, Brier score, plus an
ASCII calibration table/plot).

RED-first TDD: this file was written BEFORE agent/trust/calibrate.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_calibrate -v   (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import calibrate  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _recs_80_pct():
    # 10 predictions at 0.8 confidence, 8 right: the textbook "80%
    # confident means 80% right" fixture.
    return [(0.8, True)] * 8 + [(0.8, False)] * 2


class ValidationRejectsJunk(unittest.TestCase):
    def test_bad_confidence_raises(self):
        for bad in (-0.1, 1.1, 2.0, float("nan"), float("inf"),
                    "0.8", None, True, [0.8], (0.8,)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    calibrate.calibrate([(bad, True)])

    def test_bad_outcome_raises(self):
        for bad in (2, -1, 0.5, "yes", None, [True], (True,)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    calibrate.calibrate([(0.8, bad)])

    def test_bad_n_bins_raises(self):
        for bad in (0, -3, "10", 2.5, None, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    calibrate.calibrate([], n_bins=bad)
                with self.assertRaises(ValueError):
                    calibrate.Calibrator(n_bins=bad)

    def test_non_record_input_raises(self):
        for bad in (None, "nope", 42, 3.5):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    calibrate.calibrate(bad)

    def test_malformed_pairs_raise(self):
        for bad in ([(0.8, True, 1)], [(0.8,)], ["x"], [(None, None, None)]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    calibrate.calibrate(bad)

    def test_add_validates_like_calibrate(self):
        cal = calibrate.Calibrator()
        for bad_conf, bad_ok in ((1.5, True), (0.5, "yes")):
            with self.subTest(bad_conf=bad_conf, bad_ok=bad_ok):
                with self.assertRaises(ValueError):
                    cal.add(bad_conf, bad_ok)
        self.assertEqual(len(cal), 0)


class AcceptsBoundaryInputs(unittest.TestCase):
    def test_zero_and_one_confidence_ok(self):
        rows = calibrate.calibrate([(0, False), (1, True)], n_bins=2)
        self.assertEqual(rows[0]["count"], 1)
        self.assertEqual(rows[0]["mean_conf"], 0.0)
        self.assertEqual(rows[0]["accuracy"], 0.0)
        self.assertEqual(rows[1]["count"], 1)
        self.assertEqual(rows[1]["mean_conf"], 1.0)
        self.assertEqual(rows[1]["accuracy"], 1.0)

    def test_bool_and_int_outcomes_equivalent(self):
        rows = calibrate.calibrate(
            [(0.8, True), (0.8, 1), (0.8, 0), (0.8, False)])
        self.assertEqual(rows[8]["count"], 4)
        self.assertAlmostEqual(rows[8]["accuracy"], 0.5)

    def test_list_pairs_accepted_like_tuples(self):
        rows = calibrate.calibrate([[0.8, True], [0.8, False]])
        self.assertEqual(rows[8]["count"], 2)
        self.assertAlmostEqual(rows[8]["accuracy"], 0.5)


class BinningPinned(unittest.TestCase):
    def test_bin_edges(self):
        rows = calibrate.calibrate(
            [(0.0, True), (0.79999, True), (0.8, True), (1.0, True)])
        self.assertEqual(rows[0]["count"], 1)    # 0.0 -> first bin
        self.assertEqual(rows[7]["count"], 1)    # 0.79999 -> bin 7
        self.assertEqual(rows[8]["count"], 1)    # 0.8 -> bin 8
        self.assertEqual(rows[9]["count"], 1)    # 1.0 -> last bin

    def test_eighty_percent_bin(self):
        rows = calibrate.calibrate(_recs_80_pct())
        cell = rows[8]
        self.assertEqual(cell["bin"], 8)
        self.assertAlmostEqual(cell["lo"], 0.8)
        self.assertAlmostEqual(cell["hi"], 0.9)
        self.assertEqual(cell["count"], 10)
        self.assertAlmostEqual(cell["mean_conf"], 0.8)
        self.assertAlmostEqual(cell["accuracy"], 0.8)
        self.assertAlmostEqual(cell["gap"], 0.0)

    def test_empty_bins_reported_with_zero_count(self):
        rows = calibrate.calibrate([(0.05, True)])
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["count"], 1)
        for cell in rows[1:]:
            self.assertEqual(cell["count"], 0)
            self.assertIsNone(cell["mean_conf"])
            self.assertIsNone(cell["accuracy"])
            self.assertIsNone(cell["gap"])

    def test_single_bin_holds_everything(self):
        rows = calibrate.calibrate(
            [(0.1, True), (0.9, False)], n_bins=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertAlmostEqual(rows[0]["mean_conf"], 0.5)
        self.assertAlmostEqual(rows[0]["accuracy"], 0.5)

    def test_empty_records_give_empty_bins(self):
        rows = calibrate.calibrate([])
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(c["count"] == 0 for c in rows))


class EceAndBrierPinned(unittest.TestCase):
    def test_perfect_calibration_ece_zero(self):
        self.assertAlmostEqual(calibrate.ece(_recs_80_pct()), 0.0)
        self.assertAlmostEqual(
            calibrate.ece([(0, False), (1, True)], n_bins=2), 0.0)

    def test_ece_known_value(self):
        recs = [(0.25, True), (0.25, False), (0.75, False), (0.75, False)]
        # bin0: mean .25 acc .5 gap .25 (n=2); bin1: mean .75 acc 0 gap .75
        # ECE = .5*.25 + .5*.75 = .5
        self.assertEqual(calibrate.ece(recs, n_bins=2), 0.5)

    def test_total_miscalibration_gap_visible(self):
        rows = calibrate.calibrate([(0.8, False)] * 10)
        self.assertAlmostEqual(rows[8]["accuracy"], 0.0)
        self.assertAlmostEqual(rows[8]["gap"], 0.8)
        self.assertAlmostEqual(calibrate.ece([(0.8, False)] * 10), 0.8)

    def test_ece_empty_is_zero(self):
        self.assertEqual(calibrate.ece([]), 0.0)

    def test_brier_known_values(self):
        self.assertEqual(
            calibrate.brier_score([(1.0, True), (0.0, False)]), 0.0)
        self.assertEqual(calibrate.brier_score([(1.0, False)]), 1.0)
        self.assertEqual(
            calibrate.brier_score([(0.5, True), (0.5, False)]), 0.25)

    def test_brier_empty_raises(self):
        with self.assertRaises(ValueError):
            calibrate.brier_score([])


class CalibratorStateful(unittest.TestCase):
    def test_add_and_summary(self):
        cal = calibrate.Calibrator()
        for conf, ok in _recs_80_pct():
            cal.add(conf, ok)
        self.assertEqual(len(cal), 10)
        s = cal.summary()
        self.assertEqual(s["n"], 10)
        self.assertAlmostEqual(s["accuracy"], 0.8)
        self.assertAlmostEqual(s["mean_confidence"], 0.8)
        self.assertAlmostEqual(s["ece"], 0.0)
        self.assertAlmostEqual(s["brier"], 0.16)

    def test_summary_empty(self):
        s = calibrate.Calibrator().summary()
        self.assertEqual(s["n"], 0)
        self.assertIsNone(s["accuracy"])
        self.assertIsNone(s["mean_confidence"])
        self.assertEqual(s["ece"], 0.0)
        self.assertIsNone(s["brier"])

    def test_summary_function_matches_class(self):
        recs = _recs_80_pct()
        cal = calibrate.Calibrator()
        for conf, ok in recs:
            cal.add(conf, ok)
        self.assertEqual(cal.summary(), calibrate.summary(recs))
        self.assertEqual(cal.ece(), calibrate.ece(recs))
        self.assertEqual(cal.brier(), calibrate.brier_score(recs))
        self.assertEqual(cal.bins(), calibrate.calibrate(recs))

    def test_records_returns_copy(self):
        cal = calibrate.Calibrator()
        cal.add(0.8, True)
        recs = cal.records()
        recs.append((0.1, False))
        self.assertEqual(len(cal), 1)


class CalibrationTableRender(unittest.TestCase):
    def test_eighty_percent_row_exact(self):
        recs = [(0.75, True), (0.75, True), (0.75, False), (0.75, True)]
        table = calibrate.calibration_table(recs, n_bins=5)
        self.assertIn(
            "0.60-0.80 |   4 | 0.750 | 0.750 | 0.000 | "
            "###############..... | ===============.....",
            table)

    def test_empty_table_says_no_records(self):
        table = calibrate.calibration_table([])
        self.assertIn("n=0", table)
        self.assertIn("no records", table)

    def test_empty_bins_marked(self):
        table = calibrate.calibration_table([(0.05, True)], n_bins=5)
        self.assertIn("0.00-0.20", table)
        self.assertIn("(empty)", table)

    def test_table_shows_miscalibration_numbers(self):
        table = calibrate.calibration_table([(0.8, False)] * 10)
        self.assertIn("0.80-0.90", table)
        self.assertIn("0.800", table)
        self.assertIn("0.000", table)
        self.assertIn("summary:", table)

    def test_class_table_matches_function(self):
        cal = calibrate.Calibrator(n_bins=5)
        cal.add(0.75, True)
        cal.add(0.75, False)
        self.assertEqual(
            cal.table(),
            calibrate.calibration_table([(0.75, True), (0.75, False)],
                                        n_bins=5))


class ContractUntouchedAndStdlib(unittest.TestCase):
    def test_loop_and_guard_untouched(self):
        for name in ("agent/loop.py", "agent/guard.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "calibrat", text,
                "Wave D default-off: %s must not wire this in" % name)

    def test_eval_score_untouched(self):
        with open(os.path.join(ROOT, "eval", "score.py"),
                  encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("calibrat", text,
                         "score.py must not import the trust module")

    def test_stdlib_only(self):
        src = os.path.join(ROOT, "agent", "trust", "calibrate.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        imports = [ln.strip() for ln in text.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        for ln in imports:
            self.assertRegex(
                ln, r"^(import |from )(__future__|math|statistics)",
                "calibrate.py must stay stdlib-light: %r" % ln)
        lowered = text.lower()
        for token in ("urllib", "socket", "http", "bearer", "api_key",
                      "apikey", "open(", "subprocess"):
            self.assertNotIn(token, lowered,
                             "calibrate.py must do no I/O or network: %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
