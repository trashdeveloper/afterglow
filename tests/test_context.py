"""Context-pack v2 tests (static fixtures, no model calls).

Run: python3 tests/test_context.py -v   (from repo root)

The model inherits a thin envelope today (trace + verdict + 40-line
oracle excerpt). agent/context.py gathers what a senior engineer would
pull before touching code -- all machine-derived, all budgeted:

- test_localize_ranks_signal_owner: lexical ranking puts the file
  actually mentioning the trace's suspect signal first.
- test_blame_fail_soft: non-git tree -> "" (omit section, never crash).
- test_blame_git_tree: real git fixture -> non-empty window with hashes.
- test_past_fixes_skips_red: only green diagnosed runs are remembered.
- test_pack_respects_budget: bloated inputs still fit PACK_BUDGET_TOKENS;
  drop order is memory -> blame -> localization.
- test_prompt_v2_exists: versioned template carries the {{CONTEXT}} slot.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import context


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class Localize(unittest.TestCase):
    def test_localize_ranks_signal_owner(self):
        with tempfile.TemporaryDirectory() as tree:
            _write(os.path.join(tree, "unrelated.v"),
                   "module unrelated;\nwire foo;\nendmodule\n")
            _write(os.path.join(tree, "uart.v"),
                   "module uart;\nreg [7:0] rdata;\n"
                   "always @(posedge clk) rdata <= rx;\nendmodule\n")
            ranked = context.localize(
                tree, ["unrelated.v", "uart.v"], ["rdata"])
            self.assertEqual(ranked[0][0], "uart.v")
            self.assertGreater(ranked[0][1], 0)
            self.assertEqual(ranked[-1][0], "unrelated.v")


class Blame(unittest.TestCase):
    def test_blame_fail_soft(self):
        with tempfile.TemporaryDirectory() as tree:
            _write(os.path.join(tree, "f.v"), "line1\nline2\n")
            self.assertEqual(context.blame_snippet(tree, "f.v", 1), "")

    def test_blame_git_tree(self):
        with tempfile.TemporaryDirectory() as tree:
            _write(os.path.join(tree, "f.v"), "a\nb\nc\nd\ne\n")
            subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"],
                           cwd=tree, check=True)
            subprocess.run(["git", "config", "user.name", "t"],
                           cwd=tree, check=True)
            subprocess.run(["git", "add", "."], cwd=tree, check=True)
            subprocess.run(["git", "commit", "-qm", "x"],
                           cwd=tree, check=True)
            out = context.blame_snippet(tree, "f.v", 3)
            self.assertTrue(out)
            self.assertIn("c", out)


class Memory(unittest.TestCase):
    def _meta(self, runs, name, status, diagnosis="d", patch_lines=1):
        d = os.path.join(runs, name)
        os.makedirs(os.path.join(d, "api"))
        with open(os.path.join(d, "meta.json"), "w",
                   encoding="utf-8") as fh:
            json.dump({"run_id": name, "status": status,
                       "diagnosed": True, "diagnosis": diagnosis,
                       "patch_lines": patch_lines}, fh)

    def test_past_fixes_skips_red(self):
        with tempfile.TemporaryDirectory() as runs:
            self._meta(runs, "green-a", "green", diagnosis="divisor wrong")
            self._meta(runs, "red-b", "red", diagnosis="should not appear")
            self._meta(runs, "green-c", "green", diagnosis="latch missing")
            lines = context.past_fixes(runs, k=5)
            joined = "\n".join(lines)
            self.assertEqual(len(lines), 2)
            self.assertIn("divisor wrong", joined)
            self.assertNotIn("should not appear", joined)

    def test_past_fixes_empty(self):
        with tempfile.TemporaryDirectory() as runs:
            self.assertEqual(context.past_fixes(runs), [])


class PackBudget(unittest.TestCase):
    def test_pack_respects_budget(self):
        with tempfile.TemporaryDirectory() as tree:
            big = "".join("wire sig_%d;\n" % i for i in range(500))
            _write(os.path.join(tree, "big.v"), big)
            trace = "tohost: FAIL rdata mismatch\n" + big
            pack, counts = context.build_pack(
                trace, tree, ["big.v"], tree, center_line=250)
            self.assertLessEqual(counts["pack_tokens"],
                                context.PACK_BUDGET_TOKENS)
            self.assertIn(counts["dropped"], ("none", "memory",
                                             "memory+blame"))
            self.assertTrue(pack)

    def test_prompt_v2_exists(self):
        root = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        v2 = os.path.join(root, "agent", "prompts", "v2.txt")
        with open(v2, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("{{CONTEXT}}", text)
        for slot in ("{{CASE}}", "{{RTL_FILE}}", "{{RTL_EXCERPT}}",
                     "{{TRACE}}", "{{VERDICT}}"):
            self.assertIn(slot, text)


if __name__ == "__main__":
    unittest.main()
