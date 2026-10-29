"""Per-bug cost ledger rollup tests (Task D5-TRUE, plan item 35, Wave D).

`agent/trust/bugcost.py` rolls a list of per-run cost records up to
per-bug (case id) totals: attempts, tokens in/out, cost, greens,
time-to-green.

Record format reuses `afterglow_core.cost.compute_run_cost()` keys
(`text_input_tokens`, `text_output_tokens`, `text_tokens`,
`image_calls`, `est_usd`) plus a case id (`case_id`, also accepted as
`case` / `bug_id`), an outcome flag (`green`), and an optional
`time_to_green_s`. The rollup never reprices: it sums `est_usd`
verbatim, so pricing stays owned by `afterglow_core.cost` (no fork).

- test_rollup_arithmetic_pinned: hand-checkable fixture pins every
  total (attempts, in/out/total tokens, image calls, summed USD,
  greens, min time-to-green).
- test_accepts_compute_run_cost_output: a real `compute_run_cost()`
  dict plus case/outcome keys rolls up losslessly (format reuse,
  not prose).
- test_case_key_aliases / test_no_green_time_to_green_none /
  test_missing_numeric_fields_tolerate_zero / test_empty_input:
  grouping + tolerance edges.
- test_rejects_junk: non-list input, non-dict rows, missing case id,
  bad outcome flag, negative time all raise (never invent data).
- test_no_forked_pricing: the module owns no pricing table.

RED-first TDD: this file was written BEFORE agent/trust/bugcost.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_bugcost -v   (from repo root)
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import bugcost  # noqa: E402  (RED before implementation)
from afterglow_core import cost as cost_ledger  # noqa: E402


def _rec(case, tin, tout, total, img, usd, green, ttg=None, key="case_id"):
    rec = {key: case}
    rec.update({
        "text_input_tokens": tin,
        "text_output_tokens": tout,
        "text_tokens": total,
        "image_calls": img,
        "est_usd": usd,
        "green": green,
    })
    if ttg is not None:
        rec["time_to_green_s"] = ttg
    return rec


def _fixture():
    return [
        _rec("uart-tx-div", 1000, 2000, 3000, 1, 0.05, False),
        _rec("uart-tx-div", 500, 1500, 2000, 0, 0.02, True, 120.5),
        _rec("picosoc-uart-div", 2000, 4000, 6000, 1, 0.10, True, 300.0),
        _rec("picosoc-uart-div", 100, 100, 200, 0, 0.001, True, 200.0),
    ]


class TestRollupArithmetic(unittest.TestCase):
    def test_rollup_arithmetic_pinned(self):
        got = bugcost.rollup_by_bug(_fixture())
        self.assertEqual(set(got), {"uart-tx-div", "picosoc-uart-div"})

        uart = got["uart-tx-div"]
        self.assertEqual(uart["case_id"], "uart-tx-div")
        self.assertEqual(uart["attempts"], 2)
        self.assertEqual(uart["text_input_tokens"], 1500)
        self.assertEqual(uart["text_output_tokens"], 3500)
        self.assertEqual(uart["text_tokens"], 5000)
        self.assertEqual(uart["image_calls"], 1)
        # Pinned hand sum: 0.05 + 0.02 = 0.07 (not recomputed pricing).
        self.assertAlmostEqual(uart["est_usd"], 0.07, places=6)
        self.assertEqual(uart["greens"], 1)
        self.assertAlmostEqual(uart["time_to_green_s"], 120.5)

        pico = got["picosoc-uart-div"]
        self.assertEqual(pico["attempts"], 2)
        self.assertEqual(pico["text_input_tokens"], 2100)
        self.assertEqual(pico["text_output_tokens"], 4100)
        self.assertEqual(pico["text_tokens"], 6200)
        self.assertEqual(pico["image_calls"], 1)
        # Pinned hand sum: 0.10 + 0.001 = 0.101.
        self.assertAlmostEqual(pico["est_usd"], 0.101, places=6)
        self.assertEqual(pico["greens"], 2)
        # First-green wins: min(300.0, 200.0) = 200.0.
        self.assertAlmostEqual(pico["time_to_green_s"], 200.0)

    def test_accepts_compute_run_cost_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = os.path.join(tmp, "api")
            os.makedirs(api)
            with open(os.path.join(api, "text_response.json"),
                      "w", encoding="utf-8") as fh:
                json.dump({
                    "model": "muse-spark-1.3-contributor",
                    "usage": {"prompt_tokens": 1457,
                              "completion_tokens": 5940,
                              "total_tokens": 7397},
                }, fh)
            with open(os.path.join(api, "image_response.json"),
                      "w", encoding="utf-8") as fh:
                json.dump({"model": "muse-image-1.0",
                           "request_id": "img-test"}, fh)
            run_cost = cost_ledger.compute_run_cost(tmp)
        run_cost.update({"case_id": "uart-tx-div", "green": True,
                         "time_to_green_s": 61.5})
        got = bugcost.rollup_by_bug([run_cost])
        self.assertEqual(set(got), {"uart-tx-div"})
        row = got["uart-tx-div"]
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["text_input_tokens"], 1457)
        self.assertEqual(row["text_output_tokens"], 5940)
        self.assertEqual(row["text_tokens"], 7397)
        self.assertEqual(row["image_calls"], 1)
        # Lossless: the real ledger value passes through verbatim.
        self.assertEqual(row["est_usd"], run_cost["est_usd"])
        self.assertAlmostEqual(row["est_usd"], 0.080434, places=6)
        self.assertEqual(row["greens"], 1)
        self.assertAlmostEqual(row["time_to_green_s"], 61.5)


class TestGroupingAndTolerance(unittest.TestCase):
    def test_case_key_aliases(self):
        recs = [
            _rec("uart-tx-div", 10, 20, 30, 0, 0.001, False, key="case"),
            _rec("uart-tx-div", 40, 50, 90, 0, 0.002, True, 9.0,
                 key="bug_id"),
        ]
        got = bugcost.rollup_by_bug(recs)
        self.assertEqual(set(got), {"uart-tx-div"})
        self.assertEqual(got["uart-tx-div"]["attempts"], 2)
        self.assertEqual(got["uart-tx-div"]["text_tokens"], 120)
        self.assertEqual(got["uart-tx-div"]["greens"], 1)

    def test_no_green_time_to_green_none(self):
        recs = [_rec("trap-stuck", 100, 200, 300, 0, 0.01, False),
                _rec("trap-stuck", 100, 200, 300, 0, 0.01, False)]
        got = bugcost.rollup_by_bug(recs)
        self.assertEqual(got["trap-stuck"]["greens"], 0)
        self.assertIsNone(got["trap-stuck"]["time_to_green_s"])

    def test_green_without_time_stays_none(self):
        got = bugcost.rollup_by_bug(
            [_rec("pc-incr", 100, 200, 300, 0, 0.01, True)])
        self.assertEqual(got["pc-incr"]["greens"], 1)
        self.assertIsNone(got["pc-incr"]["time_to_green_s"])

    def test_missing_numeric_fields_tolerate_zero(self):
        got = bugcost.rollup_by_bug([{"case_id": "rdata-latch"}])
        row = got["rdata-latch"]
        self.assertEqual(
            (row["attempts"], row["text_input_tokens"],
             row["text_output_tokens"], row["text_tokens"],
             row["image_calls"], row["est_usd"], row["greens"]),
            (1, 0, 0, 0, 0, 0.0, 0))
        self.assertIsNone(row["time_to_green_s"])

    def test_empty_input(self):
        self.assertEqual(bugcost.rollup_by_bug([]), {})


class TestRejectsJunk(unittest.TestCase):
    def test_non_list_input_raises(self):
        for bad in (None, "nope", 42, {"case_id": "x"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bugcost.rollup_by_bug(bad)

    def test_non_dict_row_raises(self):
        for bad in (None, 42, "x", [("case_id", "x")]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bugcost.rollup_by_bug([bad])

    def test_missing_case_id_raises(self):
        for bad in ({}, {"case": ""}, {"case_id": "  "},
                    {"bug_id": None}, {"green": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bugcost.rollup_by_bug([bad])

    def test_bad_outcome_raises(self):
        for bad in ("maybe", 2, -1, 0.5, [True], {"green": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bugcost.rollup_by_bug(
                        [{"case_id": "x", "green": bad}])

    def test_bad_time_to_green_raises(self):
        for bad in (-1.0, -30, "fast", [60.0], True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bugcost.rollup_by_bug(
                        [{"case_id": "x", "green": True,
                          "time_to_green_s": bad}])

    def test_no_forked_pricing(self):
        # Pricing lives in afterglow_core.cost only; the rollup sums
        # est_usd verbatim and must not carry its own rate table.
        self.assertFalse(hasattr(bugcost, "PRICING"))


if __name__ == "__main__":
    unittest.main()
