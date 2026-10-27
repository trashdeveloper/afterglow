"""Context-pack v2: cone slicer + module graph tests (fixtures, no network).

Run: python3 tests/test_slicer.py -v   (from repo root)

- test_backward_slice_three_levels: top.v instantiates alu.v; seed rdata
  is driven by adder_out driven by a,b -> all three levels present,
  coverage 3/3, provenance file:line refs.
- test_generate_degrades: generate/macro block is counted as unparsed,
  slice still returns a non-empty lexical fallback (never empty section).
- test_module_graph_rank: top instantiates uart + alu; slice touches uart
  only -> uart ranks first.
- test_resolve_pinned_tree: resolves the pinned picorv32 tree from
  targets.pin read-only (exists, no weft/ copy, no writes).
- test_cone_cap_200: cone output never exceeds 200 lines.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import slicer


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


TOP_V = """module top(input clk, input [7:0] a, b, output [7:0] rdata);
wire [7:0] adder_out;
alu u_alu(.a(a), .b(b), .y(adder_out));
assign rdata = adder_out;
endmodule
"""

ALU_V = """module alu(input [7:0] a, b, output [7:0] y);
assign y = a + b;
endmodule
"""

UART_V = """module uart(input clk, output tx);
reg [7:0] rdata;
always @(posedge clk) rdata <= rdata + 1;
assign tx = rdata[0];
endmodule
"""

OTHER_V = """module alu(input clk, output z);
assign z = 1'b0;
endmodule
"""

GEN_V = """module genmod(input clk, input [7:0] d, output [7:0] q);
genvar i;
generate
for (i = 0; i < 8; i = i + 1) begin : g
assign q[i] = d[i];
end
endgenerate
`MYMACRO(q, d)
assign q = d;
endmodule
"""


class BackwardSlice(unittest.TestCase):
    def _tree(self):
        tree = tempfile.mkdtemp()
        _write(os.path.join(tree, "top.v"), TOP_V)
        _write(os.path.join(tree, "alu.v"), ALU_V)
        return tree

    def test_backward_slice_three_levels(self):
        tree = self._tree()
        lines, report = slicer.backward_slice(
            tree, "rdata", ["top.v", "alu.v"])
        joined = "\n".join(lines)
        self.assertIn("adder_out", joined)
        # Third level: y = a + b in alu.v names both a and b.
        self.assertIn("alu.v", joined)
        self.assertGreater(len(lines), 0)
        self.assertEqual(report["seed"], "rdata")
        self.assertGreaterEqual(report["resolved"], 2)
        # Every emitted line carries file:line provenance.
        for ln in lines:
            self.assertRegex(ln, r"^[^:]+:\d+: ")

    def test_generate_degrades(self):
        with tempfile.TemporaryDirectory() as tree:
            _write(os.path.join(tree, "genmod.v"), GEN_V)
            drivers = slicer.parse_drivers(tree, "genmod.v")
            self.assertIsInstance(drivers, dict)
            unparsed = slicer.unparsed_count(tree, "genmod.v")
            self.assertGreaterEqual(unparsed, 1)
            lines, report = slicer.backward_slice(
                tree, "q", ["genmod.v"])
            self.assertTrue(lines)  # lexical fallback, never empty
            self.assertGreaterEqual(report.get("unparsed", 0), 1)


class ModuleGraph(unittest.TestCase):
    def test_module_graph_rank(self):
        with tempfile.TemporaryDirectory() as tree:
            _write(os.path.join(tree, "top.v"),
                    "module top(input clk);\nuart u0(.clk(clk));\n"
                    "alu u1(.clk(clk));\nendmodule\n")
            _write(os.path.join(tree, "uart.v"), UART_V)
            _write(os.path.join(tree, "alu.v"), OTHER_V)
            graph = slicer.build_module_graph(tree, ["top.v", "uart.v", "alu.v"])
            self.assertIn("top", graph)
            self.assertIn("uart", graph["top"])
            self.assertIn("alu", graph["top"])
            ranked = slicer.rank_modules(graph, ["uart.v"])
            self.assertEqual(ranked[0][0], "uart")
            self.assertGreater(ranked[0][1], ranked[-1][1])

    def test_resolve_pinned_tree(self):
        tree = slicer.resolve_pinned_tree("picorv32")
        self.assertTrue(os.path.isdir(tree), "pinned tree missing: %s" % tree)
        # Foundation is referenced, never copied into this repo.
        self.assertFalse(os.path.exists(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "weft")))
        # At least one expected RTL file is visible read-only.
        self.assertTrue(os.path.isfile(os.path.join(tree, "picorv32.v")))

    def test_cone_cap_200(self):
        with tempfile.TemporaryDirectory() as tree:
            big = "module big(input [7:0] seed_sig, output [7:0] y);\n"
            for i in range(400):
                big += "wire [7:0] w%d;\nassign w%d = seed_sig + %d;\n" % (i, i, i)
            big += "assign y = w399;\nendmodule\n"
            _write(os.path.join(tree, "big.v"), big)
            lines, _report = slicer.backward_slice(
                tree, "y", ["big.v"])
            self.assertLessEqual(len(lines), 200)


WIRE_V = """module wmod(input clk, input [7:0] a, b, output [7:0] out);
wire [7:0] mid = a & b;
assign out = mid | 8'h00;
endmodule
"""


class WireInitDrivers(unittest.TestCase):
    """Wave-3: wire/reg initializer declarations drive the cone.

    Regression: ``wire mem_done = resetn && ...`` (picorv32.v:376) is a
    declaration with initializer, not an ``assign`` -- the old parser
    dropped it, so the 2nd BFS level through such signals was empty.
    """

    def _tree(self):
        tree = tempfile.mkdtemp()
        _write(os.path.join(tree, "wmod.v"), WIRE_V)
        return tree

    def test_wire_initializer_parsed(self):
        tree = self._tree()
        drivers = slicer.parse_drivers(tree, "wmod.v")
        self.assertIn("mid", drivers)
        self.assertIn("a", drivers["mid"])
        self.assertIn("b", drivers["mid"])

    def test_two_level_walk_through_wire_init(self):
        tree = self._tree()
        lines, report = slicer.backward_slice(
            tree, "out", ["wmod.v"], max_depth=2)
        joined = "\n".join(lines)
        # Level 1: mid defines out; level 2: a/b define mid.
        self.assertIn("mid", joined)
        self.assertIn("wire [7:0] mid = a & b;", joined)
        self.assertGreater(report.get("drivers", 0), 0)

    def test_cone_cap_holds_with_wire_init(self):
        tree = self._tree()
        lines, _report = slicer.backward_slice(
            tree, "out", ["wmod.v"], max_depth=2)
        self.assertLessEqual(len(lines), slicer.MAX_CONE_LINES)


class RealStaticCone(unittest.TestCase):
    """Wave-3: non-empty static cone on the real picorv32 tree.

    Seeds from the fetch-misalign anchor region (decoder_trigger /
    mem_done, picorv32.v:1446): the cone must contain the seed line's
    DRIVER lines (mem_done def at :376, wire-init proof at :363), not
    just the seed line itself.
    """

    def test_decoder_trigger_two_levels(self):
        tree = slicer.resolve_pinned_tree("picorv32")
        lines, report = slicer.backward_slice(
            tree, "decoder_trigger", ["picorv32.v"], max_depth=2)
        self.assertTrue(lines, "static cone empty for decoder_trigger")
        self.assertLessEqual(len(lines), slicer.MAX_CONE_LINES)
        have = lambda n: any(
            l.startswith("picorv32.v:%d:" % n) for l in lines)
        self.assertTrue(have(1446), "seed line 1446 missing")
        self.assertTrue(have(376), "mem_done driver def :376 missing")
        # Wire-init proof: :363 defines mem_la_firstword_xfer from
        # mem_xfer, reachable only through the ``wire mem_done = ...``
        # initializer edge (level 2 via mem_done).
        self.assertTrue(have(363), "wire-init driver line :363 missing")
        self.assertGreater(report.get("drivers", 0), 0)
        self.assertEqual(report.get("seed"), "decoder_trigger")

    def test_empty_file_behavior(self):
        with tempfile.TemporaryDirectory() as tree:
            _write(os.path.join(tree, "unrelated.v"),
                    "module u(input clk, output z);\nassign z = 1'b0;\n"
                    "endmodule\n")
            lines, report = slicer.backward_slice(
                tree, "nosuchsignal_xyz", ["unrelated.v"])
            self.assertEqual(lines, [])
            self.assertEqual(report.get("lines"), 0)
            self.assertEqual(report.get("files"), [])


class Top1Ranking(unittest.TestCase):
    """Wave-3: top-1 blame ranking (driver frequency + trace proximity)."""

    def test_top1_picks_driver_frequent_line(self):
        tree = tempfile.mkdtemp()
        _write(os.path.join(tree, "wmod.v"), WIRE_V)
        lines, report = slicer.backward_slice(
            tree, "out", ["wmod.v"], max_depth=2)
        trace = ("cycle 0 ok\n"
                 "TRAP: mid wrong, out mismatch\n"
                 "tohost: FAIL 0xdead\n")
        top1 = slicer.top1_localization(
            lines, trace, report.get("seen", ["out", "mid", "a", "b"]))
        # The mid definition line mentions two driver signals (a, b)
        # plus mid itself -- the densest driver line in the cone.
        self.assertRegex(top1, r"^wmod\.v:\d+$")
        self.assertEqual(top1, "wmod.v:2")

    def test_top1_tiebreak_trace_proximity(self):
        cone = ["m.v:10: assign q = aaa;",
                "m.v:20: assign r = bbb;"]
        trace = ("TRAP: bbb mismatch\n"
                 "tohost: FAIL 0xdead\n")
        top1 = slicer.top1_localization(cone, trace, ["aaa", "bbb"])
        self.assertEqual(top1, "m.v:20")

    def test_top1_empty_cone(self):
        self.assertEqual(slicer.top1_localization([], "TRAP: x\n", ["x"]), "")


if __name__ == "__main__":
    unittest.main()
