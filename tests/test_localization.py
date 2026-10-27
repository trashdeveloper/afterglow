"""Top-1 localization metric test (wave-3).

Runs eval/localization.py (no API, no sim), checks the report SHAPE:
53 cases scored (45 mut-bigN + 8 board), accuracy line present and in
range. Deliberately asserts NO accuracy threshold -- honesty over
gaming. The measured number lives in eval/localization.md.
"""
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LocalizationReport(unittest.TestCase):
    def test_report_generated_with_53_cases(self):
        from eval.localization import generate, TARGET_TOTAL
        summary = generate()
        self.assertEqual(summary["rows"], TARGET_TOTAL)
        self.assertEqual(summary["rows"], 53)
        report = summary["report"]
        self.assertTrue(os.path.isfile(report))
        with open(report, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("top1_accuracy", text)
        m = re.search(r"top1_accuracy:\s*(\d+)/(\d+)\s*=\s*([\d.]+)%",
                      text)
        self.assertIsNotNone(m, "accuracy line missing from report")
        hits, total = int(m.group(1)), int(m.group(2))
        self.assertEqual(total, 53)
        self.assertGreaterEqual(hits, 0)
        self.assertLessEqual(hits, 53)
        # Every mut spec id and every board case id appears in the table.
        for n in range(1, 46):
            self.assertIn("mut-bigN-%02d" % n, text)
        for cid in ("picosoc-uart-div", "picorv32-fetch-misalign",
                    "uart-tx-div", "uart-start-half", "fetch-highword",
                    "rdata-latch", "trap-stuck", "pc-incr"):
            self.assertIn(cid, text)
        # Row count: 53 table rows with a hit/miss verdict.
        verdicts = re.findall(r"\|\s*(?:HIT|miss)\s*\|", text)
        self.assertEqual(len(verdicts), 53)


if __name__ == "__main__":
    unittest.main()
