"""Task G4 (equivalent-mutant filter, plan item 74) tests:
agent/repair/equivfilter.py.

Interface (plan text, verbatim):

  screen(mutant_tree, pristine_tree, case) -> "REAL-FAULT"|"NOT-A-BUG"

Consumes agent/repair/equiv.py::check_equivalence: identical traces
-> NOT-A-BUG (never patched); behavior-changed traces -> REAL-FAULT.

Contract:
- Behavior-identical fixture mutant (same sim log on every stimulus
  seed, judged by the REAL C3 equivalence check with injected
  build/run fns) -> "NOT-A-BUG".
- Behavior-changed fixture mutant (one seed's trace differs) ->
  "REAL-FAULT".
- The verdict is a bare label string only; screen never patches.
- Errors propagate loudly: build/run failure, blank tree, bad case or
  bad (master_seed, n) raise from equiv -- never an invented verdict.
- Pure stdlib + repo imports (agent.repair.equiv); no API, no keys.
  Default-off: explicit call only; agent/loop.py and agent/guard.py
  untouched (no equivfilter wiring).

RED-first TDD: this file was written BEFORE
agent/repair/equivfilter.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_equivfilter.py -v   (from repo root)
"""
import ast
import hashlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import equiv  # noqa: E402
from agent.repair import equivfilter  # noqa: E402  (RED before implementation)


def _tiny_case():
    return {
        "name": "equivfilter-tiny",
        "project": "picorv32",
        "files": ["picorv32.v"],
        "top": "tb_picorv32_fetch",
        "tb": "picorv32-fetch_tb.sv",
        "oracle_pass": "tohost: PASS",
    }


def _counting_build(calls, fail_on=()):
    def _build(tree, case):
        calls.append(tree)
        if tree in fail_on:
            raise RuntimeError("verilator build failed (exit 1): boom")
        return "/fake/binary-%s" % os.path.basename(str(tree))
    return _build


def _tree_keyed_run(log_by_tree_seed):
    """run_fn(binary, seed) keyed by (binary, seed); missing -> loud."""
    def _run(binary, seed):
        key = (binary, int(seed))
        if key not in log_by_tree_seed:
            raise RuntimeError("sim error: no log for %r" % (key,))
        return log_by_tree_seed[key]
    return _run


IDENTICAL_LOG = "tohost: PASS\nCYCLES=10\nREG x5=123\n"
CHANGED_LOG = "tohost: PASS\nCYCLES=10\nREG x5=999\n"


def _logs_for(seeds, mutant_log, pristine_log=None):
    """Map (binary, seed) -> log for a mutant-vs-pristine pair."""
    pristine_log = IDENTICAL_LOG if pristine_log is None else pristine_log
    logs = {}
    for s in seeds:
        logs[("/fake/binary-mutant", s)] = mutant_log
        logs[("/fake/binary-pristine", s)] = pristine_log
    return logs


class ScreenIdenticalMutant(unittest.TestCase):
    def test_behavior_identical_mutant_is_not_a_bug(self):
        seeds = equiv.generate_stimulus(99, n=4)
        verdict = equivfilter.screen(
            "/fake/mutant", "/fake/pristine", _tiny_case(),
            master_seed=99, n=4,
            build_fn=_counting_build([]),
            run_fn=_tree_keyed_run(_logs_for(seeds, IDENTICAL_LOG)))
        self.assertEqual(verdict, "NOT-A-BUG")

    def test_verdict_is_bare_label_string(self):
        seeds = equiv.generate_stimulus(99, n=4)
        verdict = equivfilter.screen(
            "/fake/mutant", "/fake/pristine", _tiny_case(),
            master_seed=99, n=4,
            build_fn=_counting_build([]),
            run_fn=_tree_keyed_run(_logs_for(seeds, IDENTICAL_LOG)))
        self.assertIsInstance(verdict, str)
        self.assertIn(verdict, ("REAL-FAULT", "NOT-A-BUG"))


class ScreenChangedMutant(unittest.TestCase):
    def test_behavior_changed_mutant_is_real_fault(self):
        seeds = equiv.generate_stimulus(99, n=4)
        logs = _logs_for(seeds, IDENTICAL_LOG)
        # One seed diverges on the mutant side: behavior changed.
        logs[("/fake/binary-mutant", seeds[0])] = CHANGED_LOG
        verdict = equivfilter.screen(
            "/fake/mutant", "/fake/pristine", _tiny_case(),
            master_seed=99, n=4,
            build_fn=_counting_build([]),
            run_fn=_tree_keyed_run(logs))
        self.assertEqual(verdict, "REAL-FAULT")

    def test_fully_divergent_mutant_is_real_fault(self):
        seeds = equiv.generate_stimulus(99, n=4)
        verdict = equivfilter.screen(
            "/fake/mutant", "/fake/pristine", _tiny_case(),
            master_seed=99, n=4,
            build_fn=_counting_build([]),
            run_fn=_tree_keyed_run(_logs_for(seeds, CHANGED_LOG)))
        self.assertEqual(verdict, "REAL-FAULT")


class ScreenRefusesVerdictOnFailure(unittest.TestCase):
    def test_build_failure_raises_never_invents_verdict(self):
        seeds = equiv.generate_stimulus(99, n=4)
        with self.assertRaises(RuntimeError):
            equivfilter.screen(
                "/fake/mutant", "/fake/pristine", _tiny_case(),
                master_seed=99, n=4,
                build_fn=_counting_build([], fail_on=("/fake/mutant",)),
                run_fn=_tree_keyed_run(_logs_for(seeds, IDENTICAL_LOG)))

    def test_blank_tree_raises(self):
        for bad in ("", "   ", None):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    equivfilter.screen(
                        bad, "/fake/pristine", _tiny_case(),
                        build_fn=_counting_build([]),
                        run_fn=_tree_keyed_run({}))

    def test_bad_case_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            equivfilter.screen(
                "/fake/mutant", "/fake/pristine", 12345,
                build_fn=_counting_build([]),
                run_fn=_tree_keyed_run({}))


class ScreenConsumesEquivalenceCheck(unittest.TestCase):
    def test_module_routes_through_equiv_check_equivalence(self):
        path = os.path.join(ROOT, "agent", "repair", "equivfilter.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("check_equivalence", text,
                      "equivfilter must consume equiv.check_equivalence")
        tree = ast.parse(text)
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
        self.assertTrue(
            any("equiv" in name for name in imported),
            "equivfilter must import the equiv module, got %r" % imported)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("equivfilter", text,
                             "%s must not wire in equivfilter" % name)

    def test_no_live_api(self):
        src = os.path.join(ROOT, "agent", "repair", "equivfilter.py")
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
                             "equivfilter.py must not touch live APIs: %r"
                             % token)

    def test_module_stays_default_off(self):
        path = os.path.join(ROOT, "agent", "repair", "equivfilter.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("from loop import", "from guard import",
                       "import loop", "import guard", "try_apply_diff"):
            self.assertNotIn(banned, src,
                             "equivfilter must stay default-off "
                             "(no loop/guard wiring): %r" % banned)


if __name__ == "__main__":
    unittest.main()
