"""Past-fix recall: read-only retriever tests (fixtures, no network).

CONTEXT: past fixes never inform new diagnoses; a fresh RED re-learns
what a past GREEN already proved.

What this file proves (fixture runs/ dirs in tmp, mocked nothing --
recall is pure stdlib with no model layer to mock; NEVER real API):
- top-1: a crafted RED (same RTL file + overlapping signal names)
  recalls the exact-line past fix (file:lines) above a same-signal
  different-file bundle.
- MISS: an unrelated RED (no file/signal overlap) returns "" with
  status MISS and reason; the prompt is unchanged.
- token cap: a long lesson is truncated to fit DEFAULT_BLOCK_TOKENS;
  an impossibly small cap yields "" (miss, never overflow).
- provenance: info carries run_id/score/fix_ref and the block header
  names the run-id.
- flag-gating: loop.parse_args defaults --recall-past-fix OFF (proven
  path byte-identical); apply_to_prompt with "" is a no-op and a block
  that busts the prompt cap is refused unchanged.

Run: PYTHONPATH=. python3 tests/test_recall.py -v  (repo root)
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import recall


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _bundle(runs, name, case_id, rtl_path, new_start, new_count,
            signals, diagnosis, human_touches=0):
    d = os.path.join(runs, name)
    os.makedirs(os.path.join(d, "api"), exist_ok=True)
    diff = (
        "diff --git a/%(p)s b/%(p)s\n"
        "--- a/%(p)s\n"
        "+++ b/%(p)s\n"
        "@@ -%(o)d,1 +%(s)d,%(c)d @@ fix\n"
        "-old line\n"
        "+new line\n" % {"p": rtl_path, "s": new_start, "c": new_count,
                         "o": new_start - 1})
    _write(os.path.join(d, "patch.diff"), diff)
    _write(os.path.join(d, "meta.json"), json.dumps({
        "run_id": name, "case_id": case_id, "rtl_file": rtl_path,
        "human_touches": human_touches}))
    _write(os.path.join(d, "fingerprint.json"), json.dumps({
        "signals": signals}))
    _write(os.path.join(d, "api", "text_response.json"), json.dumps({
        "diagnosis": diagnosis}))
    _write(os.path.join(d, "report.md"),
           "## Diagnosis\n```\n%s\n```\n" % diagnosis)
    return d


TRACE_A = ("TRAP: recv_divcnt mismatchartool\n"
           "tohost: FAIL recv_divcnt stuck\n")
VERDICT_A = '{"verdict":"fail","tohost":"FAIL"}'


class RecallTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="recall-test-")
        self.runs = os.path.join(self.tmp, "runs")
        os.makedirs(self.runs)

    def _red(self):
        return TRACE_A, VERDICT_A, ["rtl/simpleuart.v"]

    def test_top1_exact_line_same_file_wins(self):
        _bundle(self.runs, "run-a-uart", "uart-tx-div",
                "rtl/simpleuart.v", 477, 1,
                ["recv_divcnt", "mismatchartool"],
                "recv_divcnt compared against shifted divider; use raw cfg.")
        _bundle(self.runs, "run-b-cpu", "pc-incr",
                "rtl/picorv32.v", 1446, 2,
                ["mismatchartool", "recv_divcnt"],
                "pc increment off by one in fetch stage.")
        ranked = recall.rank_red(*self._red(), self.runs)
        self.assertGreaterEqual(len(ranked), 1)
        # Same file (1.0 file Jaccard) beats different file despite
        # shared signal names.
        self.assertEqual(ranked[0]["run_id"], "run-a-uart")
        self.assertIn("rtl/simpleuart.v:477", ranked[0]["fix_ref"])
        block, info = recall.recall_for_red(
            *self._red(), self.runs, exclude_case_ids={"other-case"})
        self.assertIn("PAST_FIX_RECALL", block)
        self.assertIn("run-a-uart", block)
        self.assertIn("rtl/simpleuart.v:477", block)
        self.assertEqual(info["status"], "HIT")
        self.assertEqual(info["run_id"], "run-a-uart")

    def test_miss_below_threshold_changes_nothing(self):
        _bundle(self.runs, "run-far", "pc-incr",
                "rtl/picorv32.v", 100, 1,
                ["pcupdate", "fetchstage"],
                "pc increment off by one in fetch stage.")
        before = sorted(os.listdir(self.runs))
        block, info = recall.recall_for_red(
            *self._red(), self.runs, threshold=0.20)
        self.assertEqual(block, "")
        self.assertEqual(info["status"], "MISS")
        prompt = "diagnose this"
        new_prompt, applied, reason = recall.apply_to_prompt(prompt, block)
        self.assertEqual(new_prompt, prompt)
        self.assertFalse(applied)
        self.assertIn("MISS", reason)
        # Read-only: no bundle gained or lost files.
        self.assertEqual(sorted(os.listdir(self.runs)), before)
        for name in before:
            self.assertTrue(os.path.isfile(
                os.path.join(self.runs, name, "patch.diff")))

    def test_token_cap_enforced_on_long_lesson(self):
        long_diag = "recv_divcnt root cause " + ("detail word " * 200)
        _bundle(self.runs, "run-long", "uart-tx-div",
                "rtl/simpleuart.v", 477, 1,
                ["recv_divcnt", "mismatchartool"], long_diag)
        block, info = recall.recall_for_red(*self._red(), self.runs)
        self.assertEqual(info["status"], "HIT")
        self.assertLessEqual(recall.approx_tokens(block),
                             recall.DEFAULT_BLOCK_TOKENS)
        # An impossibly small cap is a miss, never an overflow.
        tiny = recall.build_block(
            {"run_id": "run-long", "score": 1.0,
             "fix_ref": "rtl/simpleuart.v:477", "lesson": long_diag},
            max_tokens=3)
        self.assertEqual(tiny, "")

    def test_provenance_run_id_logged(self):
        _bundle(self.runs, "run-prov", "uart-tx-div",
                "rtl/simpleuart.v", 481, 3,
                ["recv_divcnt", "mismatchartool"],
                "send_divcnt terminal-count fix.")
        block, info = recall.recall_for_red(*self._red(), self.runs)
        self.assertEqual(info["run_id"], "run-prov")
        self.assertGreater(info["score"], 0)
        self.assertIn("rtl/simpleuart.v", info["fix_ref"])
        header = block.splitlines()[0]
        self.assertIn("run-prov", header)
        self.assertIn("score=", header)

    def test_flag_gating_default_off(self):
        from agent import loop
        args = loop.parse_args([])
        self.assertFalse(args.recall_past_fix)
        args_on = loop.parse_args(["--recall-past-fix"])
        self.assertTrue(args_on.recall_past_fix)
        # Flag OFF path: empty block leaves the prompt byte-identical.
        prompt = "diagnose this RED window"
        new_prompt, applied, _ = recall.apply_to_prompt(prompt, "")
        self.assertEqual(new_prompt, prompt)
        self.assertFalse(applied)
        # A block that would bust the prompt cap is refused unchanged.
        big = "x " * 3000
        kept, applied2, reason2 = recall.apply_to_prompt(
            prompt, big, max_prompt_tokens=2500)
        self.assertEqual(kept, prompt)
        self.assertFalse(applied2)
        self.assertIn("MISS", reason2)

    def test_human_touched_bundle_ineligible(self):
        _bundle(self.runs, "run-touched", "uart-tx-div",
                "rtl/simpleuart.v", 477, 1,
                ["recv_divcnt", "mismatchartool"],
                "hand-fixed divider.", human_touches=1)
        ranked = recall.rank_red(*self._red(), self.runs)
        self.assertEqual(ranked, [])


if __name__ == "__main__":
    unittest.main()
