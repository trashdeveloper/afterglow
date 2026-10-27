"""Hunk-header count normalization: the model is often semantically right
but writes wrong @@ -a,b +c,d @@ counts (e.g. -379,4 for a 6-line hunk),
which `patch` rejects as malformed. Normalization recomputes counts from
the hunk BODY -- content lines are never touched, added, or removed.

Regression source: runs/fetch-misalign-v5-01/rejected_model.diff
(semantically identical to bootstrap.diff, rejected on header counts).

Run: python3 tests/test_hunk_normalize.py -v   (from repo root)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "agent"))

import loop


REJECTED = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -379,4 +379,4 @@
 \tassign mem_la_write = resetn && !mem_state && mem_do_wdata;
 \tassign mem_la_read = resetn && ((!mem_la_use_prefetched_high_word && !mem_state && (mem_do_rinst || mem_do_prefetch || mem_do_rdata)) ||
 \t\t\t(COMPRESSED_ISA && mem_xfer && (!last_mem_valid ? mem_la_firstword : mem_la_firstword_reg) && !mem_la_secondword && &mem_rdata_latched[1:0]));
-assign mem_la_addr = (mem_do_prefetch || mem_do_rinst) ? {next_pc[31:2] + mem_la_firstword_xfer, 2'b10} : {reg_op1[31:2], 2'b10};
+assign mem_la_addr = (mem_do_prefetch || mem_do_rinst) ? {next_pc[31:2] + mem_la_firstword_xfer, 2'b00} : {reg_op1[31:2], 2'b00};
"""

CORRECT = REJECTED  # -379,4 +379,4 already matches the 5-line body


class TestNormalize(unittest.TestCase):
    def test_fixes_wrong_counts(self):
        # Genuinely miscounted header (-379,2 for a 4-old/4-new hunk):
        # 3 context + 1 del = 4 old; 3 context + 1 add = 4 new.
        wrong = REJECTED.replace("@@ -379,4 +379,4 @@",
                                 "@@ -379,2 +379,2 @@", 1)
        fixed, changed = loop.normalize_hunk_counts(wrong)
        self.assertTrue(changed)
        self.assertIn("@@ -379,4 +379,4 @@\n", fixed)
        self.assertEqual(
            fixed.replace("@@ -379,4 +379,4 @@", "@@ -379,2 +379,2 @@"),
            wrong)

    def test_leaves_correct_untouched(self):
        fixed, changed = loop.normalize_hunk_counts(CORRECT)
        self.assertFalse(changed)
        self.assertEqual(fixed, CORRECT)

    def test_multi_hunk_and_skips_garbage(self):
        diff = ("diff --git a/f.v b/f.v\n--- a/f.v\n+++ b/f.v\n"
                "@@ -1,1 +1,1 @@\n ctx\n-old\n+new\n"
                "@@ -10,5 +10,5 @@\n a\n b\n-c\n+d\n")
        fixed, changed = loop.normalize_hunk_counts(diff)
        self.assertTrue(changed)
        self.assertIn("@@ -1,2 +1,2 @@", fixed)
        self.assertIn("@@ -10,3 +10,3 @@", fixed)
        fixed2, changed2 = loop.normalize_hunk_counts(
            "not a diff\n@@ bogus\n")
        self.assertEqual(fixed2, "not a diff\n@@ bogus\n")
        self.assertFalse(changed2)


class TestTryApply(unittest.TestCase):
    def test_applies_count_wrong_diff(self):
        import guard
        with tempfile.TemporaryDirectory() as tree:
            victim = os.path.join(tree, "simpleuart.v")
            with open(victim, "w") as fh:
                fh.write("module m;\nl1\nold_line\nl4\nendmodule\n")
            diff = ("diff --git a/simpleuart.v b/simpleuart.v\n--- a/simpleuart.v\n"
                    "+++ b/simpleuart.v\n"
                    "@@ -1,2 +1,2 @@\n module m;\n l1\n-old_line\n+new_line\n l4\n endmodule\n")
            ok, _, _ = guard.check(diff, allowlist=["simpleuart.v"],
                                   rtl_root=tree)
            self.assertTrue(ok, "test setup broken: guard rejects content")
            out = loop.try_apply_diff(diff, tree,
                                      os.path.join(tree, "t.diff"))
            self.assertTrue(out["applied"], out.get("reasons"))
            with open(victim) as fh:
                self.assertIn("new_line", fh.read())

    def test_applies_unterminated_diff(self):
        # Regression: the model often emits patch_diff with no trailing
        # newline (extract strips it); patch(1) calls that "malformed".
        # Termination framing must not change the verdict on content.
        import guard
        with tempfile.TemporaryDirectory() as tree:
            victim = os.path.join(tree, "simpleuart.v")
            with open(victim, "w") as fh:
                fh.write("module m;\nl1\nold_line\nl4\nendmodule\n")
            diff = ("diff --git a/simpleuart.v b/simpleuart.v\n--- a/simpleuart.v\n"
                    "+++ b/simpleuart.v\n"
                    "@@ -1,5 +1,5 @@\n module m;\n l1\n-old_line\n+new_line\n l4\n endmodule")
            self.assertFalse(diff.endswith("\n"))
            ok, _, _ = guard.check(diff, allowlist=["simpleuart.v"],
                                   rtl_root=tree)
            self.assertTrue(ok, "test setup broken: guard rejects content")
            out = loop.try_apply_diff(diff, tree,
                                      os.path.join(tree, "t.diff"))
            self.assertTrue(out["applied"], out.get("reasons"))
            with open(victim) as fh:
                self.assertIn("new_line", fh.read())


if __name__ == "__main__":
    unittest.main()
