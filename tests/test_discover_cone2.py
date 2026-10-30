"""Cone slicer v2 tests (Task E4, plan item 54, Wave E).

`agent/discover/cone2.py::slice_cone` extends the B5 one-hop cone to a
bounded multi-hop cone with a cycle guard. Native TDD: this file was
written BEFORE the module existed, so the first run must FAIL on the
missing module.

Run: python3 -m unittest tests.test_discover_cone2 -v (from repo root)
"""
import ast
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

from agent.discover import cone2  # noqa: E402  (RED before implementation)

LEAF_V = """module leaf(input wire l_in, output wire l_out);
assign l_out = l_in;
endmodule
"""

MID_V = """module mid(input wire m_in, output wire m_out);
wire w;
leaf u_leaf(.l_in(m_in), .l_out(w));
assign m_out = w;
endmodule
"""

TOP_V = """module topmod(input wire go, output wire t_out);
wire t_drv, w2;
mid u_mid(.m_in(t_drv), .m_out(w2));
assign t_drv = go & w2;
assign t_out = w2;
endmodule
"""

LOOP_V = """module loopmod(input wire go, output wire o);
wire a, b;
assign a = b & go;
assign b = a | go;
assign o = a ^ b;
endmodule
"""


def _write(tree, name, text):
    with open(os.path.join(tree, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def _three_level_tree():
    tree = tempfile.mkdtemp()
    _write(tree, "leaf.v", LEAF_V)
    _write(tree, "mid.v", MID_V)
    _write(tree, "top.v", TOP_V)
    return tree


def _loop_tree():
    tree = tempfile.mkdtemp()
    _write(tree, "loop.v", LOOP_V)
    return tree


class TestTwoHop(unittest.TestCase):
    def test_two_hop_reaches_top(self):
        res = cone2.slice_cone(_three_level_tree(), "l_out",
                               ["leaf.v", "mid.v", "top.v"], depth=2)
        self.assertTrue(any(ln.startswith("top.v:") for ln in res["lines"]))

    def test_one_hop_lacks_top(self):
        res = cone2.slice_cone(_three_level_tree(), "l_out",
                               ["leaf.v", "mid.v", "top.v"], depth=1)
        self.assertTrue(res["lines"])
        self.assertFalse(any(ln.startswith("top.v:") for ln in res["lines"]))

    def test_result_shape(self):
        res = cone2.slice_cone(_three_level_tree(), "l_out",
                               ["leaf.v", "mid.v", "top.v"], depth=2)
        self.assertEqual(set(res),
                         {"lines", "depth_reached", "truncated",
                          "cycle_guard_tripped", "hops", "files"})
        self.assertEqual(res["depth_reached"], 2)
        self.assertIn("top.v", res["files"])


class TestCycleGuard(unittest.TestCase):
    def test_guard_trips_on_tiny_budget(self):
        # Three-level tree needs 3 slices (seed + 2 hops); a budget of
        # 1 allows the seed slice, then trips on the first hop slice.
        res = cone2.slice_cone(_three_level_tree(), "l_out",
                               ["leaf.v", "mid.v", "top.v"],
                               depth=2, cycle_guard=1)
        self.assertTrue(res["cycle_guard_tripped"])
        self.assertFalse(any(ln.startswith("top.v:")
                             for ln in res["lines"]))

    def test_normal_budget_does_not_trip(self):
        res = cone2.slice_cone(_three_level_tree(), "l_out",
                               ["leaf.v", "mid.v", "top.v"], depth=2)
        self.assertFalse(res["cycle_guard_tripped"])


class TestValidation(unittest.TestCase):
    def test_bad_inputs_raise(self):
        tree = _three_level_tree()
        with self.assertRaises(ValueError):
            cone2.slice_cone("", "l_out", ["leaf.v"])
        with self.assertRaises(ValueError):
            cone2.slice_cone(tree, "  ", ["leaf.v"])
        with self.assertRaises(ValueError):
            cone2.slice_cone(tree, "l_out", ["leaf.v"], depth=0)
        with self.assertRaises(ValueError):
            cone2.slice_cone(tree, "l_out", ["leaf.v"], cycle_guard=0)
        with self.assertRaises(ValueError):
            cone2.slice_cone(tree, "l_out", "leaf.v")

    def test_pathlike_tree_accepted(self):
        import pathlib
        res = cone2.slice_cone(pathlib.Path(_three_level_tree()),
                               "l_out", ["leaf.v", "mid.v", "top.v"],
                               depth=2)
        self.assertTrue(any(ln.startswith("top.v:")
                            for ln in res["lines"]))

    def test_twin_sites_sort_without_dict_compare(self):
        # Two instantiation sites sharing (mod, file, child) force a
        # tie down to the pairs dict; sorting must use a string key.
        leaf = ("module leaf(input wire l_in, output wire l_out);\n"
                "assign l_out = l_in;\nendmodule\n")
        mid = ("module mid(input wire m_in, output wire m_out);\n"
               "wire w, w2;\n"
               "leaf u_a(.l_in(m_in), .l_out(w));\n"
               "leaf u_b(.l_in(w), .l_out(w2));\n"
               "assign m_out = w2;\nendmodule\n")
        tree = tempfile.mkdtemp()
        for name, content in (("leaf.v", leaf), ("mid.v", mid),
                              ("top.v", TOP_V)):
            with open(os.path.join(tree, name), "w",
                      encoding="utf-8") as handle:
                handle.write(content)
        res = cone2.slice_cone(tree, "l_out", ["leaf.v", "mid.v"],
                               depth=1)
        self.assertTrue(any(ln.startswith("mid.v:")
                            for ln in res["lines"]))


class TestDefaultOff(unittest.TestCase):
    def test_no_loop_guard_wiring(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("agent/loop.py", "agent/guard.py"):
            with open(os.path.join(repo, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("cone2", text)
            self.assertNotIn("slice_cone", text)

    def test_stdlib_and_repo_only(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(
                repo, "agent/discover/cone2.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertTrue(
                        alias.name.split(".")[0] in (
                            "os", "re", "sys", "json", "hashlib"),
                        alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertTrue(
                    (node.module or "").split(".")[0] in (
                        "agent", "afterglow_core")
                    or node.level > 0,
                    node.module)


if __name__ == "__main__":
    unittest.main()
