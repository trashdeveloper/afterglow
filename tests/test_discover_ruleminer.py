"""Task E3 (Temporal assertion miner) red-first tests.

- mine_rules(passing_traces) -> [rule]: mines ready->valid style
  precedence orderings (rule = {antecedent, consequent, support}).
- check_rule(rule, trace) -> True | {violations}: True when the trace
  honors the ordering; a dict naming the violations otherwise.

Fixture: 3 honest UART handshake traces (every valid is preceded by
ready); a doctored trace asserts valid without any prior ready.

Run: python3 -m unittest tests.test_discover_ruleminer -v (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import ruleminer  # noqa: E402  (RED before implementation: ModuleNotFoundError)


def _honest_trace(offset=0):
    """Honest UART handshake: every valid is preceded by ready."""
    idle = {"ready": 0, "valid": 0}
    trace = [dict(idle) for _ in range(offset)]
    trace += [
        {"ready": 1, "valid": 0},
        {"ready": 1, "valid": 1},
        {"ready": 0, "valid": 0},
        {"ready": 1, "valid": 0},
        {"ready": 1, "valid": 1},
        {"ready": 0, "valid": 0},
    ]
    return trace


def _pristine_trace():
    """Fresh honest trace (not used for mining): rule must hold."""
    return [
        {"ready": 0, "valid": 0},
        {"ready": 1, "valid": 0},
        {"ready": 1, "valid": 0},
        {"ready": 1, "valid": 1},
        {"ready": 0, "valid": 0},
    ]


def _doctored_trace():
    """Dishonest trace: valid asserted with no prior ready anywhere."""
    return [
        {"ready": 0, "valid": 0},
        {"ready": 0, "valid": 1},
        {"ready": 0, "valid": 0},
    ]


def _ready_valid_rule(rules):
    for rule in rules:
        if (rule.get("antecedent") == "ready"
                and rule.get("consequent") == "valid"):
            return rule
    return None


class TestMineRules(unittest.TestCase):
    def test_mines_ready_valid_from_honest_traces(self):
        traces = [_honest_trace(0), _honest_trace(1), _honest_trace(2)]
        rules = ruleminer.mine_rules(traces)
        rule = _ready_valid_rule(rules)
        self.assertIsNotNone(
            rule, "honest UART traces must yield a ready->valid rule")
        self.assertEqual(rule["support"], 3)

    def test_rule_shape(self):
        traces = [_honest_trace(0), _honest_trace(1), _honest_trace(2)]
        for rule in ruleminer.mine_rules(traces):
            self.assertIn("antecedent", rule)
            self.assertIn("consequent", rule)
            self.assertIn("support", rule)
            self.assertIsInstance(rule["antecedent"], str)
            self.assertIsInstance(rule["consequent"], str)
            self.assertNotEqual(rule["antecedent"], rule["consequent"])

    def test_empty_corpus_mines_nothing(self):
        self.assertEqual(ruleminer.mine_rules([]), [])

    def test_rejects_non_list_corpus(self):
        with self.assertRaises(TypeError):
            ruleminer.mine_rules("not-a-corpus")


class TestCheckRule(unittest.TestCase):
    def test_mined_rule_holds_on_pristine(self):
        traces = [_honest_trace(0), _honest_trace(1), _honest_trace(2)]
        rule = _ready_valid_rule(ruleminer.mine_rules(traces))
        self.assertTrue(ruleminer.check_rule(rule, _pristine_trace()) is True)

    def test_valid_without_ready_falsifies_with_named_violation(self):
        traces = [_honest_trace(0), _honest_trace(1), _honest_trace(2)]
        rule = _ready_valid_rule(ruleminer.mine_rules(traces))
        result = ruleminer.check_rule(rule, _doctored_trace())
        self.assertIsInstance(result, dict)
        self.assertIn("violations", result)
        self.assertTrue(result["violations"],
                        "valid-without-ready must produce a violation")
        named = " ".join(str(v) for v in result["violations"])
        self.assertIn("ready", named)
        self.assertIn("valid", named)
        cycles = [v["cycle"] for v in result["violations"]]
        self.assertIn(1, cycles)

    def test_honest_trace_checks_true(self):
        rule = {"antecedent": "ready", "consequent": "valid", "support": 3}
        self.assertTrue(
            ruleminer.check_rule(rule, _honest_trace(0)) is True)

    def test_empty_trace_holds_vacuously(self):
        rule = {"antecedent": "ready", "consequent": "valid", "support": 1}
        self.assertTrue(ruleminer.check_rule(rule, []) is True)

    def test_bad_rule_raises_loudly(self):
        with self.assertRaises((TypeError, ValueError, KeyError)):
            ruleminer.check_rule({"antecedent": "ready"}, _honest_trace(0))
        with self.assertRaises((TypeError, ValueError, KeyError)):
            ruleminer.check_rule({"antecedent": "ready",
                                  "consequent": "ready",
                                  "support": 1},
                                 _honest_trace(0))

    def test_bad_trace_raises_loudly(self):
        rule = {"antecedent": "ready", "consequent": "valid", "support": 1}
        with self.assertRaises(TypeError):
            ruleminer.check_rule(rule, "not-a-trace")


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "ruleminer", text,
                "%s must not reference the ruleminer module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # ruleminer is in-memory only; it must never write files.
        with open(os.path.join(ROOT, "agent", "discover",
                               "ruleminer.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        writes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in ("write_text", "dump", "dumpf", "copyfile",
                            "copy", "move", "rename", "makedirs", "mkdir"):
                    writes.add(name)
                if name == "open":
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value,
                                                           ast.Constant):
                            if any(c in str(kw.value.value) for c in "wax+"):
                                writes.add("open(%r)" % kw.value.value)
                    if (len(node.args) >= 2
                            and isinstance(node.args[1], ast.Constant)
                            and any(c in str(node.args[1].value)
                                    for c in "wax+")):
                        writes.add("open(%r)" % node.args[1].value)
        self.assertEqual(writes, set(),
                         "ruleminer must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "ruleminer.py"),
                  encoding="utf-8") as fh:
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
