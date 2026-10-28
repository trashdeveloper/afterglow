"""Tests for eval/mine_pairs.py (TDD, red-first).

mine_pairs.py draws PAIRS of kept-RED single mutants from
eval/mutant_pilot/board.json into sanctioned dual-fault specs
(sim/targets/mut-pair-NN.yaml). Rules under test:

- same-file pairs only (one parent TB covers both faults);
- lines within 2 of each other are rejected (fault-masking risk);
- identical anchor text at different lines is rejected (the
  mut-pilot-02-class double-occurrence trap);
- joint RED is proven per pair with the FROZEN
  agent/loop.py prepare_mutant_tree + vbackend build+run, never by
  trusting the single-RED status; jointly-GREEN pairs are discarded
  loudly with a reason;
- emitted specs carry exactly the committed mut-pair-01.yaml fields;
- the draw is deterministic under --seed;
- the miner makes no network/API calls (static scan, like mutate.py).

No Verilator builds here: build/run are mocked, prepare_mutant_tree is
REAL (tiny fixture trees). Run: python3 -m unittest tests.test_mine_pairs -v
"""

import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import mine_pairs  # noqa: E402  (red-first: missing until GREEN)
from agent import loop  # noqa: E402  (frozen; used, not modified)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = os.path.join(ROOT, "sim", "targets")
BOARD = os.path.join(ROOT, "eval", "mutant_pilot", "board.json")

REL_UART = "picosoc/simpleuart.v"
REL_CPU = "picorv32.v"


def _kept(mid, rel, line, before, after="__BUG__",
          parent_case="picosoc-uart-div", op="num_bump"):
    return {
        "mutant_id": mid, "file": rel, "line": line, "op": op,
        "before": before, "after": after, "parent_case": parent_case,
        "parent": {
            "project": "picorv32", "files": [rel],
            "top": "tb_picosoc_uart", "tb": "picosoc-uart-div_tb.sv",
            "gate": "g", "oracle_pass": "tohost: PASS",
            "oracle_trap": "TRAP:", "vflags": ["-Wno-WIDTH"],
        },
    }


def _tiny_tree(tmp):
    """8-line pristine tree; faults at lines 4 and 7 (>2 apart)."""
    src = os.path.join(tmp, "pristine")
    os.makedirs(os.path.join(src, "picosoc"))
    lines = [
        "module simpleuart(input clk);",  # 1
        "reg [3:0] recv_state;",          # 2
        "reg [7:0] send_pattern;",        # 3
        "recv_state <= 0;",               # 4 fault A
        "recv_divcnt <= recv_divcnt + 1;",  # 5
        "wire x = 1'b0;",                 # 6
        "send_pattern <= ~0;",            # 7 fault B
        "endmodule",                      # 8
    ]
    with open(os.path.join(src, REL_UART), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return src


def _top_level_keys(path):
    """Flat-scan top-level `key:` lines (faults entries are indented)."""
    keys = set()
    with open(path, encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            if not raw or raw.startswith("#") or raw.startswith((" ", "\t")):
                continue
            if ":" in raw:
                keys.add(raw.split(":", 1)[0].strip())
    return keys


class TestCandidatePairs(unittest.TestCase):
    def test_cross_file_pairs_never_drawn(self):
        kept = [
            _kept("a", REL_UART, 39, "reg [3:0] recv_state;"),
            _kept("b", REL_CPU, 376, "wire mem_done = x;"),
        ]
        self.assertEqual(mine_pairs.candidate_pairs(kept), [])

    def test_same_file_pairs_drawn_in_sorted_order(self):
        kept = [
            _kept("m2", REL_UART, 114, "send_pattern <= ~0;"),
            _kept("m1", REL_UART, 39, "reg [3:0] recv_state;"),
            _kept("m3", REL_UART, 200, "wire z = 0;"),
        ]
        pairs = mine_pairs.candidate_pairs(kept)
        self.assertEqual(len(pairs), 3)
        got = [tuple(sorted([a["mutant_id"], b["mutant_id"]]))
               for (a, b) in pairs]
        self.assertEqual(got, [("m1", "m2"), ("m1", "m3"), ("m2", "m3")])

    def test_plan_is_deterministic_under_seed(self):
        kept = [_kept("m%d" % i, REL_UART, 10 + i * 10,
                      "wire w%d = 0;" % i) for i in range(6)]
        first = mine_pairs.plan_pairs(kept, seed=1234)
        second = mine_pairs.plan_pairs(kept, seed=1234)
        self.assertEqual(
            [[a["mutant_id"], b["mutant_id"]] for (a, b) in first[0]],
            [[a["mutant_id"], b["mutant_id"]] for (a, b) in second[0]])
        third = mine_pairs.plan_pairs(kept, seed=999)
        self.assertEqual(
            sorted([tuple(sorted([a["mutant_id"], b["mutant_id"]]))
                    for (a, b) in third[0]]),
            sorted([tuple(sorted([a["mutant_id"], b["mutant_id"]]))
                    for (a, b) in first[0]]))


class TestRejections(unittest.TestCase):
    def test_too_close_pairs_rejected(self):
        a = _kept("a", REL_UART, 100, "wire x = 0;")
        b = _kept("b", REL_UART, 101, "wire y = 0;")
        c = _kept("c", REL_UART, 102, "wire z = 0;")
        self.assertEqual(mine_pairs.classify_pair(a, b), "too_close")
        self.assertEqual(mine_pairs.classify_pair(a, c), "too_close")
        kept = [a, b, _kept("d", REL_UART, 300, "wire w = 0;")]
        _order, accounting = mine_pairs.plan_pairs(kept, seed=1)
        self.assertGreaterEqual(accounting["discarded_too_close"], 1)

    def test_lines_more_than_two_apart_accepted(self):
        a = _kept("a", REL_UART, 100, "wire x = 0;")
        b = _kept("b", REL_UART, 103, "wire y = 0;")
        self.assertIsNone(mine_pairs.classify_pair(a, b))

    def test_dup_anchor_pairs_rejected(self):
        # mut-pilot-02 class: "recv_state <= 0;" lives at lines 68 AND 93.
        a = _kept("a", REL_UART, 68, "recv_state <= 0;")
        b = _kept("b", REL_UART, 93, "recv_state <= 0;")
        self.assertEqual(mine_pairs.classify_pair(a, b), "dup_anchor")
        kept = [a, b, _kept("d", REL_UART, 300, "wire w = 0;")]
        _order, accounting = mine_pairs.plan_pairs(kept, seed=1)
        self.assertGreaterEqual(accounting["discarded_dup_anchor"], 1)

    def test_same_anchor_same_line_is_too_close_not_dup(self):
        a = _kept("a", REL_UART, 376, "wire m = x || y;")
        b = _kept("b", REL_UART, 376, "wire m = x || y;")
        self.assertEqual(mine_pairs.classify_pair(a, b), "too_close")

    def test_cross_parent_pairs_rejected(self):
        # Same file, different TBs: one TB would not exercise both faults.
        a = _kept("a", REL_UART, 100, "wire x = 0;",
                  parent_case="picosoc-uart-div")
        b = _kept("b", REL_UART, 200, "wire y = 0;",
                  parent_case="picorv32-fetch-misalign")
        self.assertEqual(mine_pairs.classify_pair(a, b), "cross_parent")
        kept = [a, b]
        _order, accounting = mine_pairs.plan_pairs(kept, seed=1)
        self.assertEqual(accounting["discarded_cross_parent"], 1)

    def test_write_pair_spec_refuses_overwrite(self):
        import tempfile
        import os
        a = _kept("a", REL_UART, 100, "wire x = 0;", "wire x = 1;")
        b = _kept("b", REL_UART, 200, "wire y = 0;", "wire y = 1;")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mut-pair-01.yaml")
            mine_pairs.write_pair_spec("mut-pair-01", a, b, 1, path)
            with self.assertRaises(SystemExit):
                mine_pairs.write_pair_spec("mut-pair-01", a, b, 1, path)


class TestJointVerification(unittest.TestCase):
    def _pair(self):
        a = _kept("a", REL_UART, 4, "recv_state <= 0;",
                  "recv_state <= 1;")
        b = _kept("b", REL_UART, 7, "send_pattern <= ~0;",
                  "send_pattern <= ~1;")
        return a, b

    def test_joint_green_discarded_loudly_with_reason(self):
        a, b = self._pair()
        case = mine_pairs.make_pair_case("mut-pair-99", a, b, seed=1234)
        self.assertTrue(loop.is_mutant_case(case))
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            dest = os.path.join(tmp, "work", "mut-pair-99")
            parent = a["parent"]
            with mock.patch.object(
                    mine_pairs.vbackend, "build",
                    return_value="/tmp/fake_bin") as mb:
                with mock.patch.object(
                        mine_pairs.vbackend, "sh",
                        return_value=(0, "tohost: PASS\nCYCLES=10\n")) as ms:
                    status, rec = mine_pairs.verify_pair(
                        case, parent, src, dest, sim_seed=42)
            mb.assert_called_once()
            ms.assert_called_once()
            # The build parent carries a case name (vbackend.build needs
            # it for its obj dir; board parent rows have none).
            self.assertEqual(mb.call_args[0][1]["name"], "mut-pair-99")
            # REAL prepare_mutant_tree ran: both faults are in the tree.
            with open(os.path.join(dest, REL_UART)) as fh:
                got = fh.read().splitlines()
            self.assertEqual(got[3], "recv_state <= 1;")
            self.assertEqual(got[6], "send_pattern <= ~1;")
        self.assertEqual(status, "joint_green")
        self.assertIn("reason", rec)
        self.assertTrue(rec["reason"].strip())

    def test_joint_red_returns_oracle_evidence(self):
        a, b = self._pair()
        case = mine_pairs.make_pair_case("mut-pair-99", a, b, seed=1234)
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            dest = os.path.join(tmp, "work", "mut-pair-99")
            with mock.patch.object(mine_pairs.vbackend, "build",
                                   return_value="/tmp/fake_bin"):
                with mock.patch.object(
                        mine_pairs.vbackend, "sh",
                        return_value=(0, "tohost: FAIL 0xdead\n"
                                         "TRAP: stuck\nCYCLES=9\n")):
                    status, rec = mine_pairs.verify_pair(
                        case, a["parent"], src, dest, sim_seed=42)
        self.assertEqual(status, "joint_red")
        self.assertIn("FAIL", str(rec.get("oracle_lines")))


class TestSpecFormat(unittest.TestCase):
    def test_emitted_spec_matches_pair01_fields(self):
        a = _kept("m1", REL_UART, 39, "reg [3:0] recv_state;",
                  "reg [3:1] recv_state;")
        b = _kept("m2", REL_UART, 114, "send_pattern <= ~0;",
                  "send_pattern <= ~1;")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mut-pair-06.yaml")
            mine_pairs.write_pair_spec("mut-pair-06", a, b, 1234, path)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(
                text.startswith("# SANCTIONED dual-fault pilot spec "
                                "mut-pair-06"),
                text.splitlines()[0])
            want = _top_level_keys(os.path.join(TARGETS, "mut-pair-01.yaml"))
            got = _top_level_keys(path)
            self.assertEqual(got, want)
            for token in ('mutant_op: "pair"', 'faults:',
                          'parent_case: "picosoc-uart-div"',
                          "line: 39", "line: 114"):
                self.assertIn(token, text)

    def test_emitted_spec_fires_through_frozen_prepare(self):
        a = _kept("m1", REL_UART, 4, "recv_state <= 0;",
                  "recv_state <= 1;")
        b = _kept("m2", REL_UART, 7, "send_pattern <= ~0;",
                  "send_pattern <= ~1;")
        case = mine_pairs.make_pair_case("mut-pair-06", a, b, seed=1234)
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            dest = os.path.join(tmp, "work", "mut-pair-06")
            record = loop.prepare_mutant_tree(case, src, dest)
            self.assertEqual(len(record["faults"]), 2)


class TestNoLiveCalls(unittest.TestCase):
    def test_miner_makes_no_network_calls(self):
        path = os.path.join(ROOT, "eval", "mine_pairs.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("urllib", "requests", "API_KEY", "http.client",
                       "socket"):
            self.assertIsNone(
                re.search(r"\b%s\b" % re.escape(banned), src),
                "mine_pairs.py must not touch the network (%s)" % banned)


if __name__ == "__main__":
    unittest.main()
