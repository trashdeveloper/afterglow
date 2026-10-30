"""Task F10 (Diagnosis tournament, plan item 70) red-first tests:
agent/evidence/diagtourney.py.

- compete(fault, strategy_a, strategy_b, verifier_fn) -> {winner, evidence}
  (verifier resims both candidate neighborhoods).
- Anchor: fixture where strategy B's line verifies and A's does not:
  B wins with resim evidence logged.
- Strategies are injectable callables; no live API.
- Pure stdlib; no API, no keys, no I/O.
- Default-off: only runs on explicit call (no loop/guard edits).

RED-first TDD: this file was written BEFORE
agent/evidence/diagtourney.py existed, so the first run must FAIL on
the missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_evidence_diagtourney.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import diagtourney  # noqa: E402  (RED before implementation)


FAULT = {"signal": "uart_div", "expect": 42}

GOOD_CANDIDATE = {"file": "uart.v", "line": 42}
BAD_CANDIDATE = {"file": "uart.v", "line": 10}


def strategy_bad(fault):
    return dict(BAD_CANDIDATE)


def strategy_good(fault):
    return dict(GOOD_CANDIDATE)


def make_verifier(good_line=42):
    """Verifier stub: resim passes only for the good line."""
    calls = []

    def verifier(fault, candidate):
        calls.append((fault, dict(candidate)))
        return candidate.get("line") == good_line

    verifier.calls = calls
    return verifier


class TestBWinsAnchor(unittest.TestCase):
    def test_b_wins_when_only_b_verifies(self):
        verifier = make_verifier()
        out = diagtourney.compete(FAULT, strategy_bad, strategy_good,
                                  verifier)
        self.assertEqual(out["winner"], "b")

    def test_b_win_logs_resim_evidence(self):
        verifier = make_verifier()
        out = diagtourney.compete(FAULT, strategy_bad, strategy_good,
                                  verifier)
        ev = out["evidence"]
        self.assertEqual(ev["candidate_a"], BAD_CANDIDATE)
        self.assertEqual(ev["candidate_b"], GOOD_CANDIDATE)
        self.assertFalse(ev["verified_a"])
        self.assertTrue(ev["verified_b"])

    def test_verifier_resims_both_neighborhoods(self):
        verifier = make_verifier()
        diagtourney.compete(FAULT, strategy_bad, strategy_good, verifier)
        # Both candidates resimmed even though A fails: 2 calls, one per
        # candidate, in A-then-B order.
        self.assertEqual(len(verifier.calls), 2)
        self.assertEqual(verifier.calls[0][1], BAD_CANDIDATE)
        self.assertEqual(verifier.calls[1][1], GOOD_CANDIDATE)


class TestAWinsMirror(unittest.TestCase):
    def test_a_wins_when_only_a_verifies(self):
        verifier = make_verifier()
        out = diagtourney.compete(FAULT, strategy_good, strategy_bad,
                                  verifier)
        self.assertEqual(out["winner"], "a")
        self.assertTrue(out["evidence"]["verified_a"])
        self.assertFalse(out["evidence"]["verified_b"])


class TestTies(unittest.TestCase):
    def test_both_verify_is_tie(self):
        verifier = make_verifier()
        out = diagtourney.compete(FAULT, strategy_good, strategy_good,
                                  verifier)
        self.assertEqual(out["winner"], "tie")
        self.assertTrue(out["evidence"]["verified_a"])
        self.assertTrue(out["evidence"]["verified_b"])

    def test_neither_verifies_is_tie(self):
        verifier = make_verifier()
        out = diagtourney.compete(FAULT, strategy_bad, strategy_bad,
                                  verifier)
        self.assertEqual(out["winner"], "tie")
        self.assertFalse(out["evidence"]["verified_a"])
        self.assertFalse(out["evidence"]["verified_b"])


class TestValidation(unittest.TestCase):
    def test_non_callable_strategy_raises(self):
        verifier = make_verifier()
        with self.assertRaises(TypeError):
            diagtourney.compete(FAULT, "not-callable", strategy_good,
                                verifier)
        with self.assertRaises(TypeError):
            diagtourney.compete(FAULT, strategy_good, None, verifier)

    def test_non_callable_verifier_raises(self):
        with self.assertRaises(TypeError):
            diagtourney.compete(FAULT, strategy_bad, strategy_good,
                                "not-callable")

    def test_result_shape(self):
        verifier = make_verifier()
        out = diagtourney.compete(FAULT, strategy_bad, strategy_good,
                                  verifier)
        self.assertEqual(set(out.keys()), {"winner", "evidence"})
        self.assertIn(out["winner"], ("a", "b", "tie"))


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_twice_equal(self):
        first = diagtourney.compete(FAULT, strategy_bad, strategy_good,
                                    make_verifier())
        second = diagtourney.compete(FAULT, strategy_bad, strategy_good,
                                     make_verifier())
        self.assertEqual(first, second)

    def test_does_not_mutate_fault(self):
        fault = dict(FAULT)
        diagtourney.compete(fault, strategy_bad, strategy_good,
                            make_verifier())
        self.assertEqual(fault, FAULT)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("diagtourney", text,
                             "%s must not wire in diagtourney" % name)

    def test_no_live_api(self):
        src = os.path.join(ROOT, "agent", "evidence", "diagtourney.py")
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
                             "diagtourney.py must not touch live APIs: %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
