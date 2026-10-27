"""Context-pack v2: scored packer tests (fixtures, no network).

Run: python3 tests/test_packer.py -v   (from repo root)

- test_score_order: sections totaling 3x budget merge to the top two by
  fixed score; dropped_names names the loser.
- test_caps: pack output stays <=200 lines / <=2500 tokens with exact
  counts logged.
- test_provenance_headers: every emitted section carries a provenance
  header (name + counts).
- test_verdict_never_dropped: under extreme pressure the verdict section
  survives (mandatory, not scored).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import packer


FP_REC = {
    "fingerprint": "ab" * 32,
    "trap_line": "TRAP: uart data mismatch got 0xe0 want 0xa5",
    "oracle_sig": "FAIL 0xdead",
    "signals": ["recv_divcnt", "rdata"],
}

CONE_LINES = ["picosoc/simpleuart.v:42: if (recv_divcnt > cfg_divider) begin",
              "picosoc/simpleuart.v:43: recv_divcnt <= recv_divcnt + 1;"]

CONE_REPORT = {"seed": "recv_divcnt", "resolved": 2, "sought": 2,
               "unparsed": 0, "lines": 2, "files": ["picosoc/simpleuart.v"]}

VERDICT_TXT = '{"case": "picosoc-uart-div", "pass": false, "tohost": "FAIL 0xdead"}'


class ScoreMerge(unittest.TestCase):
    def test_score_order(self):
        mk = lambda name: ("%s " % name + "x " * 600, name)
        bodies = [mk("fingerprint"), mk("cone_slice"), mk("past_fixes")]
        sections = [(name, body, score)
                    for (body, name), score in
                    zip(bodies, (1.0, 0.9, 0.4))]
        scored = packer.score_sections(sections)
        self.assertEqual([s[0] for s in scored],
                         ["fingerprint", "cone_slice", "past_fixes"])
        text, dropped = packer.merge_scored(scored, budget_tokens=1300)
        self.assertIn("fingerprint", text)
        self.assertIn("cone_slice", text)
        self.assertEqual(dropped, ["past_fixes"])

    def test_caps(self):
        text, counts = packer.build_v3_pack(FP_REC, CONE_LINES, CONE_REPORT,
                                           VERDICT_TXT)
        self.assertLessEqual(counts["pack_lines"], 200)
        self.assertLessEqual(counts["pack_tokens"], 2500)
        self.assertEqual(counts["pack_lines"], len(text.splitlines()))
        self.assertEqual(counts["pack_tokens"], packer.approx_tokens(text))
        self.assertIn(counts["fingerprint"], text)

    def test_provenance_headers(self):
        text, _counts = packer.build_v3_pack(FP_REC, CONE_LINES, CONE_REPORT,
                                             VERDICT_TXT)
        self.assertIn("### FINGERPRINT", text)
        self.assertIn("### CONE_SLICE", text)
        self.assertIn("### VERDICT", text)
        # Each header logs its own counts.
        self.assertRegex(text, r"### FINGERPRINT \[.*tokens: \d+.*\]")
        self.assertRegex(text, r"### CONE_SLICE \[.*lines: \d+.*\]")
        self.assertRegex(text, r"### VERDICT \[.*tokens: \d+.*\]")

    def test_verdict_never_dropped(self):
        huge_cone = ["f.v:%d: wire w%d = seed + %d;" % (i, i, i)
                     for i in range(1, 500)]
        report = dict(CONE_REPORT, lines=len(huge_cone))
        text, counts = packer.build_v3_pack(FP_REC, huge_cone, report,
                                           VERDICT_TXT,
                                           budget_tokens=300)
        self.assertIn("### VERDICT", text)
        self.assertIn("FAIL 0xdead", text)
        self.assertLessEqual(counts["pack_tokens"], 2500)


if __name__ == "__main__":
    unittest.main()
