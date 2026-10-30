"""Flake-adjusted scoring tests (Task H8, plan item 88).

`agent/trust/scoreadjust.py` produces a SEPARATE flake-adjusted score
report: `adjusted_score(raw_rows, flake_evidence) -> {score, deductions}`.
It consumes `agent/evidence/flake_fp.py` flake classes read-only and never
edits `eval/score.py`.

- A fixture score adjusts exactly by the proven-flake count, with
  evidence links attached to every deduction/credit entry.
- Unknown (unproven) flakes grant no credit.

RED-first TDD: this file was written BEFORE agent/trust/scoreadjust.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_scoreadjust -v   (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import scoreadjust  # noqa: E402  (RED before implementation)


RAW_ROWS = [
    {"case": "picosoc-uart-div", "pass": True},
    {"case": "picorv32-fetch-misalign", "pass": True},
    {"case": "uart-tx-div", "pass": True},
    {"case": "uart-start-half", "pass": True},
    {"case": "fetch-highword", "pass": True},
    {"case": "rdata-latch", "pass": True},
    {"case": "trap-stuck", "pass": False},
    {"case": "pc-incr", "pass": False},
]

# Two proven flakes (timeout + empty), one unproven (unknown): only the
# two proven ones may move the score.
FLAKE_EVIDENCE = [
    {"case": "trap-stuck", "raw": "trace run timed out after 300s"},
    {"case": "pc-incr", "raw": "EMPTY content from text call"},
    {"case": "rdata-latch", "raw": "some unrecognized wobble, no marker"},
]


class TestAdjustedScoreMovesByProvenFlakeCount(unittest.TestCase):
    def test_score_adjusts_exactly_by_proven_flake_count(self):
        raw = sum(1 for r in RAW_ROWS if r["pass"])
        self.assertEqual(raw, 6)
        res = scoreadjust.adjusted_score(RAW_ROWS, FLAKE_EVIDENCE)
        self.assertEqual(res["score"], raw + 2,
                         "score must move exactly by the proven-flake count")
        self.assertEqual(len(res["deductions"]), 2, res)

    def test_every_credit_carries_an_evidence_link(self):
        res = scoreadjust.adjusted_score(RAW_ROWS, FLAKE_EVIDENCE)
        cases = sorted(d["case"] for d in res["deductions"])
        self.assertEqual(cases, ["pc-incr", "trap-stuck"])
        for d in res["deductions"]:
            self.assertTrue(d.get("link"), "credit needs an evidence link: %r" % (d,))
            self.assertIn(d["case"], d["link"])
            self.assertTrue(d.get("kind"), d)
            self.assertNotEqual(d["kind"], "unknown")
            self.assertEqual(d.get("credit"), 1)

    def test_unknown_flake_grants_no_credit(self):
        res = scoreadjust.adjusted_score(
            RAW_ROWS, [{"case": "trap-stuck",
                        "raw": "some unrecognized wobble, no marker"}])
        self.assertEqual(res["score"], 6)
        self.assertEqual(res["deductions"], [])

    def test_clean_run_is_unchanged(self):
        clean = [dict(r, **{"pass": True}) for r in RAW_ROWS]
        res = scoreadjust.adjusted_score(clean, [])
        self.assertEqual(res["score"], len(clean))
        self.assertEqual(res["deductions"], [])

    def test_preclassified_flake_dicts_work(self):
        ev = [{"case": "trap-stuck", "kind": "timeout",
               "signature": "timed out"},
              {"case": "pc-incr", "kind": "rate_limit",
               "signature": "429"}]
        res = scoreadjust.adjusted_score(RAW_ROWS, ev)
        self.assertEqual(res["score"], 8, res)
        self.assertEqual(len(res["deductions"]), 2)

    def test_flake_on_passing_case_grants_no_credit(self):
        res = scoreadjust.adjusted_score(
            RAW_ROWS, [{"case": "picosoc-uart-div",
                        "raw": "trace run timed out after 300s"}])
        self.assertEqual(res["score"], 6, res)
        self.assertEqual(res["deductions"], [])

    def test_bad_input_raises(self):
        with self.assertRaises(ValueError):
            scoreadjust.adjusted_score("not-a-list", [])
        with self.assertRaises(ValueError):
            scoreadjust.adjusted_score([{"case": "x"}], [])
        with self.assertRaises(ValueError):
            scoreadjust.adjusted_score(RAW_ROWS, "not-a-list")


if __name__ == "__main__":
    unittest.main()
