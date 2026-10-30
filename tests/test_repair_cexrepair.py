"""Task G2 (repair from counterexample, plan item 72) tests:
agent/repair/cexrepair.py.

Interface (plan text, verbatim):

  focus_from_first_diff(first_diff) -> {file, line, signals}

Contract:
- A pinned first-diff record yields the diff line as focus (file+line)
  with its signals extracted from the diff text.
- Signals are identifiers in order of first appearance, deduplicated,
  minus Verilog keywords and numeric literals.
- Malformed records (missing file/line/text, bad line, non-dict)
  -> ValueError.
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no cexrepair wiring).

RED-first TDD: this file was written BEFORE
agent/repair/cexrepair.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_cexrepair.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import cexrepair  # noqa: E402  (RED before implementation)


# Pinned first-diff record: first counterexample diff line.
FIRST_DIFF = {
    "file": "picorv32.v",
    "line": 373,
    "text": "assign mem_xfer = mem_valid & mem_ready;",
}


class FocusFromFirstDiff(unittest.TestCase):
    def test_pinned_first_diff_yields_diff_line_as_focus(self):
        focus = cexrepair.focus_from_first_diff(dict(FIRST_DIFF))
        self.assertEqual(focus["file"], "picorv32.v")
        self.assertEqual(focus["line"], 373)

    def test_pinned_first_diff_signals_extracted(self):
        focus = cexrepair.focus_from_first_diff(dict(FIRST_DIFF))
        self.assertEqual(focus["signals"],
                         ["mem_xfer", "mem_valid", "mem_ready"])

    def test_focus_keys_exact(self):
        focus = cexrepair.focus_from_first_diff(dict(FIRST_DIFF))
        self.assertEqual(sorted(focus.keys()), ["file", "line", "signals"])

    def test_signals_deduped_in_first_appearance_order(self):
        rec = {"file": "uart.v", "line": 41,
               "text": "assign tx_busy = tx_busy | tx_start;"}
        focus = cexrepair.focus_from_first_diff(rec)
        self.assertEqual(focus["signals"], ["tx_busy", "tx_start"])

    def test_keywords_and_numbers_excluded(self):
        rec = {"file": "uart.v", "line": 7,
               "text": "reg [7:0] divisor = 8'b00000000; always begin end"}
        focus = cexrepair.focus_from_first_diff(rec)
        self.assertEqual(focus["signals"], ["divisor"])

    def test_diff_marker_prefix_tolerated(self):
        rec = {"file": "picorv32.v", "line": 373,
               "text": "-assign mem_xfer = mem_valid;"}
        focus = cexrepair.focus_from_first_diff(rec)
        self.assertEqual(focus["file"], "picorv32.v")
        self.assertEqual(focus["line"], 373)
        self.assertEqual(focus["signals"], ["mem_xfer", "mem_valid"])

    def test_missing_keys_rejected(self):
        with self.assertRaises(ValueError):
            cexrepair.focus_from_first_diff({"file": "a.v", "line": 1})
        with self.assertRaises(ValueError):
            cexrepair.focus_from_first_diff({"line": 1, "text": "x"})
        with self.assertRaises(ValueError):
            cexrepair.focus_from_first_diff({"file": "a.v", "text": "x"})

    def test_bad_line_rejected(self):
        for bad in (0, -3, "373", 3.5, None):
            with self.assertRaises(ValueError, msg="line=%r" % (bad,)):
                cexrepair.focus_from_first_diff(
                    {"file": "a.v", "line": bad, "text": "assign x = y;"})

    def test_empty_file_or_text_rejected(self):
        with self.assertRaises(ValueError):
            cexrepair.focus_from_first_diff(
                {"file": "", "line": 1, "text": "assign x = y;"})
        with self.assertRaises(ValueError):
            cexrepair.focus_from_first_diff(
                {"file": "a.v", "line": 1, "text": "   "})

    def test_non_dict_rejected(self):
        with self.assertRaises(ValueError):
            cexrepair.focus_from_first_diff("picorv32.v:373")
        with self.assertRaises(ValueError):
            cexrepair.focus_from_first_diff(None)


if __name__ == "__main__":
    unittest.main()
