"""Task C5 (interactive repair, plan item 25, Wave C FIX BETTER) tests:
agent/repair/interact.py.

``--interact`` pauses AFTER PATCH: the dev accepts/edits/rejects each
hunk, then the loop continues (resim/verify). Reject-all restores the
baseline (no hunk applied).

  split_hunks(patch) -> [{"file","header","lines","raw","index",
                          "file_headers"}]
  review_patch(patch, decide) -> {"patch","decisions"}
  apply_patch(baseline, patch) -> new_files (baseline untouched)
  make_prompt_decide(input_fn, print_fn) -> decide_fn (the pause)
  interactive_review(baseline, patch, decide=None, input_fn=None,
                     print_fn=None)
    -> {"files","patch","decisions","outcome","restored"}

decide(hunk, index) returns "accept" | "reject"
  | ("edit", new_body_text) | {"action": "edit", "hunk": new_body_text}
  | {"action": "accept"} | {"action": "reject"}.
Anything else raises (refuses to guess the dev's intent).

Contract:
- Reject-all -> final patch "" + files == baseline (restored True).
- Accept-all -> files carry every +line, -lines gone.
- Mixed -> only accepted/edited hunks land (partial).
- Edit -> replacement body lands (edited).
- Prompt path (input_fn fixture, no real stdin): ["r","r"] restores
  baseline -- the --interact pause without a TTY.
- Pure stdlib + fixture I/O; no API, no keys, no network.
- Default-off: explicit call only; loop.py/guard.py untouched
  (no interact wiring).

RED-first TDD: this file was written BEFORE agent/repair/interact.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_interact.py -v   (from repo root)
"""
import copy
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import interact  # noqa: E402  (RED before implementation)


TWO_HUNK_PATCH = """\
diff --git a/rtl/uart.v b/rtl/uart.v
--- a/rtl/uart.v
+++ b/rtl/uart.v
@@ -1,3 +1,3 @@
 line1
-line2_old
+line2_new
 line3
@@ -10,3 +10,3 @@
 line10
-line11_old
+line11_new
 line12
"""

BASELINE_FILES = {
    "rtl/uart.v": (
        "line1\nline2_old\nline3\nline4\nline5\nline6\n"
        "line7\nline8\nline9\nline10\nline11_old\nline12\n"
    ),
}

EDITED_BODY_HUNK1 = " line1\n-line2_old\n+line2_EDITED\n line3"


def _accept(hunk, index):
    return "accept"


def _reject(hunk, index):
    return "reject"


def _mixed(hunk, index):
    return "accept" if index == 0 else "reject"


def _decisions(out):
    return [d["decision"] for d in out["decisions"]]


class TestSplitHunks(unittest.TestCase):
    def test_splits_two_hunks(self):
        hunks = interact.split_hunks(TWO_HUNK_PATCH)
        self.assertEqual(len(hunks), 2)
        self.assertEqual(hunks[0]["file"], "rtl/uart.v")
        self.assertEqual(hunks[1]["file"], "rtl/uart.v")
        self.assertIn("@@ -1,3 +1,3 @@", hunks[0]["header"])
        self.assertIn("@@ -10,3 +10,3 @@", hunks[1]["header"])
        self.assertEqual(hunks[0]["index"], 0)
        self.assertEqual(hunks[1]["index"], 1)

    def test_hunk_lines_keep_markers(self):
        hunks = interact.split_hunks(TWO_HUNK_PATCH)
        self.assertIn("-line2_old", hunks[0]["lines"])
        self.assertIn("+line2_new", hunks[0]["lines"])
        self.assertIn(" line1", hunks[0]["lines"])
        self.assertTrue(hunks[0]["raw"].strip())
        self.assertTrue(hunks[1]["raw"].strip())

    def test_file_headers_captured(self):
        hunks = interact.split_hunks(TWO_HUNK_PATCH)
        headers = hunks[0]["file_headers"]
        self.assertEqual(len(headers), 3)
        self.assertTrue(any("diff --git" in h for h in headers))
        self.assertTrue(any(h.startswith("--- ") for h in headers))
        self.assertTrue(any(h.startswith("+++ ") for h in headers))

    def test_empty_patch_raises(self):
        for bad in ("", "   \n  "):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    interact.split_hunks(bad)

    def test_non_string_patch_raises(self):
        for bad in (None, 42, ["@@ -1 +1 @@"]):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    interact.split_hunks(bad)

    def test_prose_is_not_a_diff(self):
        with self.assertRaises(ValueError):
            interact.split_hunks("the patch looks good, ship it")

    def test_diff_without_hunk_header_raises(self):
        nodiff = ("diff --git a/rtl/uart.v b/rtl/uart.v\n"
                  "--- a/rtl/uart.v\n+++ b/rtl/uart.v\n")
        with self.assertRaises(ValueError):
            interact.split_hunks(nodiff)


class TestReviewPatch(unittest.TestCase):
    def test_accept_all_keeps_patch(self):
        out = interact.review_patch(TWO_HUNK_PATCH, _accept)
        self.assertIn("+line2_new", out["patch"])
        self.assertIn("+line11_new", out["patch"])
        self.assertEqual(_decisions(out), ["accept", "accept"])

    def test_reject_all_empties_patch(self):
        out = interact.review_patch(TWO_HUNK_PATCH, _reject)
        self.assertEqual(out["patch"], "")
        self.assertEqual(_decisions(out), ["reject", "reject"])

    def test_mixed_keeps_only_accepted(self):
        out = interact.review_patch(TWO_HUNK_PATCH, _mixed)
        self.assertIn("+line2_new", out["patch"])
        self.assertNotIn("+line11_new", out["patch"])
        self.assertIn("diff --git", out["patch"])
        self.assertEqual(_decisions(out), ["accept", "reject"])

    def test_edit_tuple_replaces_body(self):
        def _decide(hunk, index):
            if index == 0:
                return ("edit", EDITED_BODY_HUNK1)
            return "accept"

        out = interact.review_patch(TWO_HUNK_PATCH, _decide)
        self.assertIn("+line2_EDITED", out["patch"])
        self.assertNotIn("+line2_new", out["patch"])
        self.assertEqual(_decisions(out), ["edit", "accept"])

    def test_edit_dict_form_accepted(self):
        def _decide(hunk, index):
            return {"action": "edit", "hunk": EDITED_BODY_HUNK1}

        out = interact.review_patch(TWO_HUNK_PATCH, _decide)
        self.assertIn("+line2_EDITED", out["patch"])
        self.assertEqual(_decisions(out), ["edit", "edit"])

    def test_action_dict_accept_reject(self):
        def _decide(hunk, index):
            return {"action": "accept"} if index == 0 else {"action": "reject"}

        out = interact.review_patch(TWO_HUNK_PATCH, _decide)
        self.assertEqual(_decisions(out), ["accept", "reject"])

    def test_unknown_decision_raises(self):
        with self.assertRaises(ValueError):
            interact.review_patch(TWO_HUNK_PATCH, lambda h, i: "ship it")

    def test_bad_decision_type_raises(self):
        with self.assertRaises(TypeError):
            interact.review_patch(TWO_HUNK_PATCH, lambda h, i: 42)

    def test_empty_edit_raises(self):
        with self.assertRaises(ValueError):
            interact.review_patch(
                TWO_HUNK_PATCH, lambda h, i: ("edit", "   \n "))

    def test_non_callable_decide_raises(self):
        with self.assertRaises(TypeError):
            interact.review_patch(TWO_HUNK_PATCH, "accept")

    def test_empty_patch_raises(self):
        with self.assertRaises(ValueError):
            interact.review_patch("", _accept)


class TestApplyPatch(unittest.TestCase):
    def test_accepted_patch_lands(self):
        files = interact.apply_patch(dict(BASELINE_FILES), TWO_HUNK_PATCH)
        text = files["rtl/uart.v"]
        self.assertIn("line2_new", text)
        self.assertIn("line11_new", text)
        self.assertNotIn("line2_old", text)
        self.assertNotIn("line11_old", text)

    def test_baseline_not_mutated(self):
        baseline = dict(BASELINE_FILES)
        pristine = copy.deepcopy(baseline)
        interact.apply_patch(baseline, TWO_HUNK_PATCH)
        self.assertEqual(baseline, pristine)

    def test_empty_patch_is_noop_copy(self):
        files = interact.apply_patch(dict(BASELINE_FILES), "")
        self.assertEqual(files, BASELINE_FILES)
        self.assertIsNot(files, BASELINE_FILES)

    def test_context_mismatch_raises(self):
        baseline = {"rtl/uart.v": "garbage\n"}
        with self.assertRaises(ValueError):
            interact.apply_patch(baseline, TWO_HUNK_PATCH)

    def test_unknown_file_raises(self):
        with self.assertRaises(ValueError):
            interact.apply_patch({}, TWO_HUNK_PATCH)

    def test_bad_baseline_raises(self):
        for bad in (None, [("rtl/uart.v", "x")], "rtl/uart.v"):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    interact.apply_patch(bad, TWO_HUNK_PATCH)

    def test_non_string_patch_raises(self):
        with self.assertRaises(TypeError):
            interact.apply_patch(dict(BASELINE_FILES), None)


class TestInteractiveReview(unittest.TestCase):
    def test_reject_all_restores_baseline(self):
        out = interact.interactive_review(
            dict(BASELINE_FILES), TWO_HUNK_PATCH, decide=_reject)
        self.assertEqual(out["files"], BASELINE_FILES)
        self.assertTrue(out["restored"])
        self.assertEqual(out["outcome"], "rejected_all")
        self.assertEqual(out["patch"], "")

    def test_accept_all_applies(self):
        out = interact.interactive_review(
            dict(BASELINE_FILES), TWO_HUNK_PATCH, decide=_accept)
        text = out["files"]["rtl/uart.v"]
        self.assertIn("line2_new", text)
        self.assertIn("line11_new", text)
        self.assertFalse(out["restored"])
        self.assertEqual(out["outcome"], "accepted")

    def test_mixed_is_partial(self):
        out = interact.interactive_review(
            dict(BASELINE_FILES), TWO_HUNK_PATCH, decide=_mixed)
        text = out["files"]["rtl/uart.v"]
        self.assertIn("line2_new", text)
        self.assertIn("line11_old", text)  # rejected hunk keeps baseline
        self.assertFalse(out["restored"])
        self.assertEqual(out["outcome"], "partial")

    def test_edit_lands_replacement(self):
        def _decide(hunk, index):
            if index == 0:
                return ("edit", EDITED_BODY_HUNK1)
            return "reject"

        out = interact.interactive_review(
            dict(BASELINE_FILES), TWO_HUNK_PATCH, decide=_decide)
        text = out["files"]["rtl/uart.v"]
        self.assertIn("line2_EDITED", text)
        self.assertIn("line11_old", text)
        self.assertEqual(out["outcome"], "edited")
        self.assertFalse(out["restored"])

    def test_prompt_all_reject_restores_baseline(self):
        answers = iter(["r", "r"])

        out = interact.interactive_review(
            dict(BASELINE_FILES), TWO_HUNK_PATCH,
            input_fn=lambda prompt: next(answers),
            print_fn=lambda *args: None)
        self.assertEqual(out["files"], BASELINE_FILES)
        self.assertTrue(out["restored"])
        self.assertEqual(out["outcome"], "rejected_all")

    def test_prompt_mixed_accept_reject(self):
        answers = iter(["a", "r"])

        out = interact.interactive_review(
            dict(BASELINE_FILES), TWO_HUNK_PATCH,
            input_fn=lambda prompt: next(answers),
            print_fn=lambda *args: None)
        text = out["files"]["rtl/uart.v"]
        self.assertIn("line2_new", text)
        self.assertIn("line11_old", text)
        self.assertEqual(out["outcome"], "partial")

    def test_prompt_edit_terminator_form(self):
        answers = iter(["e", " line1", "-line2_old", "+line2_PROMPT",
                        " line3", ".", "r"])

        out = interact.interactive_review(
            dict(BASELINE_FILES), TWO_HUNK_PATCH,
            input_fn=lambda prompt: next(answers),
            print_fn=lambda *args: None)
        text = out["files"]["rtl/uart.v"]
        self.assertIn("line2_PROMPT", text)
        self.assertIn("line11_old", text)
        self.assertEqual(out["outcome"], "edited")

    def test_prompt_unknown_reply_raises(self):
        answers = iter(["ship it"])

        with self.assertRaises(ValueError):
            interact.interactive_review(
                dict(BASELINE_FILES), TWO_HUNK_PATCH,
                input_fn=lambda prompt: next(answers),
                print_fn=lambda *args: None)

    def test_decide_and_input_fn_together_raises(self):
        with self.assertRaises(ValueError):
            interact.interactive_review(
                dict(BASELINE_FILES), TWO_HUNK_PATCH,
                decide=_accept, input_fn=lambda prompt: "a")

    def test_empty_patch_raises(self):
        with self.assertRaises(ValueError):
            interact.interactive_review(
                dict(BASELINE_FILES), "", decide=_accept)


class TestDefaultOff(unittest.TestCase):
    def test_no_loop_guard_wiring(self):
        path = os.path.join(ROOT, "agent", "repair", "interact.py")
        src = open(path).read()
        for pat in (r"^\s*import\s+loop\b", r"^\s*from\s+loop\s+import",
                    r"^\s*import\s+guard\b", r"^\s*from\s+guard\s+import"):
            self.assertIsNone(re.search(pat, src, re.MULTILINE),
                               "must not wire %r" % pat)

    def test_no_network_or_api(self):
        path = os.path.join(ROOT, "agent", "repair", "interact.py")
        src = open(path).read()
        for token in ("urllib", "socket", "requests", "API_KEY", "http"):
            self.assertNotIn(token, src)


if __name__ == "__main__":
    unittest.main()
