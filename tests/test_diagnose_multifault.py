"""Task F1 (multi-fault localizer, plan item 61) tests:
agent/diagnose/multifault.py.

Union of per-signal suspect sets, ranked by occurrence count
(number of signals suspecting the line) then cone overlap
(number of cones containing the line).

RED-first TDD: this file was written BEFORE
agent/diagnose/multifault.py existed, so the first run must FAIL
on the missing module (proving the test exercises real code,
not a stub).

Run: python3 tests/test_diagnose_multifault.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from diagnose import multifault  # noqa: E402  (RED before implementation)


class SharedFaultRanksFirst(unittest.TestCase):
    def test_shared_line_ranks_first(self):
        # Two signals share one fault + one unique each; shared ranks first.
        fault_signals = {
            "sigA": ["uart.v:42", "uart.v:10"],
            "sigB": ["uart.v:42", "uart.v:99"],
        }
        cones = {}
        ranked = multifault.localize(fault_signals, cones)
        self.assertEqual(ranked[0], "uart.v:42")

    def test_result_is_union_of_suspect_sets(self):
        fault_signals = {
            "sigA": ["uart.v:42", "uart.v:10"],
            "sigB": ["uart.v:42", "uart.v:99"],
        }
        ranked = multifault.localize(fault_signals, {})
        self.assertEqual(set(ranked),
                         {"uart.v:42", "uart.v:10", "uart.v:99"})
        self.assertEqual(len(ranked), 3)


class ConeOverlapBreaksTies(unittest.TestCase):
    def test_higher_cone_overlap_wins_tie(self):
        # Both uniques occur once; the one in more cones ranks higher.
        fault_signals = {
            "sigA": ["shared.v:1", "unique-high.v:2"],
            "sigB": ["shared.v:1", "unique-low.v:3"],
        }
        cones = {
            "coneA": {"shared.v:1", "unique-high.v:2"},
            "coneB": {"shared.v:1", "unique-high.v:2", "unique-low.v:3"},
            "coneC": {"unique-high.v:2"},
        }
        ranked = multifault.localize(fault_signals, cones)
        self.assertEqual(ranked[0], "shared.v:1")
        self.assertLess(ranked.index("unique-high.v:2"),
                        ranked.index("unique-low.v:3"))

    def test_no_cones_falls_back_to_deterministic_order(self):
        fault_signals = {
            "sigA": ["shared.v:1", "b.v:2"],
            "sigB": ["shared.v:1", "a.v:3"],
        }
        first = multifault.localize(fault_signals, {})
        second = multifault.localize(fault_signals, {})
        self.assertEqual(first, second)
        self.assertEqual(first[0], "shared.v:1")


class EdgeCases(unittest.TestCase):
    def test_empty_signals_returns_empty(self):
        self.assertEqual(multifault.localize({}, {}), [])

    def test_none_cones_ok(self):
        fault_signals = {"sigA": ["x.v:1", "y.v:2"]}
        ranked = multifault.localize(fault_signals, None)
        self.assertEqual(set(ranked), {"x.v:1", "y.v:2"})

    def test_bad_input_raises(self):
        with self.assertRaises(TypeError):
            multifault.localize(None, {})
        with self.assertRaises(TypeError):
            multifault.localize("not-a-dict", {})


if __name__ == "__main__":
    unittest.main()
