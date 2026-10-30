"""Task F3 (flaky-triage classifier, plan item 63) tests:
agent/diagnose/flaky.py.

classify(attempts) -> {verdict, confidence, reasons[, loud]}:
- REAL: every attempt fails with the same checksum.
- FLAKE: empty-output / transport errors dominate
  (wave-2 05-storm precedent: triple-EMPTY = loud FLAKE).
- SUSPECT: anything else (mixed signals).
confidence = fraction agreeing with the verdict.

RED-first TDD: this file was written BEFORE
agent/diagnose/flaky.py existed, so the first run must FAIL
on the missing module (proving the test exercises real code,
not a stub).

Run: python3 tests/test_diagnose_flaky.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from diagnose import flaky  # noqa: E402  (RED before implementation)


def _sig(checksum):
    """One signal-bearing failing attempt with the given checksum."""
    return {"checksum": checksum, "output": "tohost: FAIL\nTRAP: foo"}


EMPTY_ERR = ("live text call returned EMPTY content (latency 64027ms, "
             "request chatcmpl-01a17b44); refusing to invent a diagnosis")


def _empty():
    """One empty-output / transport-flake attempt (05-storm shape)."""
    return {"output": "", "error": EMPTY_ERR}


class RealVerdict(unittest.TestCase):
    def test_all_same_checksum_is_real(self):
        attempts = [_sig("deadbeef" * 8)] * 3
        res = flaky.classify(attempts)
        self.assertEqual(res["verdict"], "REAL")

    def test_real_confidence_is_full_agreement(self):
        attempts = [_sig("deadbeef" * 8)] * 3
        res = flaky.classify(attempts)
        self.assertEqual(res["confidence"], 1.0)

    def test_real_reasons_name_checksum(self):
        attempts = [_sig("abc123")] * 2
        res = flaky.classify(attempts)
        self.assertTrue(any("abc123" in r for r in res["reasons"]))


class FlakeVerdict(unittest.TestCase):
    def test_triple_empty_is_loud_flake(self):
        attempts = [_empty(), _empty(), _empty()]
        res = flaky.classify(attempts)
        self.assertEqual(res["verdict"], "FLAKE")
        self.assertTrue(res.get("loud"),
                        "triple-EMPTY must be a LOUD flake (fail loud)")

    def test_all_empty_confidence_is_full(self):
        attempts = [_empty(), _empty(), _empty()]
        res = flaky.classify(attempts)
        self.assertEqual(res["confidence"], 1.0)

    def test_flake_majority_confidence_is_flake_fraction(self):
        attempts = [_empty(), _empty(), _sig("abc123")]
        res = flaky.classify(attempts)
        self.assertEqual(res["verdict"], "FLAKE")
        self.assertAlmostEqual(res["confidence"], 2 / 3)

    def test_explicit_empty_flags_are_flakes(self):
        attempts = [{"empty": True}, {"empty": True},
                    {"error": "timeout after 30s"}]
        res = flaky.classify(attempts)
        self.assertEqual(res["verdict"], "FLAKE")
        self.assertTrue(res.get("loud"))


class SuspectVerdict(unittest.TestCase):
    def test_mixed_checksums_is_suspect(self):
        attempts = [_sig("aaa"), _sig("bbb"), _sig("aaa")]
        res = flaky.classify(attempts)
        self.assertEqual(res["verdict"], "SUSPECT")

    def test_mixed_confidence_is_largest_share(self):
        attempts = [_sig("aaa"), _sig("bbb"), _sig("aaa")]
        res = flaky.classify(attempts)
        self.assertAlmostEqual(res["confidence"], 2 / 3)

    def test_half_flakes_do_not_dominate(self):
        # 2 flakes of 4 is not a majority -> SUSPECT, not FLAKE.
        attempts = [_empty(), _empty(), _sig("aaa"), _sig("bbb")]
        res = flaky.classify(attempts)
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertAlmostEqual(res["confidence"], 0.5)

    def test_suspect_is_not_loud(self):
        attempts = [_sig("aaa"), _sig("bbb"), _sig("aaa")]
        res = flaky.classify(attempts)
        self.assertFalse(res.get("loud"))


class EdgeCases(unittest.TestCase):
    def test_bad_input_raises(self):
        with self.assertRaises(TypeError):
            flaky.classify(None)
        with self.assertRaises(TypeError):
            flaky.classify("not-a-list")
        with self.assertRaises(TypeError):
            flaky.classify([_sig("aaa"), "not-a-dict"])
        with self.assertRaises(ValueError):
            flaky.classify([])

    def test_single_signal_attempt_is_real(self):
        res = flaky.classify([_sig("abc")])
        self.assertEqual(res["verdict"], "REAL")
        self.assertEqual(res["confidence"], 1.0)

    def test_single_flake_attempt_is_flake(self):
        res = flaky.classify([_empty()])
        self.assertEqual(res["verdict"], "FLAKE")
        self.assertTrue(res.get("loud"))

    def test_result_shape(self):
        res = flaky.classify([_sig("a"), _sig("b")])
        self.assertTrue({"verdict", "confidence", "reasons"} <= set(res),
                          res.keys())
        self.assertIn(res["verdict"], ("REAL", "FLAKE", "SUSPECT"))
        self.assertGreaterEqual(res["confidence"], 0.0)
        self.assertLessEqual(res["confidence"], 1.0)
        self.assertTrue(res["reasons"])


class StandardGates(unittest.TestCase):
    MODULE = os.path.join(ROOT, "agent", "diagnose", "flaky.py")

    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "flaky", text,
                "%s must not reference the flaky module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_no_live_api(self):
        with open(self.MODULE, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint({"requests", "urllib"}),
                        "no live HTTP imports allowed ($0 gate): %r"
                        % (imported,))
        for token in ("MODEL_API_KEY", "urllib", "requests"):
            self.assertNotIn(token, src,
                             "no live-API usage allowed ($0 gate): %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
