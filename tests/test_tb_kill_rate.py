"""Per-TB kill-rate report test (wave-4: testbenches as measured oracles).

Runs eval/tb_kill_rate.py (static artifacts only -- no API, no sim, no
build), checks the report SHAPE and arithmetic, never the kill-rate
values: rows reconcile with board.json accounting, the cond_grow blind
spot is called out where true, and regeneration to a temp path is
byte-identical (deterministic generated file).
"""
import os
import re
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TbKillRateReport(unittest.TestCase):
    def test_report_exists_with_parent_tb_rows(self):
        from eval.tb_kill_rate import generate
        summary = generate()
        report = summary["report"]
        self.assertTrue(os.path.isfile(report))
        with open(report, "r", encoding="utf-8") as fh:
            text = fh.read()
        # Both parent TBs (board.json parents mapping) have rows.
        for tb in ("picorv32-fetch_tb.sv", "picosoc-uart-div_tb.sv"):
            self.assertIn(tb, text)
        self.assertIn("kill_rate", text)
        self.assertIn("RECOMMENDATION", text)

    def test_arithmetic_reconciles_with_board_totals(self):
        import json
        from eval.tb_kill_rate import generate
        summary = generate()
        with open(os.path.join(REPO, "eval", "mutant_pilot",
                               "board.json"), encoding="utf-8") as fh:
            board = json.load(fh)
        acc = board["accounting"]
        rows = summary["rows"]
        self.assertEqual(sum(r["drawn"] for r in rows), acc["drawn"])
        self.assertEqual(sum(r["kept"] for r in rows), acc["kept_new"])
        self.assertEqual(sum(r["equivalent"] for r in rows),
                         acc["equivalent"])
        self.assertEqual(sum(r["build_break"] for r in rows),
                         acc["build_break"])
        # Per-row closure: drawn == kept + equivalent + build_break
        # (discarded_other is 0 on this board).
        for r in rows:
            self.assertEqual(r["drawn"],
                             r["kept"] + r["equivalent"] + r["build_break"],
                             r["tb"])
        # kill_rate == kept / (drawn - build_break) per row.
        for r in rows:
            denom = r["drawn"] - r["build_break"]
            self.assertAlmostEqual(r["kill_rate"], r["kept"] / denom,
                                   places=9, msg=r["tb"])

    def test_cond_grow_blind_spot_called_out(self):
        from eval.tb_kill_rate import generate
        summary = generate()
        report = summary["report"]
        with open(report, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("cond_grow", text)
        # Truth: no kept-RED cond_grow mutant exists on this board.
        self.assertEqual(summary["kept_cond_grow_total"], 0)
        for r in summary["rows"]:
            self.assertEqual(r["kept_cond_grow"], 0, r["tb"])

    def test_deterministic_regen_to_temp(self):
        from eval.tb_kill_rate import generate
        summary = generate()
        with tempfile.TemporaryDirectory() as tmp:
            alt = os.path.join(tmp, "tb_kill_rate.md")
            generate(report_path=alt)
            with open(summary["report"], "rb") as fh:
                committed = fh.read()
            with open(alt, "rb") as fh:
                regen = fh.read()
        self.assertEqual(regen, committed,
                         "regen to temp differs -- report not deterministic")


if __name__ == "__main__":
    unittest.main()
