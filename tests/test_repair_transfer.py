#!/usr/bin/env python3
"""Task C7 (cross-fault transfer, plan item 27, Wave C FIX BETTER) tests:
agent/repair/transfer.py.

Index fixed diffs by signal names; on a new fault with the same
signals, the known prior fix is tried first (top match first).

  extract_signals(text) -> [signal names, encounter order, deduped]
  build_index(entries) -> {"entries": [...], "by_signal": {...}}
  suggest(fault, index, top_n=1) -> [patches, best overlap first]

``entries`` is a non-empty list/tuple of ``{"signals": ..., "patch":
...}`` dicts (``signals`` is free text or a signal-name list;
``patch`` is a non-empty diff string). ``fault`` is free text or a
signal-name list. Scoring is signal-overlap count (higher wins);
ties break by insertion order. Zero overlap -> [] (never invent a
fix). Only the first ``top_n`` patches are returned.

Contract:
- Fixture: two stored fixes with disjoint signals; a new fault
  naming fix-A signals retrieves fix A first (and vice versa).
- Top match first: ordering is by descending overlap, not input luck.
- Unknown signals -> [] (a miss, not a guess).
- Empty entries / missing patch / bad fault / bad top_n ->
  ValueError/TypeError (refuse to fabricate a ranking).
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no transfer wiring).

RED-first TDD: this file was written BEFORE agent/repair/transfer.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_transfer.py -v   (from repo root)
"""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import transfer  # noqa: E402  (RED before implementation)


UART_DIFF = "\n".join([
    "diff --git a/uart.v b/uart.v",
    "--- a/uart.v",
    "+++ b/uart.v",
    "@@ -1,2 +1,2 @@",
    "-wire baud_tick = (baud_cnt == 16'd433);",
    "+wire baud_tick = (baud_cnt == 16'd434);",
]) + "\n"

FETCH_DIFF = "\n".join([
    "diff --git a/picorv32.v b/picorv32.v",
    "--- a/picorv32.v",
    "+++ b/picorv32.v",
    "@@ -1,2 +1,2 @@",
    "-wire fetch_ok = mem_valid && (pc_addr[1:0] == 2'b00);",
    "+wire fetch_ok = mem_valid && (pc_addr[1:0] != 2'b11);",
]) + "\n"


def _index_two():
    return transfer.build_index([
        {"signals": ["baud_cnt", "baud_tick", "tx_busy"],
         "patch": UART_DIFF},
        {"signals": ["pc_addr", "mem_valid", "fetch_ok"],
         "patch": FETCH_DIFF},
    ])


class TestExtractSignals(unittest.TestCase):
    def test_finds_signal_names_in_order_deduped(self):
        sigs = transfer.extract_signals(
            "TRAP: baud_cnt stuck; baud_tick missing, tx_busy=1 "
            "baud_cnt again")
        self.assertEqual(sigs, ["baud_cnt", "baud_tick", "tx_busy"])

    def test_ignores_verilog_keywords_and_noise(self):
        sigs = transfer.extract_signals(
            "always @(posedge clk) if (baud_cnt == 1) begin end")
        self.assertIn("baud_cnt", sigs)
        for kw in ("always", "posedge", "if", "begin", "end"):
            self.assertNotIn(kw, sigs)

    def test_empty_text_has_no_signals(self):
        self.assertEqual(transfer.extract_signals(""), [])
        self.assertEqual(transfer.extract_signals("   "), [])

    def test_non_string_raises(self):
        for bad in (None, 123, ["baud_cnt"], {"s": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    transfer.extract_signals(bad)


class TestBuildIndex(unittest.TestCase):
    def test_indexes_fixes_by_signal_name(self):
        idx = _index_two()
        self.assertIn("baud_cnt", idx["by_signal"])
        self.assertIn("pc_addr", idx["by_signal"])
        self.assertEqual(idx["by_signal"]["baud_cnt"], [0])
        self.assertEqual(idx["by_signal"]["pc_addr"], [1])
        self.assertEqual(len(idx["entries"]), 2)

    def test_accepts_free_text_signals(self):
        idx = transfer.build_index([
            {"signals": "baud_cnt stuck, tx_busy held", "patch": UART_DIFF},
        ])
        self.assertIn("baud_cnt", idx["by_signal"])
        self.assertIn("tx_busy", idx["by_signal"])

    def test_empty_entries_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            transfer.build_index([])
        with self.assertRaises((TypeError, ValueError)):
            transfer.build_index(None)

    def test_missing_or_empty_patch_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            transfer.build_index([{"signals": ["a_sig"], "patch": ""}])
        with self.assertRaises((TypeError, ValueError)):
            transfer.build_index([{"signals": ["a_sig"]}])


class TestSuggest(unittest.TestCase):
    def test_new_same_signal_fault_retrieves_known_prior_fix(self):
        idx = _index_two()
        got = transfer.suggest(
            "tohost FAIL: baud_cnt stuck, baud_tick missing, tx_busy held",
            idx)
        self.assertEqual(got, [UART_DIFF])

    def test_other_signal_fault_retrieves_other_fix(self):
        idx = _index_two()
        got = transfer.suggest("TRAP pc_addr misalign mem_valid drop", idx)
        self.assertEqual(got, [FETCH_DIFF])

    def test_accepts_signal_list_fault(self):
        idx = _index_two()
        got = transfer.suggest(["pc_addr", "mem_valid"], idx)
        self.assertEqual(got, [FETCH_DIFF])

    def test_top_match_first_orders_by_overlap(self):
        idx = transfer.build_index([
            {"signals": ["baud_cnt"], "patch": UART_DIFF},
            {"signals": ["baud_cnt", "tx_busy", "baud_tick"],
             "patch": FETCH_DIFF},
        ])
        got = transfer.suggest(["baud_cnt", "tx_busy", "baud_tick"], idx,
                               top_n=2)
        self.assertEqual(got, [FETCH_DIFF, UART_DIFF])

    def test_unknown_signals_return_empty_miss(self):
        idx = _index_two()
        self.assertEqual(
            transfer.suggest("zz_qq_top_wire_xx yy_zz_unknown", idx), [])

    def test_top_n_caps_results(self):
        idx = _index_two()
        got = transfer.suggest(["baud_cnt", "pc_addr"], idx, top_n=1)
        self.assertEqual(len(got), 1)

    def test_bad_fault_and_top_n_raise(self):
        idx = _index_two()
        for bad in (None, 123, [], [""]):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    transfer.suggest(bad, idx)
        for bad_n in (0, -1, 99, "1"):
            with self.subTest(bad_n=bad_n):
                with self.assertRaises((TypeError, ValueError)):
                    transfer.suggest(["baud_cnt"], idx, top_n=bad_n)
        with self.assertRaises((TypeError, ValueError)):
            transfer.suggest(["baud_cnt"], None)


class TestUntouchedLoopGuard(unittest.TestCase):
    def test_loop_and_guard_do_not_wire_transfer(self):
        # C7 is additive + default-off: loop.py / guard.py must not
        # import the repair package (existing behavior byte-identical).
        for rel in (os.path.join("agent", "loop.py"),
                    os.path.join("agent", "guard.py")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(
                re.search(r"^\s*(import|from)\s+\S*(repair|transfer)",
                          src, re.M),
                "%s wires transfer" % rel)


class TestStdlibOnly(unittest.TestCase):
    def test_module_is_stdlib_only(self):
        # No sockets, no HTTP, no API clients: pure mechanical check.
        with open(os.path.join(ROOT, "agent", "repair", "transfer.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        allowed = {"re"}
        forbidden = imported - allowed
        self.assertEqual(forbidden, set(),
                         "transfer may import only stdlib 're', got: %s"
                         % sorted(imported))


if __name__ == "__main__":
    unittest.main()
