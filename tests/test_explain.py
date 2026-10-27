"""Dev-happiness track tests: `afterglow explain` bundle storytelling.

Run: python3 tests/test_explain.py -v   (from repo root)

Bundle-only rule (mirrors agent/report.py): render reads finished
bundle files, never re-invents. Missing files render as honest MISSING,
never guessed text.

- green bundle: story contains RED symptom (tohost FAIL), verbatim
  diagnosis quote, patch lines, VERIFY checksums (MATCH), cost.
- red bundle: story still renders (honest RED, no invention).
- missing files: every absent section says MISSING, exit nonzero? No --
  render succeeds with MISSING markers; CLI exits 0 (the bundle, not
  the renderer, carries the RED).
- cli: `afterglow explain runs/<id>` prints the story;
  `afterglow run --explain runs/<id>` is an accepted alias;
  `afterglow explain --help` exists and `afterglow --help` lists it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import explain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")
GREEN_BUNDLE = os.path.join(ROOT, "runs", "ablate-v2-uartdiv")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _write_json(path, obj):
    _write(path, json.dumps(obj) + "\n")


class TestExplainRender(unittest.TestCase):
    def test_green_bundle_story_has_all_five_beats(self):
        story = explain.render(GREEN_BUNDLE)
        self.assertIn("RED", story)
        self.assertIn("FAIL", story)
        # verbatim diagnosis quote from the bundle itself
        self.assertIn("Loopback sent 0xa5", story)
        self.assertIn("PATCH", story)
        self.assertIn("VERIFY", story)
        self.assertIn("5c8f58d0", story)
        self.assertIn("COST", story)

    def test_verify_match_on_green(self):
        story = explain.render(GREEN_BUNDLE)
        self.assertIn("MATCH", story)

    def test_missing_files_are_honest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = os.path.join(tmp, "empty-run")
            os.makedirs(run)
            story = explain.render(run)
            self.assertIn("MISSING", story)
            # never invents a diagnosis
            self.assertNotIn("Loopback sent", story)

    def test_missing_patch_is_missing_not_empty_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = os.path.join(tmp, "r")
            _write_json(os.path.join(run, "meta.json"),
                        {"run_id": "r", "case_id": "c", "status": "red"})
            story = explain.render(run)
            self.assertIn("MISSING", story)

    def test_render_reads_diagnosis_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = os.path.join(tmp, "r")
            diag = "UNIQUE verbatim diagnosis 12345"
            _write_json(os.path.join(run, "api", "text_response.json"),
                        {"diagnosis": diag,
                         "usage": {"prompt_tokens": 1,
                                   "completion_tokens": 1,
                                   "total_tokens": 2}})
            _write_json(os.path.join(run, "meta.json"),
                        {"run_id": "r", "case_id": "c", "status": "green"})
            story = explain.render(run)
            self.assertIn(diag, story)


class TestExplainCli(unittest.TestCase):
    def test_explain_subcommand_prints_story(self):
        proc = subprocess.run(
            [AFTERGLOW, "explain", GREEN_BUNDLE],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Loopback sent 0xa5", proc.stdout)

    def test_run_explain_flag_is_alias(self):
        proc = subprocess.run(
            [AFTERGLOW, "run", "--explain", GREEN_BUNDLE],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Loopback sent 0xa5", proc.stdout)

    def test_explain_help_exists(self):
        proc = subprocess.run(
            [AFTERGLOW, "explain", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("runs/<id>", proc.stdout)

    def test_explain_missing_bundle_fails_loud(self):
        proc = subprocess.run(
            [AFTERGLOW, "explain",
             os.path.join(ROOT, "runs", "does-not-exist-xyz")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertNotEqual(proc.returncode, 0)
        # stderr is merged into stdout (stderr=STDOUT), so proc.stderr is None.
        self.assertIn("MISSING", proc.stdout)


if __name__ == "__main__":
    unittest.main()
