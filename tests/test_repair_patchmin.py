"""Task G3 (patch minimality enforcer, plan item 73) tests:
agent/repair/patchmin.py.

Interface (plan text, verbatim):

  minimize(patch, still_green_fn) -> smaller_patch

Contract:
- A 6-line passing fixture patch minimizes to <=3 lines still GREEN
  via the fixture predicate (ddmin: only the essential lines survive).
- The result is a sub-patch of the input (subset, order preserved)
  and still GREEN under still_green_fn.
- Malformed inputs (non-list patch, non-callable predicate, patch
  itself not GREEN) -> ValueError.
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no patchmin wiring).

RED-first TDD: this file was written BEFORE
agent/repair/patchmin.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_patchmin.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import patchmin  # noqa: E402  (RED before implementation)


# 6-line passing fixture patch; only 2 lines are essential for GREEN.
ESSENTIAL_A = "fix: latch divisor on baud tick"
ESSENTIAL_B = "fix: gate tx enable on busy flag"
FIXTURE_PATCH = [
    "cosmetic: rename local wire",
    ESSENTIAL_A,
    "comment-only: clarify reset polarity",
    "whitespace: reindent always block",
    ESSENTIAL_B,
    "comment-only: document baud table",
]


def fixture_still_green(candidate):
    """Fixture predicate: GREEN iff both essential lines are present."""
    present = set(candidate)
    return ESSENTIAL_A in present and ESSENTIAL_B in present


class MinimizeFixture(unittest.TestCase):
    def test_six_line_patch_minimizes_to_at_most_three_lines(self):
        smaller = patchmin.minimize(list(FIXTURE_PATCH), fixture_still_green)
        self.assertLessEqual(len(smaller), 3)

    def test_minimized_patch_still_green(self):
        smaller = patchmin.minimize(list(FIXTURE_PATCH), fixture_still_green)
        self.assertTrue(fixture_still_green(smaller))

    def test_minimized_patch_keeps_essentials(self):
        smaller = patchmin.minimize(list(FIXTURE_PATCH), fixture_still_green)
        self.assertIn(ESSENTIAL_A, smaller)
        self.assertIn(ESSENTIAL_B, smaller)

    def test_result_is_subpatch_in_input_order(self):
        smaller = patchmin.minimize(list(FIXTURE_PATCH), fixture_still_green)
        self.assertEqual(sorted(smaller), sorted(set(smaller) & set(FIXTURE_PATCH)))
        positions = [list(FIXTURE_PATCH).index(line) for line in smaller]
        self.assertEqual(positions, sorted(positions))

    def test_already_minimal_patch_unchanged(self):
        minimal = [ESSENTIAL_A, ESSENTIAL_B]
        smaller = patchmin.minimize(
            list(minimal), fixture_still_green)
        self.assertEqual(smaller, minimal)

    def test_single_essential_line_patch(self):
        smaller = patchmin.minimize(
            ["noise: tweak comment", ESSENTIAL_A, ESSENTIAL_B],
            fixture_still_green)
        self.assertLessEqual(len(smaller), 3)
        self.assertTrue(fixture_still_green(smaller))

    def test_red_patch_rejected(self):
        with self.assertRaises(ValueError):
            patchmin.minimize(["unrelated line"], fixture_still_green)

    def test_non_list_patch_rejected(self):
        for bad in ("a patch string", None, 42, {"lines": []}):
            with self.assertRaises(ValueError, msg="patch=%r" % (bad,)):
                patchmin.minimize(bad, fixture_still_green)

    def test_non_callable_predicate_rejected(self):
        for bad in (None, True, "green", 42):
            with self.assertRaises(ValueError, msg="fn=%r" % (bad,)):
                patchmin.minimize(list(FIXTURE_PATCH), bad)

    def test_input_patch_not_mutated(self):
        original = list(FIXTURE_PATCH)
        patchmin.minimize(original, fixture_still_green)
        self.assertEqual(original, FIXTURE_PATCH)


if __name__ == "__main__":
    unittest.main()
