#!/usr/bin/env python3
"""Task H4 (cross-design transfer, plan item 84) tests:
agent/repair/xferdesign.py.

Interface (plan text, verbatim):

  transfer_fix(picorv32_fix, darkriscv_fault) -> patch|None
    without any model call.

Consumes agent/repair/transfer.py index: the picorv32 fix is indexed
by signal names; a same-class fault on the second design (darkriscv)
retrieves the known prior fix. Zero overlap -> None (a miss, not a
guess). No model is ever called (an optional model hook, when passed,
is ignored; a counting model asserts zero calls).

Contract:
- Fixture: picorv32 fetch fix (pc_addr/mem_valid/fetch_ok); a
  same-class darkriscv fetch fault greens with the picorv32 patch and
  zero model calls (counter asserted).
- Different-class darkriscv fault (uart baud signals) -> None, still
  zero model calls.
- Unknown signals -> None (a miss, not a guess).
- Bad fix / bad fault propagate ValueError/TypeError loudly (refuse
  to fabricate a patch); never a fake diff over errors.
- Pure stdlib + repo import (agent.repair.transfer); no API, no keys.
  Default-off: explicit call only; agent/loop.py and agent/guard.py
  untouched (no xferdesign wiring).

RED-first TDD: this file was written BEFORE
agent/repair/xferdesign.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_xferdesign.py -v   (from repo root)
"""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import xferdesign  # noqa: E402  (RED before implementation)


PICORV32_FETCH_DIFF = "\n".join([
    "diff --git a/picorv32.v b/picorv32.v",
    "--- a/picorv32.v",
    "+++ b/picorv32.v",
    "@@ -1,2 +1,2 @@",
    "-wire fetch_ok = mem_valid && (pc_addr[1:0] == 2'b00);",
    "+wire fetch_ok = mem_valid && (pc_addr[1:0] != 2'b11);",
]) + "\n"

PICORV32_FETCH_FIX = {
    "signals": ["pc_addr", "mem_valid", "fetch_ok"],
    "patch": PICORV32_FETCH_DIFF,
}

# Same-class fault on the SECOND design (darkriscv): fetch window
# fault naming the picorv32 fix signals.
DARKRISCV_FETCH_FAULT = (
    "TRAP darkriscv fetch: pc_addr misalign, mem_valid drop, "
    "fetch_ok low"
)

# Different-class fault on the second design: uart baud signals the
# picorv32 fetch fix knows nothing about.
DARKRISCV_UART_FAULT = "tohost FAIL: baud_cnt stuck, baud_tick missing"


def _counting_model(calls):
    def _model(prompt):
        calls.append(prompt)
        return "fake-model-patch"
    return _model


class TestCrossDesignTransfer(unittest.TestCase):
    def test_same_class_second_design_fault_greens(self):
        got = xferdesign.transfer_fix(
            PICORV32_FETCH_FIX, DARKRISCV_FETCH_FAULT)
        self.assertEqual(got, PICORV32_FETCH_DIFF)

    def test_same_class_transfer_makes_zero_model_calls(self):
        calls = []
        got = xferdesign.transfer_fix(
            PICORV32_FETCH_FIX, DARKRISCV_FETCH_FAULT,
            model=_counting_model(calls))
        self.assertEqual(got, PICORV32_FETCH_DIFF)
        self.assertEqual(calls, [])

    def test_accepts_signal_list_fault(self):
        got = xferdesign.transfer_fix(
            PICORV32_FETCH_FIX, ["pc_addr", "mem_valid"])
        self.assertEqual(got, PICORV32_FETCH_DIFF)

    def test_accepts_pair_form_fix(self):
        got = xferdesign.transfer_fix(
            (["pc_addr", "mem_valid", "fetch_ok"], PICORV32_FETCH_DIFF),
            DARKRISCV_FETCH_FAULT)
        self.assertEqual(got, PICORV32_FETCH_DIFF)

    def test_different_class_fault_returns_none(self):
        calls = []
        got = xferdesign.transfer_fix(
            PICORV32_FETCH_FIX, DARKRISCV_UART_FAULT,
            model=_counting_model(calls))
        self.assertIsNone(got)
        self.assertEqual(calls, [])

    def test_unknown_signals_return_none_miss(self):
        self.assertIsNone(xferdesign.transfer_fix(
            PICORV32_FETCH_FIX, "zz_qq_top_wire_xx yy_zz_unknown"))

    def test_bad_fix_raises_loudly(self):
        for bad_fix in (None, 123, {}, {"signals": ["pc_addr"]},
                        {"signals": ["pc_addr"], "patch": ""},
                        {"signals": [], "patch": PICORV32_FETCH_DIFF}):
            with self.subTest(bad_fix=bad_fix):
                with self.assertRaises((TypeError, ValueError)):
                    xferdesign.transfer_fix(bad_fix, DARKRISCV_FETCH_FAULT)

    def test_bad_fault_raises_loudly(self):
        for bad_fault in (None, 123, "", [], [""]):
            with self.subTest(bad_fault=bad_fault):
                with self.assertRaises((TypeError, ValueError)):
                    xferdesign.transfer_fix(PICORV32_FETCH_FIX, bad_fault)


class TestUntouchedLoopGuard(unittest.TestCase):
    def test_loop_and_guard_do_not_wire_xferdesign(self):
        # H4 is additive + default-off: loop.py / guard.py must not
        # import the repair package (existing behavior byte-identical).
        for rel in (os.path.join("agent", "loop.py"),
                    os.path.join("agent", "guard.py")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(
                re.search(r"^\s*(import|from)\s+\S*(repair|xferdesign)",
                          src, re.M),
                "%s wires xferdesign" % rel)


class TestStdlibOnly(unittest.TestCase):
    def test_module_is_stdlib_plus_transfer_only(self):
        # No sockets, no HTTP, no API clients: only stdlib + the
        # consumed transfer index module (plan interface).
        with open(os.path.join(ROOT, "agent", "repair", "xferdesign.py"),
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
        repo_allowed = {"agent"}
        stdlib_allowed = {"re"}
        forbidden = imported - repo_allowed - stdlib_allowed
        self.assertEqual(forbidden, set(),
                         "xferdesign may import only stdlib + "
                         "agent.repair.transfer, got: %s"
                         % sorted(imported))
        with open(os.path.join(ROOT, "agent", "repair", "xferdesign.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("transfer", src)


if __name__ == "__main__":
    unittest.main()
