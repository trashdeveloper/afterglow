"""Task E2 (Seed portfolio scheduler) red-first tests.

- schedule(seeds, history): unseen seeds first (input order), then
  seen seeds by kills desc (ties by seed asc, deterministic).
- portfolio_kill(seeds, run_fn, is_kill): runs seeds in order,
  attributes the kill to the first seed whose result satisfies
  is_kill -> {"killed", "by_seed", "attempts"}.

Fixture: mutant killed by seed 89 but not seed 42.

Run: python3 tests/test_discover_seedportfolio.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import seedportfolio  # noqa: E402  (RED before implementation: ModuleNotFoundError)


def _fixture_run(seed):
    """Fixture mutant: killed by seed 89, survives seed 42."""
    if seed == 89:
        return "tohost: FAIL 0xdead"
    return "tohost: PASS"


def _is_kill(log):
    return "FAIL" in log


class TestScheduleOrdersByHistory(unittest.TestCase):
    def test_high_kill_seed_first(self):
        # Fixture mutant killed by 89, not 42: history favors 89.
        history = {42: 0, 89: 3}
        ordered = seedportfolio.schedule([42, 89], history)
        self.assertEqual(ordered[0], 89)
        self.assertEqual(sorted(ordered), [42, 89])

    def test_kills_descending(self):
        history = {1: 1, 2: 5, 3: 2}
        ordered = seedportfolio.schedule([1, 2, 3], history)
        self.assertEqual(ordered, [2, 3, 1])

    def test_unseen_first(self):
        # Unseen seeds run before any seen seed, regardless of kills.
        history = {42: 10}
        ordered = seedportfolio.schedule([42, 99, 100], history)
        self.assertEqual(ordered[:2], [99, 100])
        self.assertEqual(ordered[2], 42)

    def test_unseen_preserves_input_order(self):
        history = {7: 9}
        ordered = seedportfolio.schedule([5, 6, 7], history)
        self.assertEqual(ordered, [5, 6, 7])

    def test_tie_break_by_seed_asc(self):
        history = {10: 2, 20: 2, 30: 2}
        ordered = seedportfolio.schedule([30, 20, 10], history)
        self.assertEqual(ordered, [10, 20, 30])

    def test_empty_history_keeps_input_order(self):
        ordered = seedportfolio.schedule([42, 89], {})
        self.assertEqual(ordered, [42, 89])

    def test_does_not_mutate_inputs(self):
        seeds = [42, 89]
        history = {42: 0, 89: 3}
        seedportfolio.schedule(seeds, history)
        self.assertEqual(seeds, [42, 89])
        self.assertEqual(history, {42: 0, 89: 3})


class TestPortfolioKillAttributes(unittest.TestCase):
    def test_kill_attributed_to_89(self):
        calls = []

        def run_fn(seed):
            calls.append(seed)
            return _fixture_run(seed)

        result = seedportfolio.portfolio_kill([42, 89], run_fn, _is_kill)
        self.assertTrue(result["killed"])
        self.assertEqual(result["by_seed"], 89)

    def test_runs_in_given_order_stops_at_kill(self):
        calls = []

        def run_fn(seed):
            calls.append(seed)
            return _fixture_run(seed)

        result = seedportfolio.portfolio_kill([42, 89, 100], run_fn,
                                              _is_kill)
        self.assertTrue(result["killed"])
        self.assertEqual(result["by_seed"], 89)
        # Stops at the first kill: seed 100 never runs.
        self.assertEqual(calls, [42, 89])

    def test_all_survive_reports_no_kill(self):
        result = seedportfolio.portfolio_kill(
            [42, 43], lambda seed: "tohost: PASS", _is_kill)
        self.assertFalse(result["killed"])
        self.assertIsNone(result["by_seed"])

    def test_empty_portfolio_no_kill(self):
        result = seedportfolio.portfolio_kill([], _fixture_run, _is_kill)
        self.assertFalse(result["killed"])
        self.assertIsNone(result["by_seed"])


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_schedule_rejects_non_int_seed(self):
        with self.assertRaises(TypeError):
            seedportfolio.schedule([42, "nop"], {42: 1})

    def test_schedule_rejects_bool_seed(self):
        with self.assertRaises(TypeError):
            seedportfolio.schedule([True, 89], {})

    def test_schedule_rejects_bad_history(self):
        with self.assertRaises((TypeError, ValueError)):
            seedportfolio.schedule([42], {42: "many"})

    def test_portfolio_kill_rejects_non_callable(self):
        with self.assertRaises(TypeError):
            seedportfolio.portfolio_kill([42], "not-fn", _is_kill)
        with self.assertRaises(TypeError):
            seedportfolio.portfolio_kill([42], _fixture_run, "not-fn")

    def test_run_error_propagates_never_miss(self):
        def _boom(seed):
            raise RuntimeError("sim error: binary crashed")

        with self.assertRaises(RuntimeError):
            seedportfolio.portfolio_kill([42, 89], _boom, _is_kill)

    def test_is_kill_error_propagates(self):
        def _bad_pred(log):
            raise RuntimeError("predicate boom")

        with self.assertRaises(RuntimeError):
            seedportfolio.portfolio_kill([42], _fixture_run, _bad_pred)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "seedportfolio", text,
                "%s must not reference the seedportfolio module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # seedportfolio is in-memory only; it must never write files.
        with open(os.path.join(ROOT, "agent", "discover",
                               "seedportfolio.py"),
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
                         "seedportfolio must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "seedportfolio.py"),
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
