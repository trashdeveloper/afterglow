"""Task E8 (Stimulus minimizer, plan item 58) red-first tests.

- minimize(vector, run_fn) -> minimal vector (ddmin-style) where
  run_fn(vector) -> True means the vector still exposes the fault.
- Fixture: injected run_fn exposes only when a specific subset is
  present; the minimizer must return a 1-minimal subset and must never
  return a non-exposing vector.
- Plan gate: a 20-step failing stimulus minimizes to <=5 steps still RED
  via the fixture predicate.

Run: python3 -m unittest tests.test_discover_stimmin -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import stimmin  # noqa: E402  (RED before implementation: ModuleNotFoundError)


# ---------------------------------------------------------------------------
# Fixtures: run_fn exposes only when a specific subset is present.
# ---------------------------------------------------------------------------
REQUIRED = frozenset((4, 11))


def _fixture_run(vector):
    """Fixture predicate: exposes iff BOTH steps 4 and 11 are present."""
    return REQUIRED.issubset(set(vector))


def _assert_one_minimal(testcase, result, run_fn):
    """1-minimal: result exposes, but removing ANY single element breaks it."""
    testcase.assertTrue(run_fn(list(result)),
                        "minimized vector must still expose the fault")
    for i in range(len(result)):
        reduced = list(result[:i]) + list(result[i + 1:])
        testcase.assertFalse(
            run_fn(reduced),
            "not 1-minimal: element %r at index %d is redundant"
            % (result[i], i))


class TestMinimizesToSubset(unittest.TestCase):
    def test_plan_gate_twenty_steps_to_five_or_fewer_still_red(self):
        stimulus = list(range(20))
        self.assertTrue(_fixture_run(stimulus))  # fixture RED before minimize
        minimal = stimmin.minimize(stimulus, _fixture_run)
        self.assertTrue(_fixture_run(list(minimal)),
                        "minimized stimulus must still be RED")
        self.assertLessEqual(len(minimal), 5,
                             "20-step stimulus must shrink to <=5 steps")

    def test_returns_one_minimal_subset(self):
        stimulus = list(range(20))
        minimal = stimmin.minimize(stimulus, _fixture_run)
        _assert_one_minimal(self, minimal, _fixture_run)

    def test_never_returns_non_exposing_vector(self):
        for stimulus in (list(range(20)), list(reversed(range(20))),
                         [0, 4, 1, 11, 2], [4, 11]):
            minimal = stimmin.minimize(list(stimulus), _fixture_run)
            self.assertTrue(_fixture_run(list(minimal)),
                            "must never return a non-exposing vector for %r"
                            % (stimulus,))

    def test_result_is_subsequence_of_input(self):
        stimulus = list(range(20))
        minimal = list(stimmin.minimize(stimulus, _fixture_run))
        self.assertTrue(set(minimal).issubset(set(stimulus)))
        # Order preserved: filtering the input by the result reproduces it.
        self.assertEqual([x for x in stimulus if x in set(minimal)], minimal)

    def test_single_required_step(self):
        run_fn = lambda vec: 7 in set(vec)  # noqa: E731
        minimal = stimmin.minimize(list(range(10)), run_fn)
        self.assertEqual(list(minimal), [7])
        _assert_one_minimal(self, minimal, run_fn)

    def test_already_minimal_input_returned_unchanged(self):
        minimal = stimmin.minimize([4, 11], _fixture_run)
        self.assertEqual(sorted(minimal), [4, 11])

    def test_positional_and_keyword_spellings_agree(self):
        stimulus = list(range(20))
        by_pos = list(stimmin.minimize(stimulus, _fixture_run))
        by_kw = list(stimmin.minimize(vector=list(stimulus),
                                      run_fn=_fixture_run))
        by_plan_kw = list(stimmin.minimize(stimulus=list(stimulus),
                                           still_red_fn=_fixture_run))
        self.assertEqual(by_pos, by_kw)
        self.assertEqual(by_pos, by_plan_kw)

    def test_does_not_mutate_input(self):
        stimulus = list(range(20))
        snapshot = list(stimulus)
        stimmin.minimize(stimulus, _fixture_run)
        self.assertEqual(stimulus, snapshot)

    def test_deterministic_across_runs(self):
        stimulus = list(range(20))
        first = list(stimmin.minimize(list(stimulus), _fixture_run))
        second = list(stimmin.minimize(list(stimulus), _fixture_run))
        self.assertEqual(first, second)


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_non_exposing_input_raises(self):
        with self.assertRaises(ValueError):
            stimmin.minimize([0, 1, 2], _fixture_run)

    def test_empty_input_raises(self):
        with self.assertRaises(ValueError):
            stimmin.minimize([], _fixture_run)

    def test_rejects_non_callable_predicate(self):
        with self.assertRaises(TypeError):
            stimmin.minimize([4, 11], "not-callable")

    def test_rejects_non_sequence_stimulus(self):
        with self.assertRaises(TypeError):
            stimmin.minimize(42, _fixture_run)

    def test_predicate_exception_propagates(self):
        def boom(vec):
            raise RuntimeError("sim exploded")
        with self.assertRaises(RuntimeError):
            stimmin.minimize([4, 11, 5], boom)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "stimmin", text,
                "%s must not reference the stimmin module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # stimmin is in-memory only; it must never write files.
        with open(os.path.join(ROOT, "agent", "discover",
                               "stimmin.py"),
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
                         "stimmin must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "stimmin.py"),
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
