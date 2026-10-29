"""Task C1 (repair templates, plan item 21, Wave C FIX BETTER) tests:
agent/repair/templates.py.

Guarded sketch-fill for 3 bug shapes: (1) reset-missing (sequential
always block without a reset branch -> add ``if (!rst_n) q <= 0; else``
guard), (2) off-by-one (counter compare ``cnt == N`` boundary shape ->
propose decrement to N-1), (3) width-mismatch (assignment truncating a
wider rhs into a narrower lhs -> propose explicit slice/mask).

Each template exposes ``guard(lines) -> (fires, reason)`` and
``fill(lines) -> new_lines``. A template fires ONLY on its own shape:
each template fires on its pinned fixture and refuses the other two
fixtures plus a non-matching file, with pinned refusal reasons. fill()
on a refused shape raises ValueError carrying the refusal reason
(template applies only if guard passes).

RED-first TDD: this file was written BEFORE agent/repair/templates.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_templates.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import templates  # noqa: E402  (RED before implementation)


RESET_FIXTURE = [
    "module reg1(input clk, input rst_n, input d, output reg q);",
    "always @(posedge clk) begin",
    "q <= d;",
    "end",
    "endmodule",
]

OBO_FIXTURE = [
    "module cnt(input clk, input rst_n, output reg tick);",
    "reg [3:0] cnt;",
    "always @(posedge clk) begin",
    "if (!rst_n) cnt <= 0;",
    "else if (cnt == 10) begin",
    "cnt <= 0;",
    "tick <= 1;",
    "end else cnt <= cnt + 1;",
    "end",
    "endmodule",
]

WIDTH_FIXTURE = [
    "module trunc(input clk, input rst_n, input [15:0] data,",
    "  output reg [7:0] y);",
    "always @(posedge clk) begin",
    "if (!rst_n) y <= 0;",
    "else y <= data;",
    "end",
    "endmodule",
]

NONMATCHING = [
    "module comb(input a, input b, output y);",
    "assign y = a & b;",
    "endmodule",
]

# Pinned refusal reasons (exact strings, also defined in templates.py).
RESET_NO_BLOCK = (
    "reset-missing: no sequential always @(posedge ...) block found")
RESET_PRESENT = "reset-missing: reset branch already present"
OBO_NONE = "off-by-one: no counter equality compare (== N) found"
WIDTH_NONE = (
    "width-mismatch: no truncating assignment "
    "(wider rhs into narrower lhs) found")


class ResetMissingFires(unittest.TestCase):
    def test_fires_on_reset_fixture(self):
        fires, reason = templates.guard_reset_missing(RESET_FIXTURE)
        self.assertTrue(fires, reason)
        self.assertIn("reset-missing", reason)

    def test_fill_adds_reset_guard(self):
        out = templates.fill_reset_missing(RESET_FIXTURE)
        text = "\n".join(out)
        self.assertIn("if (!rst_n) q <= 0;", text)
        self.assertIn("else q <= d;", text)
        # Original driver statement is preserved, not dropped.
        self.assertIn("q <= d;", text)
        self.assertEqual(len(out), len(RESET_FIXTURE) + 1)

    def test_fill_refuses_when_guard_fails(self):
        with self.assertRaises(ValueError) as cm:
            templates.fill_reset_missing(OBO_FIXTURE)
        self.assertEqual(str(cm.exception), RESET_PRESENT)


class ResetMissingRefuses(unittest.TestCase):
    def test_refuses_obo_fixture_reset_present(self):
        fires, reason = templates.guard_reset_missing(OBO_FIXTURE)
        self.assertFalse(fires)
        self.assertEqual(reason, RESET_PRESENT)

    def test_refuses_width_fixture_reset_present(self):
        fires, reason = templates.guard_reset_missing(WIDTH_FIXTURE)
        self.assertFalse(fires)
        self.assertEqual(reason, RESET_PRESENT)

    def test_refuses_nonmatching_no_block(self):
        fires, reason = templates.guard_reset_missing(NONMATCHING)
        self.assertFalse(fires)
        self.assertEqual(reason, RESET_NO_BLOCK)


class OffByOneFires(unittest.TestCase):
    def test_fires_on_obo_fixture(self):
        fires, reason = templates.guard_off_by_one(OBO_FIXTURE)
        self.assertTrue(fires, reason)
        self.assertIn("off-by-one", reason)

    def test_fill_decrements_bound(self):
        out = templates.fill_off_by_one(OBO_FIXTURE)
        text = "\n".join(out)
        self.assertIn("cnt == 9", text)
        self.assertNotIn("cnt == 10", text)
        self.assertEqual(len(out), len(OBO_FIXTURE))

    def test_fill_refuses_when_guard_fails(self):
        with self.assertRaises(ValueError) as cm:
            templates.fill_off_by_one(RESET_FIXTURE)
        self.assertEqual(str(cm.exception), OBO_NONE)


class OffByOneRefuses(unittest.TestCase):
    def test_refuses_reset_fixture(self):
        fires, reason = templates.guard_off_by_one(RESET_FIXTURE)
        self.assertFalse(fires)
        self.assertEqual(reason, OBO_NONE)

    def test_refuses_width_fixture(self):
        fires, reason = templates.guard_off_by_one(WIDTH_FIXTURE)
        self.assertFalse(fires)
        self.assertEqual(reason, OBO_NONE)

    def test_refuses_nonmatching(self):
        fires, reason = templates.guard_off_by_one(NONMATCHING)
        self.assertFalse(fires)
        self.assertEqual(reason, OBO_NONE)


class WidthMismatchFires(unittest.TestCase):
    def test_fires_on_width_fixture(self):
        fires, reason = templates.guard_width_mismatch(WIDTH_FIXTURE)
        self.assertTrue(fires, reason)
        self.assertIn("width-mismatch", reason)

    def test_fill_adds_explicit_slice(self):
        out = templates.fill_width_mismatch(WIDTH_FIXTURE)
        text = "\n".join(out)
        self.assertIn("y <= data[7:0];", text)
        self.assertEqual(len(out), len(WIDTH_FIXTURE))

    def test_fill_refuses_when_guard_fails(self):
        with self.assertRaises(ValueError) as cm:
            templates.fill_width_mismatch(RESET_FIXTURE)
        self.assertEqual(str(cm.exception), WIDTH_NONE)


class WidthMismatchRefuses(unittest.TestCase):
    def test_refuses_reset_fixture(self):
        fires, reason = templates.guard_width_mismatch(RESET_FIXTURE)
        self.assertFalse(fires)
        self.assertEqual(reason, WIDTH_NONE)

    def test_refuses_obo_fixture(self):
        fires, reason = templates.guard_width_mismatch(OBO_FIXTURE)
        self.assertFalse(fires)
        self.assertEqual(reason, WIDTH_NONE)

    def test_refuses_nonmatching(self):
        fires, reason = templates.guard_width_mismatch(NONMATCHING)
        self.assertFalse(fires)
        self.assertEqual(reason, WIDTH_NONE)


class DispatcherAndDefaults(unittest.TestCase):
    def test_template_names_pinned(self):
        self.assertEqual(templates.TEMPLATE_NAMES,
                         ("reset-missing", "off-by-one", "width-mismatch"))

    def test_dispatch_guards_match_direct(self):
        for name, direct, fixture in (
                ("reset-missing", templates.guard_reset_missing,
                 RESET_FIXTURE),
                ("off-by-one", templates.guard_off_by_one, OBO_FIXTURE),
                ("width-mismatch", templates.guard_width_mismatch,
                 WIDTH_FIXTURE)):
            with self.subTest(name=name):
                self.assertEqual(templates.guard(name, fixture),
                                 direct(fixture))

    def test_dispatch_fill_matches_direct(self):
        self.assertEqual(templates.fill("reset-missing", RESET_FIXTURE),
                         templates.fill_reset_missing(RESET_FIXTURE))
        self.assertEqual(templates.fill("off-by-one", OBO_FIXTURE),
                         templates.fill_off_by_one(OBO_FIXTURE))
        self.assertEqual(templates.fill("width-mismatch", WIDTH_FIXTURE),
                         templates.fill_width_mismatch(WIDTH_FIXTURE))

    def test_unknown_template_raises(self):
        with self.assertRaises(ValueError):
            templates.guard("no-such-template", RESET_FIXTURE)
        with self.assertRaises(ValueError):
            templates.fill("no-such-template", RESET_FIXTURE)

    def test_bad_input_raises_loudly(self):
        for bad in (None, "always @(posedge clk)", 123, [None], [123]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    templates.guard_reset_missing(bad)
                with self.assertRaises(ValueError):
                    templates.guard_off_by_one(bad)
                with self.assertRaises(ValueError):
                    templates.guard_width_mismatch(bad)


class DefaultOffNoGuardWiring(unittest.TestCase):
    def test_templates_import_nothing_from_loop_or_guard(self):
        path = os.path.join(ROOT, "agent", "repair", "templates.py")
        with open(path) as fh:
            src = fh.read()
        for banned in ("from loop import", "from guard import",
                       "import loop", "import guard",
                       "try_apply_diff"):
            self.assertNotIn(banned, src,
                             "templates must stay default-off "
                             "(no loop/guard wiring): %r" % banned)

    def test_loop_and_guard_have_no_repair_wiring(self):
        for rel in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, rel)
            with open(path) as fh:
                src = fh.read()
            for banned in ("from repair", "import repair", "repair/",
                           "repair.templates"):
                with self.subTest(file=rel, banned=banned):
                    self.assertNotIn(
                        banned, src,
                        "%s untouched: no repair wiring" % rel)


if __name__ == "__main__":
    unittest.main()
