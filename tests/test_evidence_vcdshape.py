"""Task F9 (Waveform-shape features, plan item 69) red-first tests:
agent/evidence/vcdshape.py.

- VCD shape features (toggle density, first-change cycle) map to fault
  classes: features(signal_trace) -> {toggle_density, first_change};
  classify(feats) -> "stuck-at" | "wrong-constant" | "unknown".
- Anchor tests: stuck-at fixture (driven flatline) -> "stuck-at";
  wrong-constant fixture (one transition, then holds) -> "wrong-constant";
  flatline with no driver -> "unknown".
- Pure stdlib; no API, no keys, no I/O.
- Default-off: only runs on explicit call (no loop/guard edits).

PATH NOTE (flagged, not invented around): the delegation ticket named
agent/evidence/diagslice.py + tests/test_evidence_diagslice.py, but the
plan text of item 69 (the cited authority, implemented as written) names
agent/evidence/vcdshape.py + tests/test_evidence_vcdshape.py, and the
repo-wide spec (2026-10-29-machine-wave-2-design.md) mentions no
"diagslice" anywhere. This test file therefore targets vcdshape.py, and
the no-loop-wiring gate asserts NEITHER name is wired into loop/guard.

RED-first TDD: this file was written BEFORE
agent/evidence/vcdshape.py existed, so the first run must FAIL on
the missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_evidence_vcdshape.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import vcdshape  # noqa: E402  (RED before implementation)


STUCK_TRACE = [0] * 16
STUCK_ONES = [1] * 8
WRONG_CONST_TRACE = [0, 0, 0, 1, 1, 1, 1, 1]  # one change, then holds
WRONG_CONST_FALLING = [1, 1, 1, 1, 0, 0, 0, 0]
CLOCK_TRACE = [0, 1] * 8  # busy: toggles every sample


class TestStuckAtAnchor(unittest.TestCase):
    def test_driven_flatline_features(self):
        feats = vcdshape.features(STUCK_TRACE)
        self.assertEqual(feats["toggle_density"], 0.0)
        self.assertIsNone(feats["first_change"])

    def test_stuck_at_fixture_classifies(self):
        feats = vcdshape.features(STUCK_TRACE)  # driven by default
        self.assertEqual(vcdshape.classify(feats), "stuck-at")

    def test_stuck_high_also_stuck_at(self):
        feats = vcdshape.features(STUCK_ONES)
        self.assertEqual(feats["toggle_density"], 0.0)
        self.assertIsNone(feats["first_change"])
        self.assertEqual(vcdshape.classify(feats), "stuck-at")

    def test_multibit_flatline_is_stuck_at(self):
        feats = vcdshape.features([0xA5] * 10)
        self.assertEqual(feats["toggle_density"], 0.0)
        self.assertEqual(vcdshape.classify(feats), "stuck-at")


class TestWrongConstantAnchor(unittest.TestCase):
    def test_wrong_constant_features(self):
        feats = vcdshape.features(WRONG_CONST_TRACE)
        self.assertEqual(feats["first_change"], 3)
        self.assertAlmostEqual(feats["toggle_density"], 1.0 / 7.0)

    def test_wrong_constant_fixture_classifies(self):
        feats = vcdshape.features(WRONG_CONST_TRACE)
        self.assertEqual(vcdshape.classify(feats), "wrong-constant")

    def test_falling_wrong_constant_classifies(self):
        feats = vcdshape.features(WRONG_CONST_FALLING)
        self.assertEqual(feats["first_change"], 4)
        self.assertEqual(vcdshape.classify(feats), "wrong-constant")

    def test_sparse_multi_glitch_still_mostly_constant(self):
        trace = [0, 0, 1, 1, 0, 0, 0, 0]  # density 2/7 < 0.5
        feats = vcdshape.features(trace)
        self.assertEqual(vcdshape.classify(feats), "wrong-constant")


class TestUnknownAnchor(unittest.TestCase):
    def test_flatline_no_driver_is_unknown(self):
        feats = vcdshape.features(STUCK_TRACE, has_driver=False)
        self.assertEqual(feats["toggle_density"], 0.0)
        self.assertEqual(vcdshape.classify(feats), "unknown")

    def test_busy_clock_is_unknown(self):
        feats = vcdshape.features(CLOCK_TRACE)
        self.assertEqual(feats["toggle_density"], 1.0)
        self.assertEqual(vcdshape.classify(feats), "unknown")

    def test_missing_driver_key_fails_safe_to_unknown(self):
        # Hand-built flat feats with no provenance must NOT invent
        # a stuck-at diagnosis: unknown is the honest answer.
        self.assertEqual(
            vcdshape.classify({"toggle_density": 0.0, "first_change": None}),
            "unknown")

    def test_density_boundary_is_unknown(self):
        # density exactly 0.5 is busy, not mostly-constant.
        feats = vcdshape.features([0, 1, 1])  # 1 transition / 2 gaps
        self.assertEqual(feats["toggle_density"], 0.5)
        self.assertEqual(vcdshape.classify(feats), "unknown")


class TestValidation(unittest.TestCase):
    def test_empty_trace_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.features([])

    def test_bare_string_trace_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.features("000111")

    def test_generator_trace_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.features(iter([0, 0, 1, 1]))

    def test_none_sample_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.features([0, 0, None, 1])

    def test_float_sample_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.features([0.0, 0.0, 1.0])

    def test_nested_sample_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.features([0, [1], 1])

    def test_non_bool_has_driver_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.features(STUCK_TRACE, has_driver=1)

    def test_classify_non_dict_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.classify("stuck-at")

    def test_classify_missing_keys_raise(self):
        with self.assertRaises(ValueError):
            vcdshape.classify({"toggle_density": 0.0})
        with self.assertRaises(ValueError):
            vcdshape.classify({"first_change": None})

    def test_classify_density_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.classify({"toggle_density": 1.5,
                               "first_change": 0})
        with self.assertRaises(ValueError):
            vcdshape.classify({"toggle_density": -0.1,
                               "first_change": None})

    def test_classify_bool_density_rejected(self):
        with self.assertRaises(ValueError):
            vcdshape.classify({"toggle_density": False,
                               "first_change": None})

    def test_classify_inconsistent_flat_feats_raise(self):
        # density 0 but a first change claimed: caller bug, never a guess.
        with self.assertRaises(ValueError):
            vcdshape.classify({"toggle_density": 0.0, "first_change": 2})

    def test_classify_inconsistent_active_feats_raise(self):
        # density > 0 but no first change: caller bug, never a guess.
        with self.assertRaises(ValueError):
            vcdshape.classify({"toggle_density": 0.25,
                               "first_change": None})

    def test_classify_non_bool_has_driver_raises(self):
        with self.assertRaises(ValueError):
            vcdshape.classify({"toggle_density": 0.0, "first_change": None,
                               "has_driver": "yes"})


class TestDeterminism(unittest.TestCase):
    def test_same_trace_twice_equal(self):
        first = vcdshape.features(WRONG_CONST_TRACE)
        second = vcdshape.features(WRONG_CONST_TRACE)
        self.assertEqual(first, second)

    def test_does_not_mutate_input(self):
        trace = list(WRONG_CONST_TRACE)
        before = list(trace)
        vcdshape.features(trace)
        self.assertEqual(trace, before)

    def test_single_sample_trace(self):
        feats = vcdshape.features([1])
        self.assertEqual(feats["toggle_density"], 0.0)
        self.assertIsNone(feats["first_change"])
        self.assertEqual(vcdshape.classify(feats), "stuck-at")

    def test_tuple_trace_accepted(self):
        feats = vcdshape.features((0, 0, 1, 1))
        self.assertEqual(feats["first_change"], 2)
        self.assertEqual(vcdshape.classify(feats), "wrong-constant")


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        # F9 is standalone: loop.py + guard.py must not reference the new
        # module under EITHER the plan name (vcdshape) or the ticket's
        # stray name (diagslice) -- see PATH NOTE above.
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for token in ("vcdshape", "diagslice"):
                self.assertNotIn(token, text,
                                 "%s must not wire in %s" % (name, token))

    def test_no_live_api(self):
        # No MODEL_API_KEY / requests / urllib usage in the new module.
        src = os.path.join(ROOT, "agent", "evidence", "vcdshape.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        tree = ast.parse(text)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "socket", "http",
                     "openai", "anthropic"}
        self.assertTrue(imported.isdisjoint(forbidden),
                        "non-stdlib/API imports: %s"
                        % (imported & forbidden))
        lowered = text.lower()
        for token in ("model_api_key", "requests", "urllib"):
            self.assertNotIn(token, lowered,
                             "vcdshape.py must not touch live APIs: %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
