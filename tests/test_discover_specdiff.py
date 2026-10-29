"""Task A7 (Spec-vs-RTL differ) red-first tests: agent/discover/specdiff.py.

- Takes a small spec excerpt (list of documented behaviors, each with an
  ID + keywords/signals) and checks each behavior has a TB path: the TB +
  DUT sources are searched for the named signals/behaviors and each
  behavior is reported covered (with file:line evidence) or uncovered.
- A behavior with zero hits is reported uncovered, never silently
  dropped; malformed spec entries raise; empty spec raises (never a
  vacuous all-covered).
- Retrieval-only: NO model calls, NO API.

Run: python3 tests/test_discover_specdiff.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from discover import specdiff  # noqa: E402  (RED before implementation: ModuleNotFoundError)


def _fixture_spec():
    return [
        {"id": "TX-BAUD",
         "description": "TX shifts one bit per cfg_divider cycles",
         "signals": ["send_divcnt", "cfg_divider"]},
        {"id": "TX-DONE",
         "description": "send_bitcnt counts the 10 frame bits down",
         "signals": ["send_bitcnt"]},
        {"id": "TX-PARITY",
         "description": "even parity bit appended after data bits",
         "signals": ["parity_bit", "send_parity"]},
    ]


def _fixture_sources():
    dut = (
        "if (send_divcnt > cfg_divider && send_bitcnt) begin\n"
        "  send_pattern <= {1'b1, send_pattern[9:1]};\n"
        "  send_bitcnt <= send_bitcnt - 1;\n"
        "end\n"
    )
    tb = (
        "// loopback TB drives reg_dat_we then waits for RX byte\n"
        "reg_dat_we = 1;\n"
    )
    return {"dut/simpleuart.v": dut, "tb/uart_tb.sv": tb}


class TestFixtureSplitPinned(unittest.TestCase):
    def test_three_behaviors_two_covered_one_uncovered(self):
        report = specdiff.check_spec(_fixture_spec(), _fixture_sources())
        # Pinned 2/3 split.
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["covered"], 2)
        self.assertEqual(report["uncovered"], 1)
        self.assertFalse(report["all_covered"])
        # The uncovered behavior is named, never silently dropped.
        uncovered_ids = [r["id"] for r in report["results"]
                         if r["status"] == "uncovered"]
        self.assertEqual(uncovered_ids, ["TX-PARITY"])
        by_id = {r["id"]: r for r in report["results"]}
        self.assertEqual(by_id["TX-PARITY"]["hits"], [])
        # Covered behaviors carry file:line evidence.
        baud_hits = by_id["TX-BAUD"]["hits"]
        self.assertTrue(baud_hits)
        self.assertIn("dut/simpleuart.v:1", baud_hits)
        done_hits = by_id["TX-DONE"]["hits"]
        self.assertTrue(done_hits)
        self.assertTrue(all(":" in h for h in done_hits))

    def test_covered_result_names_matching_signal(self):
        report = specdiff.check_spec(_fixture_spec(), _fixture_sources())
        by_id = {r["id"]: r for r in report["results"]}
        self.assertIn("send_divcnt", by_id["TX-BAUD"]["matched"])
        self.assertIn("send_bitcnt", by_id["TX-DONE"]["matched"])
        self.assertEqual(by_id["TX-PARITY"]["matched"], [])


class TestMalformedEntriesRaise(unittest.TestCase):
    def test_missing_id_raises(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec([{"signals": ["send_bitcnt"]}],
                                _fixture_sources())

    def test_empty_id_raises(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec([{"id": "  ", "signals": ["send_bitcnt"]}],
                                _fixture_sources())

    def test_missing_signals_raises(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec([{"id": "TX-X", "description": "no signals"}],
                                _fixture_sources())

    def test_empty_signals_raises(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec([{"id": "TX-X", "signals": []}],
                                _fixture_sources())

    def test_blank_signal_raises(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec([{"id": "TX-X", "signals": ["  "]}],
                                _fixture_sources())

    def test_non_dict_entry_raises(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec(["TX-BAUD"], _fixture_sources())

    def test_duplicate_ids_raise(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec(
                [{"id": "TX-A", "signals": ["a_sig"]},
                 {"id": "TX-A", "signals": ["b_sig"]}],
                _fixture_sources())

    def test_empty_spec_raises_never_vacuous_all_covered(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec([], _fixture_sources())

    def test_non_list_spec_raises(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec({"id": "TX-A", "signals": ["a_sig"]},
                                _fixture_sources())


class TestCheckSpecFiles(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises((OSError, IOError)):
            specdiff.check_spec_files(_fixture_spec(),
                                      ["/nonexistent/path_tb.sv"])

    def test_bad_paths_arg_raises(self):
        with self.assertRaises(ValueError):
            specdiff.check_spec_files(_fixture_spec(), [])
        with self.assertRaises(ValueError):
            specdiff.check_spec_files(_fixture_spec(), "not-a-list")


class TestReportWriter(unittest.TestCase):
    def test_write_discovery_report_names_uncovered_id(self):
        report = specdiff.check_spec(_fixture_spec(), _fixture_sources())
        path = "/tmp/specdiff-test-report.md"
        try:
            out = specdiff.write_discovery_report(path, "uart-tx", report)
            self.assertEqual(out, path)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("TX-PARITY", text)
            self.assertIn("2/3", text)
            # Discovery reports NEVER carry verdicts.
            self.assertNotIn("verdict", text.lower())
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
