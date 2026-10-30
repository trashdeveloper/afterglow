"""Task F2 (cone-intersection diagnosis, plan item 62) tests:
agent/diagnose/conecut.py.

intersect(cones) ranks the intersection of suspect sets (lines in
ALL cones), ordered by first-cone order with duplicates dropped;
coverage(cones) is the fraction of union lines present in ALL
cones (0.0-1.0).

RED-first TDD: this file was written BEFORE
agent/diagnose/conecut.py existed, so the first run must FAIL
on the missing module (proving the test exercises real code,
not a stub).

Run: python3 -m unittest tests.test_diagnose_conecut -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from diagnose import conecut  # noqa: E402  (RED before implementation)


class SharedLineRanksFirst(unittest.TestCase):
    def test_two_cones_share_exactly_one_line(self):
        # RED-first fixture: two suspect sets sharing exactly one line.
        cone_a = ["uart.v:10", "uart.v:42", "uart.v:99"]
        cone_b = ["uart.v:42", "uart.v:77"]
        ranked = conecut.intersect([cone_a, cone_b])
        self.assertEqual(ranked, ["uart.v:42"])
        self.assertEqual(ranked[0], "uart.v:42")

    def test_coverage_arithmetic_exact(self):
        # union = {10, 42, 99, 77} (4 lines); all-cones = {42} (1 line).
        cone_a = ["uart.v:10", "uart.v:42", "uart.v:99"]
        cone_b = ["uart.v:42", "uart.v:77"]
        self.assertEqual(conecut.coverage([cone_a, cone_b]), 1 / 4)


class IntersectionOrder(unittest.TestCase):
    def test_first_cone_order_duplicates_dropped(self):
        cone_a = ["c.v:3", "c.v:1", "c.v:3", "c.v:2"]
        cone_b = ["c.v:2", "c.v:1", "c.v:3"]
        self.assertEqual(conecut.intersect([cone_a, cone_b]),
                         ["c.v:3", "c.v:1", "c.v:2"])

    def test_disjoint_cones_intersect_empty(self):
        self.assertEqual(conecut.intersect([["a.v:1"], ["b.v:2"]]), [])

    def test_empty_cone_list_intersects_empty(self):
        self.assertEqual(conecut.intersect([]), [])

    def test_single_cone_returns_its_lines_deduped(self):
        self.assertEqual(conecut.intersect([["s.v:1", "s.v:1", "s.v:2"]]),
                         ["s.v:1", "s.v:2"])

    def test_dict_cones_use_insertion_order(self):
        cones = {"coneA": ["d.v:9", "d.v:7"], "coneB": ["d.v:7", "d.v:9"]}
        self.assertEqual(conecut.intersect(cones), ["d.v:9", "d.v:7"])


class CoverageEdges(unittest.TestCase):
    def test_identical_cones_cover_fully(self):
        cones = [["e.v:1", "e.v:2"], ["e.v:1", "e.v:2"]]
        self.assertEqual(conecut.coverage(cones), 1.0)

    def test_disjoint_cones_cover_zero(self):
        self.assertEqual(conecut.coverage([["a.v:1"], ["b.v:2"]]), 0.0)

    def test_empty_input_covers_zero(self):
        self.assertEqual(conecut.coverage([]), 0.0)
        self.assertEqual(conecut.coverage([[], []]), 0.0)

    def test_coverage_bounded(self):
        cov = conecut.coverage([["f.v:1", "f.v:2", "f.v:3"],
                                ["f.v:2", "f.v:3"],
                                ["f.v:3", "f.v:4"]])
        # intersection = {f.v:3}; union = 4 lines -> 0.25.
        self.assertEqual(cov, 1 / 4)
        self.assertGreaterEqual(cov, 0.0)
        self.assertLessEqual(cov, 1.0)

    def test_bad_input_raises(self):
        with self.assertRaises(TypeError):
            conecut.intersect(None)
        with self.assertRaises(TypeError):
            conecut.intersect("not-a-cone-list")
        with self.assertRaises(TypeError):
            conecut.coverage(None)


class StandardGates(unittest.TestCase):
    MODULE = os.path.join(ROOT, "agent", "diagnose", "conecut.py")

    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "conecut", text,
                "%s must not reference the conecut module "
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
