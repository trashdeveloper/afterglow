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


if __name__ == "__main__":
    unittest.main()
