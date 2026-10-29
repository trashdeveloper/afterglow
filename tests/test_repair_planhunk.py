"""Task C2 (multi-hunk planner, plan item 22, Wave C FIX BETTER) tests:
agent/repair/planhunk.py.

Plan-then-diff validation: the model emits a PLANNED HUNKS list
(file:line pairs) plus a unified diff; the loop validates every
planned hunk landed via validate_landed().

  parse_plan(text) -> list[str]          # ["picorv32.v:373", ...]
  validate_landed(plan, applied_diff)   # {"landed_all": bool, "missing": [...]}

Missing-hunk detection is by @@-header range coverage: a planned
"file:line" hunk counts as landed iff its file matches the diff's
target file (from ``diff --git``/``+++`` lines) and its line falls in
one of that file's ``@@ -a,b +c,d @@`` new-side ranges. Literal
"file:line" marker comments in the diff body are ignored.

Contract:
- 2-hunk plan with 1 landed -> incomplete, missing == [exact hunk].
- 2/2 landed -> complete (landed_all True, missing []).
- Empty plan (no text / no PLANNED HUNKS / no pairs / empty list)
  -> ValueError.
- Non-diff applied text -> ValueError.
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no planhunk wiring).

RED-first TDD: this file was written BEFORE agent/repair/planhunk.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_planhunk.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import planhunk  # noqa: E402  (RED before implementation)


PLAN_TWO_HUNKS = """DIAGNOSIS: mem interface pair fault.

PLANNED HUNKS:
- picorv32.v:373
- picorv32.v:376

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -370,8 +370,8 @@
 // picorv32.v:373
-assign mem_xfer = (mem_valid && mem_ready) && (mem_la_use_prefetched_high_word && mem_do_rinst);
+assign mem_xfer = (mem_valid && mem_ready) || (mem_la_use_prefetched_high_word && mem_do_rinst);
 // picorv32.v:376
-wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) && (&mem_state && mem_do_rinst)) &&
+wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) &&
```
"""

DIFF_BOTH_LANDED = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -370,8 +370,8 @@
 // picorv32.v:373
-assign mem_xfer = (mem_valid && mem_ready) && (mem_la_use_prefetched_high_word && mem_do_rinst);
+assign mem_xfer = (mem_valid && mem_ready) || (mem_la_use_prefetched_high_word && mem_do_rinst);
 // picorv32.v:376
-wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) && (&mem_state && mem_do_rinst)) &&
+wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) &&
"""

DIFF_ONE_LANDED = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -370,4 +370,4 @@
 // picorv32.v:373
-assign mem_xfer = (mem_valid && mem_ready) && (mem_la_use_prefetched_high_word && mem_do_rinst);
+assign mem_xfer = (mem_valid && mem_ready) || (mem_la_use_prefetched_high_word && mem_do_rinst);
"""

# VERBATIM live diff from
# runs/planhunk-c2-live-20261029T031046Z/api/text_response.json
# (content field -> "diff" string, decoded exactly; note: it carries
# NO "// picorv32.v:NNN" marker comments -- the old literal-substring
# check reported both hunks missing here, the range-coverage check
# lands both via the @@ new-side ranges).
LIVE_DIFF_BOTH_LANDED = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -373,1 +373,1 @@
-assign mem_xfer = (mem_valid && mem_ready) && (mem_la_use_prefetched_high_word && mem_do_rinst);
+assign mem_xfer = (mem_valid && mem_ready) || (mem_la_use_prefetched_high_word && mem_do_rinst);
@@ -376,1 +376,1 @@
-wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) && (&mem_state && mem_do_rinst)) &&
+wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) &&
"""

# Partial real-diff case: live diff with the second @@ hunk removed.
LIVE_DIFF_ONE_LANDED = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -373,1 +373,1 @@
-assign mem_xfer = (mem_valid && mem_ready) && (mem_la_use_prefetched_high_word && mem_do_rinst);
+assign mem_xfer = (mem_valid && mem_ready) || (mem_la_use_prefetched_high_word && mem_do_rinst);
"""


class ParsePlan(unittest.TestCase):
    def test_parses_two_hunk_plan(self):
        plan = planhunk.parse_plan(PLAN_TWO_HUNKS)
        self.assertEqual(plan, ["picorv32.v:373", "picorv32.v:376"])

    def test_parses_bare_lines_without_dashes(self):
        text = "PLANNED HUNKS\npicorv32.v:373\npicorv32.v:376\n"
        self.assertEqual(planhunk.parse_plan(text),
                         ["picorv32.v:373", "picorv32.v:376"])

    def test_empty_text_raises(self):
        for bad in ("", "   ", "\n"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    planhunk.parse_plan(bad)

    def test_no_planned_hunks_section_raises(self):
        with self.assertRaises(ValueError):
            planhunk.parse_plan("DIAGNOSIS: nothing planned here.\n"
                                "diff --git a/x b/x\n")

    def test_header_without_pairs_raises(self):
        with self.assertRaises(ValueError):
            planhunk.parse_plan("PLANNED HUNKS:\n(none)\n")

    def test_non_string_raises(self):
        for bad in (None, 123, ["picorv32.v:373"], {"hunks": []}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    planhunk.parse_plan(bad)


class ValidateLanded(unittest.TestCase):
    def test_partial_plan_flagged_incomplete_with_exact_missing(self):
        plan = ["picorv32.v:373", "picorv32.v:376"]
        result = planhunk.validate_landed(plan, DIFF_ONE_LANDED)
        self.assertFalse(result["landed_all"])
        self.assertEqual(result["missing"], ["picorv32.v:376"])

    def test_full_plan_complete(self):
        plan = ["picorv32.v:373", "picorv32.v:376"]
        result = planhunk.validate_landed(plan, DIFF_BOTH_LANDED)
        self.assertTrue(result["landed_all"])
        self.assertEqual(result["missing"], [])

    def test_roundtrip_parse_then_validate(self):
        plan = planhunk.parse_plan(PLAN_TWO_HUNKS)
        result = planhunk.validate_landed(plan, DIFF_ONE_LANDED)
        self.assertFalse(result["landed_all"])
        self.assertEqual(result["missing"], ["picorv32.v:376"])
        full = planhunk.validate_landed(plan, DIFF_BOTH_LANDED)
        self.assertTrue(full["landed_all"])
        self.assertEqual(full["missing"], [])

    def test_empty_plan_raises(self):
        with self.assertRaises(ValueError):
            planhunk.validate_landed([], DIFF_BOTH_LANDED)
        with self.assertRaises(ValueError):
            planhunk.validate_landed((), DIFF_BOTH_LANDED)
        with self.assertRaises(ValueError):
            planhunk.validate_landed(None, DIFF_BOTH_LANDED)
        with self.assertRaises(ValueError):
            planhunk.validate_landed("picorv32.v:373", DIFF_BOTH_LANDED)

    def test_non_diff_applied_text_raises(self):
        plan = ["picorv32.v:373", "picorv32.v:376"]
        for bad in ("hello world", "PLANNED HUNKS:\n- picorv32.v:373\n",
                    "", "   ", None, 123):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    planhunk.validate_landed(plan, bad)

    def test_malformed_plan_entries_raise(self):
        with self.assertRaises(ValueError):
            planhunk.validate_landed(["no-colon-here"], DIFF_BOTH_LANDED)
        with self.assertRaises(ValueError):
            planhunk.validate_landed([123], DIFF_BOTH_LANDED)


class LiveDiffRegression(unittest.TestCase):
    """C2 NEEDS_FIX regression: verbatim live diff has no markers.

    Source: runs/planhunk-c2-live-20261029T031046Z/api/text_response.json
    content field -> "diff" string, copied exactly (no "// picorv32.v:NNN"
    comments). Range coverage must land both hunks; the partial case
    (second @@ hunk removed) must report exactly ["picorv32.v:376"].
    """

    def test_live_diff_both_hunks_landed_without_markers(self):
        self.assertNotIn("picorv32.v:373", LIVE_DIFF_BOTH_LANDED)
        self.assertNotIn("picorv32.v:376", LIVE_DIFF_BOTH_LANDED)
        plan = ["picorv32.v:373", "picorv32.v:376"]
        result = planhunk.validate_landed(plan, LIVE_DIFF_BOTH_LANDED)
        self.assertTrue(result["landed_all"])
        self.assertEqual(result["missing"], [])

    def test_live_diff_partial_reports_exact_missing(self):
        plan = ["picorv32.v:373", "picorv32.v:376"]
        result = planhunk.validate_landed(plan, LIVE_DIFF_ONE_LANDED)
        self.assertFalse(result["landed_all"])
        self.assertEqual(result["missing"], ["picorv32.v:376"])


class DefaultOffNoGuardWiring(unittest.TestCase):
    def test_planhunk_imports_nothing_from_loop_or_guard(self):
        path = os.path.join(ROOT, "agent", "repair", "planhunk.py")
        with open(path) as fh:
            src = fh.read()
        for banned in ("from loop import", "from guard import",
                       "import loop", "import guard",
                       "try_apply_diff"):
            self.assertNotIn(banned, src,
                             "planhunk must stay default-off "
                             "(no loop/guard wiring): %r" % banned)

    def test_loop_and_guard_have_no_planhunk_wiring(self):
        for rel in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, rel)
            with open(path) as fh:
                src = fh.read()
            for banned in ("planhunk", "parse_plan", "validate_landed"):
                with self.subTest(file=rel, banned=banned):
                    self.assertNotIn(
                        banned, src,
                        "%s untouched: no planhunk wiring" % rel)

    def test_no_api_or_keys_in_module(self):
        path = os.path.join(ROOT, "agent", "repair", "planhunk.py")
        with open(path) as fh:
            src = fh.read()
        for banned in ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY",
                       "urllib", "requests", "socket", "os.environ"):
            self.assertNotIn(banned, src,
                             "planhunk is SIM-ONLY pure stdlib "
                             "(no API/keys): %r" % banned)


if __name__ == "__main__":
    unittest.main()
