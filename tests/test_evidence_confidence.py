"""Task B9 (confidence calibration, plan item 19, Wave B SEE MORE) tests:
agent/evidence/confidence.py.

Model self-reports fix confidence 1-5 via a CONFIDENCE: N marker; the
loop budgets follow-up attempts proportionally (low confidence -> more
attempts, never fewer than 1). This module is utility + mapping ONLY:
parsing and budgeting, no loop wiring, no API.

RED-first TDD: this file was written BEFORE agent/evidence/confidence.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_evidence_confidence.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import confidence  # noqa: E402  (RED before implementation)


class ParseConfidenceBasics(unittest.TestCase):
    def test_all_valid_levels(self):
        for n in (1, 2, 3, 4, 5):
            with self.subTest(n=n):
                self.assertEqual(
                    confidence.parse_confidence("CONFIDENCE: %d" % n), n)

    def test_embedded_in_model_output(self):
        text = ('{"diagnosis": "divisor stuck at 0, baud wrong", '
                '"patch": "rtl fix ..."} \nCONFIDENCE: 4\n')
        self.assertEqual(confidence.parse_confidence(text), 4)

    def test_case_insensitive(self):
        self.assertEqual(confidence.parse_confidence("confidence: 2"), 2)
        self.assertEqual(confidence.parse_confidence("Confidence: 5"), 5)

    def test_whitespace_variants(self):
        self.assertEqual(confidence.parse_confidence("CONFIDENCE:5"), 5)
        self.assertEqual(confidence.parse_confidence("CONFIDENCE : 3"), 3)
        self.assertEqual(confidence.parse_confidence("CONFIDENCE:\t1"), 1)
        self.assertEqual(
            confidence.parse_confidence("  CONFIDENCE:   2  "), 2)

    def test_last_marker_wins(self):
        # Reasoning may mention an early guess; the final self-report
        # at the end of the output is authoritative.
        text = ("I am fairly sure CONFIDENCE: 2 but after re-checking "
                "the trap line I stand by the fix.\nCONFIDENCE: 5")
        self.assertEqual(confidence.parse_confidence(text), 5)


class ParseConfidenceNeverInvents(unittest.TestCase):
    def test_missing_marker_is_none(self):
        self.assertIsNone(confidence.parse_confidence(
            '{"diagnosis": "looks like a divisor bug"}'))
        self.assertIsNone(confidence.parse_confidence(""))
        self.assertIsNone(confidence.parse_confidence("   \n  "))

    def test_unparseable_values_are_none(self):
        for text in ("CONFIDENCE: high",
                     "CONFIDENCE: 0",
                     "CONFIDENCE: 6",
                     "CONFIDENCE: 42",
                     "CONFIDENCE: 3.5",
                     "CONFIDENCE: -1",
                     "CONFIDENCE:",
                     "CONFIDENCE 4",       # no colon: not the marker
                     "CONFIDENT: 4",       # wrong keyword
                     "my confidence is 4 out of 5"):  # prose, no marker
            with self.subTest(text=text):
                self.assertIsNone(confidence.parse_confidence(text),
                                  "must never invent a level: %r" % (text,))

    def test_out_of_range_digit_prefix_is_none(self):
        # "CONFIDENCE: 42" must not parse as 4; the digit run is one
        # token and it is out of range.
        self.assertIsNone(confidence.parse_confidence("CONFIDENCE: 42"))
        self.assertIsNone(confidence.parse_confidence("CONFIDENCE: 10"))

    def test_non_string_raises(self):
        for bad in (None, 4, 4.0, ["CONFIDENCE: 4"], {"c": 4}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    confidence.parse_confidence(bad)


class BudgetMappingPinned(unittest.TestCase):
    # Spec mapping: 5->base, 4->base, 3->base+1, 2->base+2, 1->base+3,
    # None->base+1 (conservative: unknown confidence gets one extra).
    PINNED_BASE_2 = {5: 2, 4: 2, 3: 3, 2: 4, 1: 5, None: 3}
    PINNED_BASE_3 = {5: 3, 4: 3, 3: 4, 2: 5, 1: 6, None: 4}

    def test_pinned_table_base_2(self):
        for conf, want in self.PINNED_BASE_2.items():
            with self.subTest(conf=conf):
                self.assertEqual(
                    confidence.budget_for_confidence(conf, 2), want)

    def test_pinned_table_base_3(self):
        for conf, want in self.PINNED_BASE_3.items():
            with self.subTest(conf=conf):
                self.assertEqual(
                    confidence.budget_for_confidence(conf, 3), want)

    def test_high_confidence_never_exceeds_base(self):
        for base in (1, 2, 3, 5):
            for conf in (4, 5):
                self.assertEqual(
                    confidence.budget_for_confidence(conf, base), base)

    def test_floor_never_fewer_than_one(self):
        self.assertEqual(confidence.budget_for_confidence(5, 0), 1)
        self.assertEqual(confidence.budget_for_confidence(4, 0), 1)
        self.assertEqual(confidence.budget_for_confidence(5, 1), 1)
        self.assertEqual(confidence.budget_for_confidence(None, 0), 1)
        # Floor only clamps below-1 results; the mapping still applies.
        self.assertEqual(confidence.budget_for_confidence(1, 0), 3)
        self.assertEqual(confidence.budget_for_confidence(2, 0), 2)

    def test_returns_int(self):
        res = confidence.budget_for_confidence(3, 2)
        self.assertIsInstance(res, int)
        self.assertEqual(res, 3)

    def test_invalid_confidence_raises(self):
        for bad in (0, 6, -1, 42, "3", 3.0, True, [3]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    confidence.budget_for_confidence(bad, 2)

    def test_invalid_base_raises(self):
        for bad in (-1, -5, "2", 2.0, None, [2]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    confidence.budget_for_confidence(3, bad)


class DefaultOffContract(unittest.TestCase):
    def test_loop_untouched(self):
        loop = os.path.join(ROOT, "agent", "loop.py")
        with open(loop, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("confidence", text,
                         "Wave B default-off: loop.py must not wire this in")

    def test_stdlib_only(self):
        src = os.path.join(ROOT, "agent", "evidence", "confidence.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        imports = [ln.strip() for ln in text.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        for ln in imports:
            self.assertRegex(
                ln, r"^(import re|from re import)",
                "confidence.py must stay pure stdlib (re only): %r" % ln)
        lowered = text.lower()
        for token in ("urllib", "socket", "http", "bearer", "api_key",
                      "apikey", "open(", "subprocess"):
            self.assertNotIn(token, lowered,
                             "confidence.py must do no I/O or network: %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
