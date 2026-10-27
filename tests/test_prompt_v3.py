"""Context-pack v2: prompt v3 template test (no model calls).

Run: python3 tests/test_prompt_v3.py -v   (from repo root)

- test_v3_slots: v3 carries all six collector slots (HARVEST supersedes
  v2 CONTEXT); v1/v2 files are untouched (hash-pinned here only as
  existence, never edited by this task).
- test_v3_renders_clean: substituting every slot leaves no {{ token.
- test_v1_v2_untouched: v1 has no HARVEST/CONTEXT slot; v2 keeps CONTEXT.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, "agent", "prompts", name),
              encoding="utf-8") as fh:
        return fh.read()


class PromptV3(unittest.TestCase):
    def test_v3_slots(self):
        v3 = _read("v3.txt")
        for slot in ("{{CASE}}", "{{RTL_FILE}}", "{{RTL_EXCERPT}}",
                     "{{HARVEST}}", "{{TRACE}}", "{{VERDICT}}"):
            self.assertIn(slot, v3)

    def test_v3_renders_clean(self):
        v3 = _read("v3.txt")
        rendered = v3.replace("{{CASE}}", "picosoc-uart-div").replace(
            "{{RTL_FILE}}", "picosoc/simpleuart.v").replace(
            "{{RTL_EXCERPT}}", "line 42: x").replace(
            "{{HARVEST}}", "### FINGERPRINT [x]\nfp").replace(
            "{{TRACE}}", "TRAP: x").replace(
            "{{VERDICT}}", '{"pass": false}')
        self.assertNotIn("{{", rendered)
        self.assertNotIn("}}", rendered)

    def test_v1_v2_untouched(self):
        v1 = _read("v1.txt")
        v2 = _read("v2.txt")
        self.assertNotIn("{{HARVEST}}", v1)
        self.assertNotIn("{{CONTEXT}}", v1)
        self.assertIn("{{CONTEXT}}", v2)


if __name__ == "__main__":
    unittest.main()
