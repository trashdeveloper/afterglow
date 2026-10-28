"""Per-fault excerpt windows for pair specs (red-first).

Proven failure (drift investigation on mutpair-v5-03/04/05): the excerpt
path centers on the FIRST fault only, so the second fault is out of the
40-line window (03: 877 is 198 lines past the 679 window; 05: 1450 is 22
lines past the 1408 window) while the prompt forbids signals outside the
excerpt -- the model can fix at most one fault and the pair stays RED
with an identical checksum. Fix: one window per fault + pinned-lines
grounding, with single-fault output byte-identical to today.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import loop  # noqa: E402


PAIR_CASE = {
    "name": "mut-pair-t",
    "files": ["rtl/u.v"],
    "faults": [
        {"file": "rtl/u.v", "line": 4,
         "anchor": "reg [3:0] recv_state;",
         "bug": "reg [3:1] recv_state;"},
        {"file": "rtl/u.v", "line": 9,
         "anchor": "send_pattern <= ~0;",
         "bug": "send_pattern <= ~1;"},
    ],
}

RTL = "\n".join([
    "module u;",
    "reg clk;",
    "reg [3:0] recv_state;",
    "reg [3:1] recv_state;",
    "wire x;",
    "wire y;",
    "wire z;",
    "wire w;",
    "send_pattern <= ~1;",
    "endmodule",
    ""])  # bug texts at lines 4 and 9 (1-based)


def _tree_with(content):
    tree = tempfile.mkdtemp(prefix="pair-windows-")
    os.makedirs(os.path.join(tree, "rtl"))
    with open(os.path.join(tree, "rtl", "u.v"), "w") as fh:
        fh.write(content)
    return tree


class TestPairExcerptWindows(unittest.TestCase):
    def test_two_windows_for_two_faults(self):
        tree = _tree_with(RTL)
        wins = loop.rtl_excerpts(tree, "rtl/u.v", PAIR_CASE,
                                 max_lines=6)
        self.assertEqual(len(wins), 2)
        self.assertEqual([ln for ln, _ in wins], [4, 9])

    def test_each_window_covers_its_fault(self):
        tree = _tree_with(RTL)
        wins = loop.rtl_excerpts(tree, "rtl/u.v", PAIR_CASE,
                                 max_lines=6)
        by_line = dict(wins)
        self.assertIn("line 4: reg [3:1] recv_state;", by_line[4])
        self.assertIn("line 9: send_pattern <= ~1;", by_line[9])

    def test_flat_case_matches_single_window(self):
        flat = {"name": "flat", "files": ["rtl/u.v"],
                "anchor": "reg [3:0] recv_state;",
                "bug": "reg [3:1] recv_state;"}
        tree = _tree_with(RTL)
        wins = loop.rtl_excerpts(tree, "rtl/u.v", flat, max_lines=6)
        self.assertEqual(len(wins), 1)
        self.assertEqual(wins[0][1],
                         loop.rtl_excerpt(tree, "rtl/u.v", flat,
                                          max_lines=6))

    def test_single_window_identical_for_pairs_first_fault(self):
        # The first pair window must equal today's first-fault output,
        # so singles and pair-first-fault behavior cannot drift apart.
        tree = _tree_with(RTL)
        wins = loop.rtl_excerpts(tree, "rtl/u.v", PAIR_CASE,
                                 max_lines=6)
        self.assertEqual(wins[0][1],
                         loop.rtl_excerpt(tree, "rtl/u.v", PAIR_CASE,
                                          max_lines=6))

    def test_pinned_lines_listed_for_pairs_only(self):
        tree = _tree_with(RTL)  # noqa: F841 -- tree unused, API symmetry
        self.assertEqual(loop.pair_pinned_lines(PAIR_CASE, "rtl/u.v"),
                         [4, 9])
        flat = {"name": "flat",
                "anchor": "a;", "bug": "b;"}
        self.assertEqual(loop.pair_pinned_lines(flat, "rtl/u.v"), [])

    def test_excerpt_text_has_separators_and_grounding(self):
        tree = _tree_with(RTL)
        text = loop.build_excerpt_text(tree, "rtl/u.v", PAIR_CASE,
                                       max_lines=6)
        self.assertIn("=== fault 1 (rtl/u.v:4) ===", text)
        self.assertIn("=== fault 2 (rtl/u.v:9) ===", text)
        self.assertIn("candidate bug lines: 4, 9", text)
        # Control experiment (mutpair-v6-05): a byte-perfect hunk ending
        # on -/+ lines with zero trailing context is rejected by
        # patch/git-apply; the grounding must demand trailing context.
        self.assertIn("trailing", text)

    def test_excerpt_text_flat_is_bare_window(self):
        flat = {"name": "flat", "files": ["rtl/u.v"],
                "anchor": "reg [3:0] recv_state;",
                "bug": "reg [3:1] recv_state;"}
        tree = _tree_with(RTL)
        text = loop.build_excerpt_text(tree, "rtl/u.v", flat,
                                       max_lines=6)
        self.assertEqual(text, loop.rtl_excerpt(tree, "rtl/u.v", flat,
                                                max_lines=6))
        self.assertNotIn("=== fault", text)
        self.assertNotIn("candidate bug lines", text)


if __name__ == "__main__":
    unittest.main()
