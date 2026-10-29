"""Task D3 (uncertainty abstention, plan item 33, Wave D) tests:
agent/trust/abstain.py.

Loop abstains instead of patching when confidence is below threshold:
low-confidence input produces abstain, never a patch.

RED-first TDD: this file was written BEFORE agent/trust/abstain.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_abstain -v   (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import abstain  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ValidationRejectsJunk(unittest.TestCase):
    def test_bad_confidence_raises(self):
        for bad in (-0.1, 1.1, 2.0, float("nan"), float("inf"),
                    float("-inf"), "0.8", None, True, False,
                    [0.8], (0.8,), {"c": 0.8}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    abstain.should_abstain(bad)
                with self.assertRaises(ValueError):
                    abstain.decide(bad)

    def test_bad_threshold_raises(self):
        for bad in (0, -0.5, 1.1, 2.0, float("nan"), float("inf"),
                    "0.5", None, True, [0.5], (0.5,)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    abstain.should_abstain(0.5, threshold=bad)
                with self.assertRaises(ValueError):
                    abstain.decide(0.5, threshold=bad)

    def test_policy_bad_threshold_raises(self):
        for bad in (0, -1, 1.5, "0.5", None, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    abstain.AbstentionPolicy(threshold=bad)

    def test_policy_bad_confidence_raises(self):
        pol = abstain.AbstentionPolicy()
        for bad in (-0.1, 1.1, float("nan"), "0.9", None, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    pol.check(bad)
                with self.assertRaises(ValueError):
                    pol.decide(bad)


class ThresholdBoundary(unittest.TestCase):
    def test_default_threshold_pinned(self):
        self.assertEqual(abstain.DEFAULT_THRESHOLD, 0.5)

    def test_below_threshold_abstains(self):
        self.assertTrue(abstain.should_abstain(0.49))
        self.assertTrue(abstain.should_abstain(0.0))
        self.assertTrue(abstain.should_abstain(0.1, threshold=0.9))

    def test_at_threshold_proceeds(self):
        # Strict less-than: confidence == threshold means proceed.
        self.assertFalse(abstain.should_abstain(0.5))
        self.assertFalse(abstain.should_abstain(0.7, threshold=0.7))

    def test_above_threshold_proceeds(self):
        self.assertFalse(abstain.should_abstain(0.51))
        self.assertFalse(abstain.should_abstain(1.0))
        self.assertFalse(abstain.should_abstain(0.9, threshold=0.9 - 1e-9))

    def test_boundary_inputs_ok(self):
        self.assertTrue(abstain.should_abstain(0.0, threshold=1.0))
        self.assertFalse(abstain.should_abstain(1.0, threshold=1.0))


class DecideNeverPatchesOnAbstain(unittest.TestCase):
    def test_low_confidence_abstains(self):
        d = abstain.decide(0.2)
        self.assertEqual(d["action"], "abstain")
        self.assertEqual(d["confidence"], 0.2)
        self.assertEqual(d["threshold"], 0.5)

    def test_abstain_drops_supplied_patch(self):
        # Core guarantee: abstain NEVER carries a patch, even if the
        # caller passes one in.
        patch = "--- a/rtl/uart.v\n+++ b/rtl/uart.v\n@@ -1 +1 @@\n-x\n+y\n"
        d = abstain.decide(0.2, patch=patch)
        self.assertEqual(d["action"], "abstain")
        self.assertIsNone(d["patch"])

    def test_abstain_reason_names_threshold(self):
        d = abstain.decide(0.2, threshold=0.6)
        self.assertIn("reason", d)
        self.assertIn("0.2", d["reason"])
        self.assertIn("0.6", d["reason"])

    def test_high_confidence_proceeds_with_patch(self):
        patch = "--- a/rtl/uart.v\n+++ b/rtl/uart.v\n@@ -1 +1 @@\n-x\n+y\n"
        d = abstain.decide(0.9, patch=patch)
        self.assertEqual(d["action"], "proceed")
        self.assertEqual(d["patch"], patch)
        self.assertEqual(d["confidence"], 0.9)

    def test_proceed_without_patch_is_none(self):
        d = abstain.decide(0.9)
        self.assertEqual(d["action"], "proceed")
        self.assertIsNone(d["patch"])

    def test_decide_matches_should_abstain(self):
        for conf in (0.0, 0.2, 0.49, 0.5, 0.51, 0.9, 1.0):
            with self.subTest(conf=conf):
                d = abstain.decide(conf, patch="P")
                expect_abstain = abstain.should_abstain(conf)
                self.assertEqual(d["action"] == "abstain", expect_abstain)
                if expect_abstain:
                    self.assertIsNone(d["patch"])
                else:
                    self.assertEqual(d["patch"], "P")

    def test_custom_threshold_respected(self):
        d = abstain.decide(0.65, threshold=0.7, patch="P")
        self.assertEqual(d["action"], "abstain")
        self.assertIsNone(d["patch"])
        d2 = abstain.decide(0.65, threshold=0.6, patch="P")
        self.assertEqual(d2["action"], "proceed")
        self.assertEqual(d2["patch"], "P")

    def test_sweep_low_never_carries_patch(self):
        # The task test: every low-confidence input abstains, none
        # carries a patch.
        for i in range(50):
            conf = i / 100.0  # 0.00 .. 0.49, all below default 0.5
            d = abstain.decide(conf, patch="PATCH-%d" % i)
            with self.subTest(conf=conf):
                self.assertEqual(d["action"], "abstain")
                self.assertIsNone(d["patch"])


class PolicyStateful(unittest.TestCase):
    def test_counts_tracked(self):
        pol = abstain.AbstentionPolicy(threshold=0.5)
        pol.check(0.2)
        pol.check(0.9)
        pol.decide(0.1, patch="P")
        s = pol.stats()
        self.assertEqual(s["n_abstained"], 2)
        self.assertEqual(s["n_proceeded"], 1)
        self.assertEqual(s["n"], 3)
        self.assertEqual(s["threshold"], 0.5)

    def test_policy_decide_drops_patch_on_abstain(self):
        pol = abstain.AbstentionPolicy(threshold=0.8)
        d = pol.decide(0.3, patch="P")
        self.assertEqual(d["action"], "abstain")
        self.assertIsNone(d["patch"])
        d2 = pol.decide(0.85, patch="P")
        self.assertEqual(d2["action"], "proceed")
        self.assertEqual(d2["patch"], "P")

    def test_policy_default_threshold(self):
        pol = abstain.AbstentionPolicy()
        self.assertEqual(pol.threshold, 0.5)
        self.assertTrue(pol.check(0.1))
        self.assertFalse(pol.check(0.9))

    def test_policy_empty_stats(self):
        s = abstain.AbstentionPolicy().stats()
        self.assertEqual(s["n"], 0)
        self.assertEqual(s["n_abstained"], 0)
        self.assertEqual(s["n_proceeded"], 0)


class DefaultOffContract(unittest.TestCase):
    def test_loop_guard_score_do_not_import_abstain(self):
        # Wave D additive + default-off: loop.py, guard.py, eval/score.py
        # must not import this module (it runs ONLY on explicit call).
        for rel in ("agent/loop.py", "agent/guard.py", "eval/score.py"):
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path) as f:
                src = f.read()
            self.assertNotIn("abstain", src, "%s must not import abstain" % rel)

    def test_no_network_or_file_imports(self):
        # No API, no keys, no network: pure core. Only stdlib-free
        # (no imports at all, like calibrate.py).
        path = os.path.join(ROOT, "agent", "trust", "abstain.py")
        with open(path) as f:
            src = f.read()
        self.assertNotIn("import ", src)
        for token in ("urllib", "socket", "http", "open(",
                      "API_KEY", "request"):
            self.assertNotIn(token, src)


if __name__ == "__main__":
    unittest.main()
