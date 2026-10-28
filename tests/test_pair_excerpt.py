"""Pair-spec support in the DIAGNOSE excerpt path (red-first).

loop.rtl_excerpt / loop.bug_line_no assume top-level case['bug'] /
case['anchor']; pair specs (mut-pair-*) carry a faults list and no
top-level bug, so re-firing them on the fixed loader crashed with
KeyError: 'bug' (5/5 mutpair-v4 runs) -- loud, but the pair path is
still unusable. These tests pin the required behavior: excerpts center
on the pinned fault line, verified against the tree (never blind trust
of the spec line number).
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
    tree = tempfile.mkdtemp(prefix="pair-excerpt-")
    os.makedirs(os.path.join(tree, "rtl"))
    with open(os.path.join(tree, "rtl", "u.v"), "w") as fh:
        fh.write(content)
    return tree


class TestPairExcerpt(unittest.TestCase):
    def test_excerpt_centers_on_first_pinned_fault(self):
        tree = _tree_with(RTL)
        text = loop.rtl_excerpt(tree, "rtl/u.v", PAIR_CASE, max_lines=6)
        self.assertIn("line 4: reg [3:1] recv_state;", text)

    def test_bug_line_no_returns_pinned_line(self):
        tree = _tree_with(RTL)
        self.assertEqual(loop.bug_line_no(tree, "rtl/u.v",
                                           PAIR_CASE), 4)

    def test_no_top_level_bug_key_needed(self):
        case = dict(PAIR_CASE)
        self.assertNotIn("bug", case)
        self.assertNotIn("anchor", case)
        tree = _tree_with(RTL)
        loop.rtl_excerpt(tree, "rtl/u.v", case, max_lines=6)
        loop.bug_line_no(tree, "rtl/u.v", case)

    def test_pinned_line_mismatch_falls_back_to_search(self):
        # Tree holds the anchor (post-patch state): excerpt must still
        # find the region via text search, never crash, never blindly
        # trust the spec line number.
        fixed = RTL.replace("reg [3:1] recv_state;",
                            "reg [3:0] recv_state;")
        tree = _tree_with(fixed)
        text = loop.rtl_excerpt(tree, "rtl/u.v", PAIR_CASE, max_lines=6)
        self.assertIn("reg [3:0] recv_state;", text)
        self.assertEqual(loop.bug_line_no(tree, "rtl/u.v",
                                           PAIR_CASE), 3)

    def test_no_fault_in_rel_refused_loudly(self):
        tree = _tree_with(RTL)
        with self.assertRaises(loop.LoopError):
            loop.rtl_excerpt(tree, "rtl/other.v", PAIR_CASE)
        with self.assertRaises(loop.LoopError):
            loop.bug_line_no(tree, "rtl/other.v", PAIR_CASE)

    def test_flat_cases_unchanged(self):
        flat = {"name": "flat", "files": ["rtl/u.v"],
                "anchor": "reg [3:0] recv_state;",
                "bug": "reg [3:1] recv_state;"}
        tree = _tree_with(RTL)
        self.assertEqual(loop.bug_line_no(tree, "rtl/u.v", flat), 4)
        text = loop.rtl_excerpt(tree, "rtl/u.v", flat, max_lines=6)
        self.assertIn("line 4: reg [3:1] recv_state;", text)


if __name__ == "__main__":
    unittest.main()
