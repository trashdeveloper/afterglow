"""Task D6 (uncertainty display, plan item 36, Wave D) tests:
agent/trust/uncertain.py.

Verdicts carry explicit uncertainty ranges instead of bare PASS/FAIL:
every verdict renders as ``VERDICT [lo, hi] (n=...)`` and range widths
shrink as evidence strengthens (more agreeing observations -> narrower
interval, zero evidence -> full [0, 1]).

RED-first TDD: this file was written BEFORE agent/trust/uncertain.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_uncertain -v   (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import uncertain  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

Z = 1.96
Z2 = Z ** 2  # 3.8416


class ValidationRejectsJunk(unittest.TestCase):
    def test_bad_passed_raises(self):
        for bad in ("PASS", "FAIL", "pass", "", 1, 0, 1.0, 0.0,
                    None, [True], (True,), {"v": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    uncertain.verdict_interval(bad, 10)
                with self.assertRaises(ValueError):
                    uncertain.uncertain_verdict(bad, 10)
                with self.assertRaises(ValueError):
                    uncertain.format_verdict(bad, 10)

    def test_bad_n_raises(self):
        for bad in (-1, -100, 1.5, 2.0, float("nan"), "10", None,
                    True, False, [10], (10,)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    uncertain.verdict_interval(True, bad)
                with self.assertRaises(ValueError):
                    uncertain.wilson_interval(1, bad)

    def test_bad_z_raises(self):
        for bad in (0, -1.0, float("nan"), float("inf"),
                    float("-inf"), "1.96", None, True, [1.96]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    uncertain.verdict_interval(True, 10, z=bad)
                with self.assertRaises(ValueError):
                    uncertain.wilson_interval(5, 10, z=bad)

    def test_bad_k_raises(self):
        for bad_k, bad_n in ((-1, 10), (11, 10), (5, -5), (1.5, 10),
                             ("5", 10), (None, 10), (True, 10)):
            with self.subTest(bad_k=bad_k, bad_n=bad_n):
                with self.assertRaises(ValueError):
                    uncertain.wilson_interval(bad_k, bad_n)

    def test_class_rejects_junk(self):
        with self.assertRaises(ValueError):
            uncertain.UncertainVerdict(z=0)
        with self.assertRaises(ValueError):
            uncertain.UncertainVerdict(z=float("nan"))
        box = uncertain.UncertainVerdict()
        for bad in ("PASS", 1, 0, None, 1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    box.add(bad)
        self.assertEqual(len(box), 0)


class EndpointsPinned(unittest.TestCase):
    def test_zero_evidence_is_full_width(self):
        lo, hi = uncertain.verdict_interval(True, 0)
        self.assertEqual((lo, hi), (0.0, 1.0))
        lo, hi = uncertain.verdict_interval(False, 0)
        self.assertEqual((lo, hi), (0.0, 1.0))
        lo, hi = uncertain.wilson_interval(0, 0)
        self.assertEqual((lo, hi), (0.0, 1.0))
        d = uncertain.uncertain_verdict(True, 0)
        self.assertEqual(d["width"], 1.0)

    def test_pass_closed_form(self):
        # k=n unanimous PASS: Wilson collapses to lo = n/(n+z^2), hi = 1.
        for n in (1, 5, 10, 100):
            with self.subTest(n=n):
                lo, hi = uncertain.verdict_interval(True, n)
                self.assertAlmostEqual(lo, n / (n + Z2))
                self.assertEqual(hi, 1.0)

    def test_fail_closed_form(self):
        # k=0 unanimous FAIL: mirror image, lo = 0, hi = z^2/(n+z^2).
        for n in (1, 5, 10, 100):
            with self.subTest(n=n):
                lo, hi = uncertain.verdict_interval(False, n)
                self.assertEqual(lo, 0.0)
                self.assertAlmostEqual(hi, Z2 / (n + Z2))

    def test_pass_ten_pinned(self):
        lo, hi = uncertain.verdict_interval(True, 10)
        self.assertAlmostEqual(lo, 0.7225, places=4)
        self.assertEqual(hi, 1.0)

    def test_dict_shape(self):
        d = uncertain.uncertain_verdict(True, 10)
        self.assertEqual(d["verdict"], "PASS")
        self.assertEqual(d["p_hat"], 1.0)
        self.assertAlmostEqual(d["lo"], 10 / (10 + Z2))
        self.assertEqual(d["hi"], 1.0)
        self.assertAlmostEqual(d["width"], d["hi"] - d["lo"])
        self.assertEqual(d["n"], 10)
        self.assertEqual(d["z"], Z)
        d = uncertain.uncertain_verdict(False, 10)
        self.assertEqual(d["verdict"], "FAIL")
        self.assertEqual(d["p_hat"], 0.0)


class WidthReflectsEvidenceStrength(unittest.TestCase):
    def test_task_width_shrinks_with_n_pass(self):
        # THE task test: more evidence -> narrower range (PASS side).
        widths = [uncertain.uncertain_verdict(True, n)["width"]
                  for n in (0, 1, 5, 10, 50, 200)]
        self.assertEqual(widths[0], 1.0)
        for earlier, later in zip(widths, widths[1:]):
            self.assertGreater(earlier, later)

    def test_task_width_shrinks_with_n_fail(self):
        # Same monotonic shrink on the FAIL side.
        widths = [uncertain.uncertain_verdict(False, n)["width"]
                  for n in (0, 1, 5, 10, 50, 200)]
        self.assertEqual(widths[0], 1.0)
        for earlier, later in zip(widths, widths[1:]):
            self.assertGreater(earlier, later)

    def test_task_wilson_width_shrinks_with_n(self):
        # Mid-rate evidence also tightens with n: 6/10 vs 60/100.
        narrow = uncertain.wilson_interval(60, 100)
        wide = uncertain.wilson_interval(6, 10)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_task_agreement_narrows_at_fixed_n(self):
        # Same n, stronger agreement -> narrower: 10/10 < 6/10.
        split = uncertain.wilson_interval(6, 10)
        solid = uncertain.wilson_interval(10, 10)
        self.assertLess(solid[1] - solid[0], split[1] - split[0])
        split0 = uncertain.wilson_interval(4, 10)
        solid0 = uncertain.wilson_interval(0, 10)
        self.assertLess(solid0[1] - solid0[0], split0[1] - split0[0])

    def test_zero_evidence_widest(self):
        for k, n in ((10, 10), (0, 10), (5, 10), (50, 100)):
            with self.subTest(k=k, n=n):
                w = uncertain.wilson_interval(k, n)
                self.assertLess(w[1] - w[0], 1.0)


class ContainmentPinned(unittest.TestCase):
    def test_bounds_inside_unit(self):
        for k, n in ((0, 1), (1, 1), (3, 10), (0, 10), (7, 7), (50, 100)):
            with self.subTest(k=k, n=n):
                lo, hi = uncertain.wilson_interval(k, n)
                self.assertGreaterEqual(lo, 0.0)
                self.assertLessEqual(hi, 1.0)
                self.assertLessEqual(lo, hi)

    def test_phat_inside_interval(self):
        for k, n in ((3, 10), (0, 10), (10, 10), (1, 3), (99, 100)):
            with self.subTest(k=k, n=n):
                lo, hi = uncertain.wilson_interval(k, n)
                phat = k / n
                self.assertLessEqual(lo, phat + 1e-12)
                self.assertGreaterEqual(hi, phat - 1e-12)

    def test_wider_z_wider_interval(self):
        lo_n, hi_n = uncertain.wilson_interval(6, 10, z=1.0)
        lo_w, hi_w = uncertain.wilson_interval(6, 10, z=2.576)
        self.assertLessEqual(lo_w, lo_n)
        self.assertGreaterEqual(hi_w, hi_n)


class DisplayNeverBare(unittest.TestCase):
    def test_pass_display_carries_range(self):
        text = uncertain.format_verdict(True, 10)
        self.assertIn("PASS", text)
        self.assertIn("[", text)
        self.assertIn("]", text)
        self.assertNotEqual(text.strip(), "PASS")

    def test_fail_display_carries_range(self):
        text = uncertain.format_verdict(False, 10)
        self.assertIn("FAIL", text)
        self.assertIn("[", text)
        self.assertIn("]", text)
        self.assertNotEqual(text.strip(), "FAIL")

    def test_display_shows_n(self):
        self.assertIn("n=10", uncertain.format_verdict(True, 10))
        self.assertIn("n=0", uncertain.format_verdict(False, 0))

    def test_display_zero_evidence_full(self):
        text = uncertain.format_verdict(True, 0)
        self.assertIn("PASS", text)
        self.assertIn("0.0000", text)
        self.assertIn("1.0000", text)

    def test_display_narrows_with_evidence(self):
        # Rendered bounds visibly tighten: lo rises with n on PASS.
        import re

        def lo_of(n):
            m = re.search(r"\[([0-9.]+),", uncertain.format_verdict(True, n))
            return float(m.group(1))

        self.assertLess(lo_of(1), lo_of(10))
        self.assertLess(lo_of(10), lo_of(100))


class StatefulBox(unittest.TestCase):
    def test_add_and_counts(self):
        box = uncertain.UncertainVerdict()
        box.add(True)
        box.add(True)
        box.add(False)
        self.assertEqual(len(box), 3)
        self.assertEqual(box.counts(), (2, 3))

    def test_empty_is_unknown_full_width(self):
        box = uncertain.UncertainVerdict()
        self.assertEqual(len(box), 0)
        self.assertEqual(box.counts(), (0, 0))
        s = box.summary()
        self.assertEqual(s["verdict"], "UNKNOWN")
        self.assertEqual((s["lo"], s["hi"]), (0.0, 1.0))
        self.assertEqual(s["width"], 1.0)
        self.assertIn("UNKNOWN", box.display())

    def test_majority_rules(self):
        box = uncertain.UncertainVerdict()
        box.add(True)
        box.add(True)
        box.add(False)
        self.assertEqual(box.summary()["verdict"], "PASS")
        box2 = uncertain.UncertainVerdict()
        box2.add(False)
        box2.add(False)
        box2.add(True)
        self.assertEqual(box2.summary()["verdict"], "FAIL")

    def test_tie_is_unknown(self):
        box = uncertain.UncertainVerdict()
        box.add(True)
        box.add(False)
        self.assertEqual(box.summary()["verdict"], "UNKNOWN")

    def test_box_matches_functions(self):
        box = uncertain.UncertainVerdict()
        for v in (True, True, False, True):
            box.add(v)
        lo, hi = uncertain.wilson_interval(3, 4)
        blo, bhi = box.interval()
        self.assertAlmostEqual(blo, lo)
        self.assertAlmostEqual(bhi, hi)
        s = box.summary()
        self.assertAlmostEqual(s["width"], hi - lo)
        self.assertIn("[", box.display())
        self.assertIn("PASS", box.display())

    def test_box_interval_tightens(self):
        thin = uncertain.UncertainVerdict()
        thin.add(True)
        fat = uncertain.UncertainVerdict()
        for _ in range(20):
            fat.add(True)
        tlo, thi = thin.interval()
        flo, fhi = fat.interval()
        self.assertGreater(thi - tlo, fhi - flo)


class ContractUntouchedAndStdlib(unittest.TestCase):
    def test_loop_guard_score_do_not_import_uncertain(self):
        # Wave D additive + default-off: loop.py, guard.py, eval/score.py
        # must not import this module (it runs ONLY on explicit call).
        for rel in ("agent/loop.py", "agent/guard.py", "eval/score.py"):
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotIn("uncertain", src,
                             "%s must not import uncertain" % rel)

    def test_no_network_or_file_imports(self):
        # No API, no keys, no network: pure core, zero imports.
        path = os.path.join(ROOT, "agent", "trust", "uncertain.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for ln in src.splitlines():
            if ln.strip().startswith(("import ", "from ")):
                self.fail("uncertain.py must stay import-free, found: %r"
                          % ln)
        lowered = src.lower()
        for token in ("urllib", "socket", "http", "bearer", "api_key",
                      "apikey", "open(", "subprocess"):
            self.assertNotIn(token, lowered,
                             "uncertain.py must do no I/O or network: %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
