"""Task A10 (Regression bisect) red-first tests: agent/discover/bisect.py.

- Given a failing stimulus list + a predicate (subset -> True still
  fails / False passes), delta-minimizes to the shortest failing
  trigger via ddmin-style chunk halving (split, test complements,
  narrow), returning the minimal trigger + reduction trail.
- Empty initial set raises; predicate errors propagate (never treated
  as pass/fail); a predicate that never fails on the full set raises
  loudly (vacuous input); termination is bounded (call count capped).

Run: python3 tests/test_discover_bisect.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from discover import bisect  # noqa: E402  (RED before implementation: ModuleNotFoundError)


CORE_100 = (7, 42, 89)


def _contains_core_predicate(core):
    """Stub predicate: fails iff the subset still contains every core item."""
    core_set = set(core)

    def _pred(subset):
        return core_set.issubset(set(subset))

    return _pred


class TestMinimizesToCore(unittest.TestCase):
    def test_100_input_trigger_minimizes_exactly_to_3_core(self):
        stimuli = list(range(100))
        result = bisect.minimize(stimuli, _contains_core_predicate(CORE_100))
        self.assertEqual(set(result["minimal"]), set(CORE_100))
        self.assertEqual(result["n_initial"], 100)
        self.assertEqual(result["n_minimal"], 3)
        # The reported minimal set must still fail ...
        self.assertTrue(_contains_core_predicate(CORE_100)(result["minimal"]))
        # ... and be 1-minimal: dropping any single element must pass.
        for i in range(len(result["minimal"])):
            single_removed = (result["minimal"][:i]
                              + result["minimal"][i + 1:])
            self.assertFalse(
                _contains_core_predicate(CORE_100)(single_removed),
                "not 1-minimal: element %r is redundant"
                % (result["minimal"][i],))

    def test_trail_logs_before_after_and_strictly_shrinks(self):
        stimuli = list(range(100))
        result = bisect.minimize(stimuli, _contains_core_predicate(CORE_100))
        trail = result["trail"]
        self.assertTrue(trail, "reduction trail must not be empty")
        for entry in trail:
            self.assertIn("before", entry)
            self.assertIn("after", entry)
            self.assertLess(
                entry["after"], entry["before"],
                "progress guarantee violated: step did not strictly "
                "shrink (%r)" % (entry,))
        self.assertEqual(trail[0]["before"], 100)
        self.assertEqual(trail[-1]["after"], 3)
        # Trail sizes must chain: each step starts where the last ended.
        for prev, cur in zip(trail, trail[1:]):
            self.assertEqual(cur["before"], prev["after"])

    def test_input_ordering_preserved_and_input_not_mutated(self):
        stimuli = list(range(100))
        snapshot = list(stimuli)
        result = bisect.minimize(stimuli, _contains_core_predicate(CORE_100))
        self.assertEqual(stimuli, snapshot)  # caller's list untouched
        self.assertEqual(result["minimal"], sorted(result["minimal"]))


class TestVacuousAndErrors(unittest.TestCase):
    def test_predicate_never_fails_on_full_set_raises(self):
        with self.assertRaises(ValueError):
            bisect.minimize(list(range(20)), lambda subset: False)

    def test_empty_initial_set_raises(self):
        calls = []

        def _pred(subset):
            calls.append(list(subset))
            return True

        with self.assertRaises(ValueError):
            bisect.minimize([], _pred)
        self.assertEqual(calls, [],
                         "predicate must never run on an empty input")

    def test_predicate_error_propagates_mid_run(self):
        # Full set (100 items) still fails; any 50-item subset raises.
        # ddmin's first halving round queries 50-item subsets, so the
        # error must surface instead of being treated as pass/fail.
        def _flaky(subset):
            if len(list(subset)) == 50:
                raise RuntimeError("sim harness blew up mid-bisect")
            return True

        with self.assertRaises(RuntimeError):
            bisect.minimize(list(range(100)), _flaky)

    def test_predicate_error_on_full_set_propagates(self):
        def _boom(subset):
            raise RuntimeError("predicate exploded on day one")

        with self.assertRaises(RuntimeError):
            bisect.minimize(list(range(10)), _boom)


class TestTerminationBounded(unittest.TestCase):
    def test_100_input_case_uses_bounded_predicate_calls(self):
        calls = []
        core = (3, 55, 91)

        def _counting(subset):
            calls.append(1)
            return set(core).issubset(set(subset))

        result = bisect.minimize(list(range(100)), _counting)
        self.assertEqual(set(result["minimal"]), set(core))
        self.assertEqual(result["calls"], len(calls))
        self.assertLess(
            len(calls), 1500,
            "termination not bounded: %d predicate calls for 100 inputs"
            % len(calls))
        # Progress guarantee bounds accepted steps: at most n-1 shrinks.
        self.assertLessEqual(len(result["trail"]), 99)


if __name__ == "__main__":
    unittest.main()
