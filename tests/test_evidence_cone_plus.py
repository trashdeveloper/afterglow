"""Task B5 (cone-plus, plan item 15, Wave B SEE MORE) red-first tests:
agent/evidence/cone_plus.py.

- Extends the wave-3 static slicer (afterglow_core/slicer.py) across ONE
  module boundary following fan-in: a within-module driver cone that
  bottoms out at a module input port resolves that port's connection at
  the instantiation site in the parent module and continues one level up.
- Reuses slicer.backward_slice / parse_drivers (import, no duplicate).
- Default-off: only runs on explicit call; loop.py untouched.
- Depth cap 1: deeper hierarchies stop honestly (truncated=True, no
  invented drivers). Stdlib-only; no API, no keys.

Run: python3 tests/test_evidence_cone_plus.py -v   (from repo root)
"""
import ast
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import cone_plus  # noqa: E402  (RED before implementation)
from afterglow_core import slicer  # noqa: E402


def _write(tree, name, text):
    with open(os.path.join(tree, name), "w", encoding="utf-8") as fh:
        fh.write(text)


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

TOP_V = """module topmod(input wire t_in, output wire t_out);
wire w2;
mid u_mid(.m_in(t_in), .m_out(w2));
assign t_out = w2;
endmodule
"""

SINGLE_V = """module solo(input wire s_in, output wire s_out);
wire mid;
assign mid = s_in;
assign s_out = mid;
endmodule
"""


def _two_module_tree():
    tree = tempfile.mkdtemp()
    _write(tree, "leaf.v", LEAF_V)
    _write(tree, "mid.v", MID_V)
    return tree


class CrossBoundary(unittest.TestCase):
    def test_child_fault_driver_appears_in_parent(self):
        # Within-module view (leaf only): the base cone bottoms out at
        # the leaf input port l_in. The extended cone resolves
        # .l_in(m_in) at the mid.v instantiation site and continues up:
        # mid.v lines (m_in driver) must appear, base lacks them.
        tree = _two_module_tree()
        base_lines, _ = slicer.backward_slice(tree, "l_out", ["leaf.v"])
        self.assertTrue(base_lines)
        self.assertFalse(any(l.startswith("mid.v:") for l in base_lines))
        ext_lines, report = cone_plus.extended_cone(
            tree, "l_out", ["leaf.v"])
        self.assertTrue(report["crossed"])
        self.assertFalse(report["truncated"])
        self.assertTrue(any(l.startswith("mid.v:") for l in ext_lines),
                        "parent driver missing: %r" % (ext_lines,))
        joined = "\n".join(ext_lines)
        self.assertIn("m_in", joined)
        self.assertEqual(len(report["hops"]), 1)
        hop = report["hops"][0]
        self.assertEqual(hop["port"], "l_in")
        self.assertEqual(hop["parent_signal"], "m_in")

    def test_full_file_set_still_records_hop(self):
        tree = _two_module_tree()
        ext_lines, report = cone_plus.extended_cone(
            tree, "l_out", ["leaf.v", "mid.v"])
        self.assertTrue(report["crossed"])
        joined = "\n".join(ext_lines)
        self.assertIn("m_in", joined)

    def test_seed_at_boundary_port_starts_there(self):
        # Seeding directly at the child input port (no driver key in
        # its home file, only a port-map edge elsewhere): home is the
        # port's module and the hop still resolves upward.
        tree = _two_module_tree()
        ext_lines, report = cone_plus.extended_cone(
            tree, "l_in", ["leaf.v", "mid.v"])
        self.assertEqual(report["home"]["module"], "leaf")
        self.assertTrue(report["crossed"])
        self.assertEqual(report["hops"][0]["port"], "l_in")
        self.assertEqual(report["hops"][0]["parent_signal"], "m_in")
        self.assertTrue(any(l.startswith("mid.v:") for l in ext_lines))


class SingleModuleIdentical(unittest.TestCase):
    def test_single_module_byte_identical_to_base(self):
        # No instantiation anywhere: extended cone MUST equal the base
        # slicer output byte-for-byte (Wave B additive constraint).
        tree = tempfile.mkdtemp()
        _write(tree, "solo.v", SINGLE_V)
        base_lines, base_report = slicer.backward_slice(
            tree, "s_out", ["solo.v"])
        ext_lines, report = cone_plus.extended_cone(
            tree, "s_out", ["solo.v"])
        self.assertEqual(ext_lines, base_lines)
        self.assertEqual("\n".join(ext_lines).encode(),
                         "\n".join(base_lines).encode())
        self.assertFalse(report["crossed"])
        self.assertFalse(report["truncated"])
        self.assertEqual(report["hops"], [])
        for key in ("seed", "resolved", "sought", "unparsed", "lines",
                    "files", "drivers", "seen"):
            self.assertEqual(report[key], base_report[key])


class DepthCap(unittest.TestCase):
    def test_second_boundary_stops_honestly(self):
        # leaf -> mid -> topmod: only ONE crossing allowed. The cone
        # crosses leaf->mid, then sees m_in needs topmod and stops with
        # truncated=True; no top.v line is ever emitted.
        tree = tempfile.mkdtemp()
        _write(tree, "leaf.v", LEAF_V)
        _write(tree, "mid.v", MID_V)
        _write(tree, "top.v", TOP_V)
        ext_lines, report = cone_plus.extended_cone(
            tree, "l_out", ["leaf.v"])
        self.assertTrue(report["crossed"])
        self.assertTrue(report["truncated"])
        self.assertFalse(any(l.startswith("top.v:") for l in ext_lines),
                         "invented driver past depth cap: %r" % (ext_lines,))
        self.assertTrue(any(l.startswith("mid.v:") for l in ext_lines))


class DefaultOff(unittest.TestCase):
    def test_disabled_returns_without_touching_disk(self):
        # enable_cone_plus=False (default): no file reads -- a bogus
        # tree path must not raise.
        pack = cone_plus.cone_for_prompt(
            "/nonexistent/tree-xyz", "l_out", ["leaf.v"])
        self.assertFalse(pack["cone_plus_enabled"])
        self.assertEqual(pack["cone_lines"], [])
        self.assertEqual(pack["prompt_section"], "")

    def test_enabled_empty_seed_raises(self):
        # Enabled with nothing to seed: loud ValueError, never a guess.
        with self.assertRaises(ValueError):
            cone_plus.cone_for_prompt(
                tempfile.mkdtemp(), "", ["leaf.v"],
                enable_cone_plus=True)

    def test_loop_untouched_by_evidence_wiring(self):
        # B5 is additive + default-off: loop.py must not import the
        # evidence package (existing loop behavior byte-identical).
        with open(os.path.join(ROOT, "agent", "loop.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIsNone(re.search(r"^\s*(import|from)\s+\S*evidence",
                                    src, re.M))

    def test_module_is_stdlib_only(self):
        # No sockets, no HTTP, no API clients: local RTL only.
        with open(os.path.join(ROOT, "agent", "evidence",
                               "cone_plus.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "socket", "http",
                     "openai", "anthropic"}
        self.assertTrue(imported.isdisjoint(forbidden),
                        "non-stdlib/API imports: %s"
                        % (imported & forbidden))

    def test_reuses_base_slicer(self):
        # No duplicated cone-walking: the module must import the
        # wave-3 slicer instead of reimplementing driver parsing.
        with open(os.path.join(ROOT, "agent", "evidence",
                               "cone_plus.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertRegex(src, r"from afterglow_core import slicer")
        self.assertIn("slicer.backward_slice", src)


if __name__ == "__main__":
    unittest.main()
