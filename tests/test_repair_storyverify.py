"""Task G8 (story-vs-diff verifier, plan item 78) tests:
agent/repair/storyverify.py.

Interface (plan text, verbatim):

  Consumes: agent/trust/diffstory.py sentence schema.
  Produces: verify(story, patch) -> True|{lies} (each named signal +
    direction checked against the actual hunk).

``story`` is a diffstory-format dict (as returned by
``agent/trust/diffstory.py::story_from_patch``): {"files", "hunks",
"sentences", "text"} where each hunk is {"file", "line", "signals",
"direction"}. ``patch`` is the unified diff string the story claims
to explain. ``verify`` re-derives the truth with
``diffstory.story_from_patch(patch)`` and checks every claimed hunk:

- hunk count must match;
- each claimed file must equal the actual hunk file;
- each claimed line (when present as an int) must equal the actual line;
- every named signal must appear in the actual hunk signals (claimed
  subset of actual; exact, case-sensitive -- Verilog is);
- each claimed direction must match the actual direction (exact, or
  category-level: one normalized string contains the other, so a
  summarized "inverted" matches "inverted (`==` -> `!=`)").

Contract:
- A true story (fresh ``story_from_patch`` output) verifies to True.
- A story claiming an inversion the diff does not contain is rejected
  with {"lies": [...]} naming the lie (hunk number + claimed vs actual
  direction). A story naming a signal absent from the hunk is rejected
  naming that signal. Wrong file/line/count are each rejected naming
  the mismatch. An empty signal list is a lie of omission (rejected,
  not vacuously passed).
- Vacuous input raises loudly before any verdict: non-dict story,
  missing/non-list hunks, empty hunks, malformed hunk entries,
  non-string/blank patch, or a patch with no hunks (propagated from
  diffstory) -- never an invented pass.
- Deterministic, read-only: inputs never mutated; no I/O, no API.
- Pure stdlib + repo import (agent/trust/diffstory.py); no live API,
  no keys, no network. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no storyverify wiring).

RED-first TDD: this file was written BEFORE
agent/repair/storyverify.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_storyverify.py -v   (from repo root)
"""
import ast
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import storyverify  # noqa: E402  (RED before implementation)
from agent.trust import diffstory  # noqa: E402  real provider, format-level use


PATCH_CONSTANT = """\
diff --git a/rtl/uart.v b/rtl/uart.v
--- a/rtl/uart.v
+++ b/rtl/uart.v
@@ -144,3 +144,3 @@
     reg uart_tx_busy;
-    uart_tx_busy <= 1'b0;
+    uart_tx_busy <= 1'b1;
"""

PATCH_INVERT = """\
diff --git a/rtl/uart_rx.v b/rtl/uart_rx.v
--- a/rtl/uart_rx.v
+++ b/rtl/uart_rx.v
@@ -88,3 +88,3 @@
     reg uart_rx_ready;
-    if (uart_rx_valid == 1'b1) begin
+    if (uart_rx_valid != 1'b1) begin
"""


def _true_story(patch):
    """A true story, derived by the real provider (never hand-typed)."""
    return diffstory.story_from_patch(patch)


class TrueStoryPasses(unittest.TestCase):
    def test_true_constant_story_passes(self):
        story = _true_story(PATCH_CONSTANT)
        # Fixture sanity: the constant patch is genuinely not an inversion,
        # so the inversion-lie tests below are meaningful.
        actual = story["hunks"][0]["direction"]
        self.assertNotIn("invert", actual.lower())
        self.assertIs(storyverify.verify(story, PATCH_CONSTANT), True)

    def test_true_inversion_story_passes(self):
        story = _true_story(PATCH_INVERT)
        actual = story["hunks"][0]["direction"]
        self.assertIn("invert", actual.lower())
        self.assertIs(storyverify.verify(story, PATCH_INVERT), True)

    def test_category_level_direction_passes(self):
        story = _true_story(PATCH_INVERT)
        claimed = copy.deepcopy(story)
        claimed["hunks"][0]["direction"] = "inverted"
        self.assertIs(storyverify.verify(claimed, PATCH_INVERT), True)

    def test_subset_of_signals_passes(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        first = story["hunks"][0]["signals"][0]
        claimed["hunks"][0]["signals"] = [first]
        self.assertIs(storyverify.verify(claimed, PATCH_CONSTANT), True)

    def test_story_without_line_still_passes(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        del claimed["hunks"][0]["line"]
        self.assertIs(storyverify.verify(claimed, PATCH_CONSTANT), True)

    def test_verify_does_not_mutate_inputs(self):
        story = _true_story(PATCH_CONSTANT)
        snapshot = copy.deepcopy(story)
        storyverify.verify(story, PATCH_CONSTANT)
        self.assertEqual(story, snapshot)

    def test_deterministic_same_inputs_same_verdict(self):
        first = storyverify.verify(
            _true_story(PATCH_CONSTANT), PATCH_CONSTANT)
        second = storyverify.verify(
            _true_story(PATCH_CONSTANT), PATCH_CONSTANT)
        self.assertIs(first, True)
        self.assertIs(second, True)


class LiesAreRejected(unittest.TestCase):
    def test_inversion_lie_rejected_naming_the_lie(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        claimed["hunks"][0]["direction"] = "inverted (`==` -> `!=`)"
        result = storyverify.verify(claimed, PATCH_CONSTANT)
        self.assertIsInstance(result, dict)
        self.assertIn("lies", result)
        self.assertTrue(result["lies"])
        blob = " ".join(result["lies"]).lower()
        self.assertIn("hunk 1", blob)
        self.assertIn("invert", blob)

    def test_ghost_signal_rejected_naming_the_signal(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        claimed["hunks"][0]["signals"] = (
            list(story["hunks"][0]["signals"]) + ["ghost_sig_xyz"])
        result = storyverify.verify(claimed, PATCH_CONSTANT)
        self.assertIsInstance(result, dict)
        self.assertIn("lies", result)
        self.assertTrue(any("ghost_sig_xyz" in lie
                            for lie in result["lies"]))

    def test_wrong_file_rejected(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        claimed["hunks"][0]["file"] = "rtl/other_block.v"
        result = storyverify.verify(claimed, PATCH_CONSTANT)
        self.assertIsInstance(result, dict)
        self.assertTrue(result["lies"])
        self.assertIn("rtl/other_block.v",
                      " ".join(result["lies"]))

    def test_wrong_line_rejected(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        claimed["hunks"][0]["line"] = (
            story["hunks"][0]["line"] + 100)
        result = storyverify.verify(claimed, PATCH_CONSTANT)
        self.assertIsInstance(result, dict)
        self.assertTrue(result["lies"])
        self.assertIn("hunk 1", " ".join(result["lies"]).lower())

    def test_hunk_count_mismatch_rejected(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        claimed["hunks"] = claimed["hunks"] + copy.deepcopy(
            claimed["hunks"])
        result = storyverify.verify(claimed, PATCH_CONSTANT)
        self.assertIsInstance(result, dict)
        self.assertTrue(result["lies"])
        blob = " ".join(result["lies"]).lower()
        self.assertIn("2", blob)
        self.assertIn("1", blob)

    def test_empty_signals_is_a_lie_not_a_pass(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        claimed["hunks"][0]["signals"] = []
        result = storyverify.verify(claimed, PATCH_CONSTANT)
        self.assertIsInstance(result, dict)
        self.assertTrue(result["lies"])

    def test_true_story_for_other_patch_fails(self):
        story = _true_story(PATCH_CONSTANT)
        result = storyverify.verify(story, PATCH_INVERT)
        self.assertIsInstance(result, dict)
        self.assertTrue(result["lies"])


class RefusesVacuousInput(unittest.TestCase):
    def test_non_dict_story_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify("a story string", PATCH_CONSTANT)
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(None, PATCH_CONSTANT)
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(["hunks"], PATCH_CONSTANT)

    def test_story_missing_hunks_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify({"files": ["rtl/uart.v"]},
                               PATCH_CONSTANT)

    def test_story_with_non_list_hunks_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify({"hunks": "hunk 1"}, PATCH_CONSTANT)

    def test_story_with_empty_hunks_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify({"hunks": []}, PATCH_CONSTANT)

    def test_hunk_missing_direction_raises(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        del claimed["hunks"][0]["direction"]
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(claimed, PATCH_CONSTANT)

    def test_hunk_missing_signals_raises(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        del claimed["hunks"][0]["signals"]
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(claimed, PATCH_CONSTANT)

    def test_hunk_with_non_string_signal_raises(self):
        story = _true_story(PATCH_CONSTANT)
        claimed = copy.deepcopy(story)
        claimed["hunks"][0]["signals"] = [42]
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(claimed, PATCH_CONSTANT)

    def test_non_string_patch_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(_true_story(PATCH_CONSTANT), None)
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(_true_story(PATCH_CONSTANT), 42)

    def test_blank_patch_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(_true_story(PATCH_CONSTANT), "   \n  ")

    def test_patch_with_no_hunks_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            storyverify.verify(_true_story(PATCH_CONSTANT),
                               "just some prose, no diff here\n")


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("storyverify", text,
                             "%s must not wire in storyverify" % name)

    def test_no_live_api(self):
        src = os.path.join(ROOT, "agent", "repair", "storyverify.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        tree = ast.parse(text)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "socket", "http",
                     "openai", "anthropic"}
        self.assertTrue(imported.isdisjoint(forbidden),
                        "non-stdlib/API imports: %s"
                        % (imported & forbidden))
        lowered = text.lower()
        for token in ("model_api_key", "requests", "urllib"):
            self.assertNotIn(token, lowered,
                             "storyverify.py must not touch live APIs: %r"
                             % token)

    def test_storyverify_consumes_diffstory(self):
        with open(os.path.join(ROOT, "agent", "repair",
                               "storyverify.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("diffstory", src,
                      "storyverify.py must consume "
                      "agent/trust/diffstory.py")

    def test_standalone_no_loop_guard_imports_no_io(self):
        path = os.path.join(ROOT, "agent", "repair", "storyverify.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                for alias in node.names:
                    imported.add(alias.name)
        for banned in ("agent.loop", "agent.guard", "loop", "guard"):
            self.assertFalse(
                any(name == banned or name.endswith("." + banned)
                    for name in imported),
                "storyverify must stay default-off (no loop/guard "
                "import), got %r" % imported)
        for banned in ("try_apply_diff", "save_registry", "record_fixed",
                       "open("):
            self.assertNotIn(banned, src,
                             "storyverify must stay default-off/read-only: %r"
                             % banned)


if __name__ == "__main__":
    unittest.main()
