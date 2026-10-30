"""Task G6 (repair portfolio race, plan item 76) tests:
agent/repair/repairrace.py.

Interface (plan text, verbatim):

  race(fault, strategies, verify_fn) -> {winner, attempts}

``strategies`` are injectable callables (template/model/transfer shapes;
all simulated, no live API). Each strategy takes the fault and returns a
candidate; ``verify_fn(candidate)`` is truthy when that candidate greens.
The return is ``{"winner": <strategy name or None>, "attempts": [...]}``
where ``attempts`` logs one entry per strategy in run order.

Contract:
- Fixture where the template strategy greens: winner is "template" and
  all three attempts are logged (the race runs every strategy; the
  winner is the first green in order).
- First-green-wins: a later green does not displace an earlier green.
- All-red race: winner is None (honest red, never an invented winner),
  with every attempt logged as failed.
- Malformed input (None fault, empty/non-list strategies, non-callable
  strategy, missing/non-callable verify_fn) raises TypeError/ValueError
  before any verdict.
- Strategy and verify_fn exceptions propagate unchanged -- never caught,
  never a verdict.
- Fault is opaque and passed through untouched (strategy receives the
  exact fault object).
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no repairrace wiring).

RED-first TDD: this file was written BEFORE
agent/repair/repairrace.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_repairrace.py -v   (from repo root)
"""
import ast
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import repairrace  # noqa: E402  (RED before implementation)


def _fault(case="picosoc-uart-div"):
    return {"case": case, "checksum": "sha256:abc123", "seed": 42}


def template(fault):
    return {"patch": "template-fix", "for": fault["case"]}


def model(fault):
    return {"patch": "model-fix", "for": fault["case"]}


def transfer(fault):
    return {"patch": "transfer-fix", "for": fault["case"]}


def _strategies():
    return [template, model, transfer]


class TemplateWinner(unittest.TestCase):
    def test_template_greens_winner_is_template(self):
        result = repairrace.race(
            _fault(), _strategies(),
            verify_fn=lambda cand: cand["patch"] == "template-fix")
        self.assertEqual(result["winner"], "template")

    def test_all_three_attempts_logged(self):
        result = repairrace.race(
            _fault(), _strategies(),
            verify_fn=lambda cand: cand["patch"] == "template-fix")
        attempts = result["attempts"]
        self.assertEqual(len(attempts), 3)
        self.assertEqual(
            [a["strategy"] for a in attempts],
            ["template", "model", "transfer"])
        self.assertEqual(
            [bool(a["ok"]) for a in attempts], [True, False, False])


class FirstGreenWins(unittest.TestCase):
    def test_later_green_does_not_displace_earlier_green(self):
        result = repairrace.race(
            _fault(), _strategies(),
            verify_fn=lambda cand: cand["patch"].endswith("-fix"))
        self.assertEqual(result["winner"], "template")
        self.assertEqual(len(result["attempts"]), 3)

    def test_middle_strategy_wins_when_only_it_greens(self):
        result = repairrace.race(
            _fault(), _strategies(),
            verify_fn=lambda cand: cand["patch"] == "model-fix")
        self.assertEqual(result["winner"], "model")
        self.assertEqual(
            [bool(a["ok"]) for a in result["attempts"]],
            [False, True, False])

    def test_all_red_winner_is_none_honest_red(self):
        result = repairrace.race(
            _fault(), _strategies(), verify_fn=lambda cand: False)
        self.assertIsNone(result["winner"])
        self.assertEqual(len(result["attempts"]), 3)
        self.assertTrue(all(not a["ok"] for a in result["attempts"]))


class RefusesVacuousInput(unittest.TestCase):
    def test_none_fault_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            repairrace.race(
                None, _strategies(), verify_fn=lambda cand: True)

    def test_empty_strategies_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            repairrace.race(
                _fault(), [], verify_fn=lambda cand: True)

    def test_non_list_strategies_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            repairrace.race(
                _fault(), "template", verify_fn=lambda cand: True)

    def test_non_callable_strategy_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            repairrace.race(
                _fault(), [template, "model", transfer],
                verify_fn=lambda cand: True)

    def test_missing_verify_fn_raises_loudly(self):
        with self.assertRaises((TypeError, ValueError)):
            repairrace.race(_fault(), _strategies())
        with self.assertRaises((TypeError, ValueError)):
            repairrace.race(
                _fault(), _strategies(), verify_fn="not-callable")


class HonestyContract(unittest.TestCase):
    def test_strategy_exceptions_propagate_never_a_verdict(self):
        def _boom(fault):
            raise RuntimeError("sim backend exploded: boom")
        with self.assertRaises(RuntimeError):
            repairrace.race(
                _fault(), [_boom], verify_fn=lambda cand: True)

    def test_verify_exceptions_propagate_never_a_verdict(self):
        def _boom(cand):
            raise RuntimeError("verifier exploded: boom")
        with self.assertRaises(RuntimeError):
            repairrace.race(
                _fault(), _strategies(), verify_fn=_boom)

    def test_fault_passed_through_untouched(self):
        seen = []

        def _spy(fault):
            seen.append(fault)
            return {"patch": "template-fix"}

        fault = _fault()
        snapshot = copy.deepcopy(fault)
        repairrace.race(
            fault, [_spy], verify_fn=lambda cand: True)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0], fault)
        self.assertEqual(fault, snapshot)

    def test_deterministic_same_inputs_same_winner(self):
        first = repairrace.race(
            _fault(), _strategies(),
            verify_fn=lambda cand: cand["patch"] == "model-fix")
        second = repairrace.race(
            _fault(), _strategies(),
            verify_fn=lambda cand: cand["patch"] == "model-fix")
        self.assertEqual(first, second)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("repairrace", text,
                             "%s must not wire in repairrace" % name)

    def test_no_live_api(self):
        src = os.path.join(ROOT, "agent", "repair", "repairrace.py")
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
                             "repairrace.py must not touch live APIs: %r"
                             % token)

    def test_standalone_no_loop_guard_imports_no_io(self):
        path = os.path.join(ROOT, "agent", "repair", "repairrace.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                for alias in node.names:
                    imported.add(alias.name)
        for banned in ("agent.loop", "agent.guard", "agent.trust"):
            self.assertFalse(any(banned in name for name in imported),
                             "repairrace must be standalone "
                             "(no loop/guard/trust import), got %r" % imported)
        for banned in ("try_apply_diff", "save_registry", "record_fixed",
                       "open("):
            self.assertNotIn(banned, src,
                             "repairrace must stay default-off/read-only: %r"
                             % banned)


if __name__ == "__main__":
    unittest.main()
