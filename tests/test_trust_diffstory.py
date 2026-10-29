"""Diff-story tests (Task D7-TRUE, plan item 37, Wave D).

`agent/trust/diffstory.py` renders a patch explanation in the design's
own signal names. Fixture tests pin exact sentences.

RED-first TDD: this file was written BEFORE agent/trust/diffstory.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_diffstory -v (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import diffstory  # noqa: E402  (RED before implementation)

FIXTURE_DIFF = """\
--- a/picorv32.v
+++ b/picorv32.v
@@ -1445,7 +1445,7 @@
         if (mem_valid && mem_ready) begin
-            if (mem_state == 1) begin
+            if (mem_state != 1) begin
                 mem_xfer <= 1;
             end
@@ -1450,7 +1450,7 @@
         if (trap) begin
-            mem_la_secondword <= 0;
+            mem_la_secondword <= 1;
         end
"""

PINNED_HUNK_1 = ("Hunk 1 (picorv32.v:1447): mem_state inverted "
                 "(`==` -> `!=`).")
PINNED_HUNK_2 = ("Hunk 2 (picorv32.v:1452): mem_la_secondword set "
                 "to constant 1 (was 0).")


class TestStoryShape(unittest.TestCase):
    def test_pinned_hunk_sentences(self):
        story = diffstory.story_from_patch(FIXTURE_DIFF)
        self.assertEqual(story["sentences"][1], PINNED_HUNK_1)
        self.assertEqual(story["sentences"][2], PINNED_HUNK_2)

    def test_header_counts(self):
        story = diffstory.story_from_patch(FIXTURE_DIFF)
        self.assertEqual(story["sentences"][0],
                         "Patch touches 1 file(s) in 2 hunk(s).")
        self.assertEqual(story["files"], ["picorv32.v"])

    def test_text_joins_sentences(self):
        story = diffstory.story_from_patch(FIXTURE_DIFF)
        self.assertEqual(story["text"], "\n".join(story["sentences"]))
        for signal in ("mem_state", "mem_xfer",
                       "mem_la_secondword", "picorv32.v"):
            self.assertIn(signal, story["text"])

    def test_result_keys(self):
        story = diffstory.story_from_patch(FIXTURE_DIFF)
        self.assertEqual(set(story), {"files", "hunks", "sentences", "text"})
        self.assertEqual(len(story["hunks"]), 2)
        self.assertEqual(story["hunks"][0]["signals"], ["mem_state"])


class TestStoryRefusals(unittest.TestCase):
    def test_empty_diff_raises(self):
        for bad in ("", "   ", None, 123, ["x"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    diffstory.story_from_patch(bad)

    def test_hunkless_diff_raises(self):
        with self.assertRaises(ValueError):
            diffstory.story_from_patch(
                "--- a/f.v\n+++ b/f.v\nno hunks here\n")


class TestDefaultOff(unittest.TestCase):
    def test_no_loop_guard_wiring(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("agent/loop.py", "agent/guard.py"):
            with open(os.path.join(repo, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("diffstory", text)
            self.assertNotIn("story_from_patch", text)

    def test_stdlib_and_repo_only(self):
        import ast
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(
                repo, "agent/trust/diffstory.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertTrue(
                        alias.name.split(".")[0] in ("re", "os", "sys",
                                                     "json", "hashlib"),
                        alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertTrue(
                    (node.module or "").split(".")[0] == "agent"
                    or node.level > 0,
                    node.module)


if __name__ == "__main__":
    unittest.main()
