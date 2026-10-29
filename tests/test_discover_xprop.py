"""Task A8 (Reset/X-prop hunter) red-first tests: agent/discover/xprop.py.

- Forces X on chosen regs at reset release in sim, reports X reaching
  committed state (tohost / mem_valid / TB check outputs).
- Clean design (X injected but none reaches commit) -> clean per target;
  trace with X at a commit signal -> leak with cycle + signal evidence.
- Empty trace raises; build failure raises; sim errors raise loudly.
- An inconclusive run (no X injected AND no leak) is reported
  inconclusive, NEVER clean.

Run: python3 tests/test_discover_xprop.py -v   (from repo root)
"""
import hashlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import xprop  # noqa: E402  (RED before implementation: ModuleNotFoundError)


CLEAN_TRACE = """\
- tb_picorv32_fetch seed 42
- 256-word NOP mem, checking fetch alignment + trap + progress
dbg: shadow_reg=0xxxxxxx (X forced, contained in debug only)
mem_valid=1 mem_la_read=1
tohost: PASS
CYCLES=2009
"""

LEAK_TRACE = """\
- tb_picorv32_fetch seed 42
- 256-word NOP mem, checking fetch alignment + trap + progress
@120 mem_valid=x mem_addr=0xxxxxxxx
tohost: FAIL 0xdead
TRAP: fetch misalign addr low bits nonzero
CYCLES=2009
"""

NO_X_TRACE = """\
- tb_picorv32_fetch seed 42
- 256-word NOP mem, checking fetch alignment + trap + progress
mem_valid=1 mem_la_read=1
tohost: PASS
CYCLES=2009
"""


def _fake_build_ok(tree, case, target):
    return "/tmp/fakebin-Vtb"


def _fake_run_clean(binary, target, seed):
    assert binary == "/tmp/fakebin-Vtb"
    return CLEAN_TRACE


def _fake_run_leak(binary, target, seed):
    return LEAK_TRACE


def _fake_run_no_x(binary, target, seed):
    return NO_X_TRACE


class TestLineHasX(unittest.TestCase):
    def test_hex_with_x_matches(self):
        self.assertTrue(xprop.line_has_x("mem_addr=0xxxxxxxx"))
        self.assertTrue(xprop.line_has_x("data=0xdeadx00f"))
        self.assertTrue(xprop.line_has_x("TRAP: addr=xxxxxxxx"))

    def test_bare_x_matches(self):
        self.assertTrue(xprop.line_has_x("@120 mem_valid=x mem_addr=1"))
        self.assertTrue(xprop.line_has_x("mem_valid=X"))

    def test_verilog_literal_matches(self):
        self.assertTrue(xprop.line_has_x("force cpu.reg_op1 = 32'hx;"))

    def test_normal_hex_does_not_match(self):
        self.assertFalse(xprop.line_has_x("max_addr=0x00000040"))
        self.assertFalse(xprop.line_has_x("tohost: FAIL 0xdead"))

    def test_signal_names_with_x_do_not_match(self):
        self.assertFalse(xprop.line_has_x("mem_la_firstword_xfer ok"))
        self.assertFalse(xprop.line_has_x("max_addr=0x00000000"))
        self.assertFalse(xprop.line_has_x("- tb_picorv32_fetch seed 42"))


class TestScanLogForCommitX(unittest.TestCase):
    def test_leak_trace_yields_mem_valid_evidence(self):
        ev = xprop.scan_log_for_commit_x(LEAK_TRACE)
        self.assertTrue(ev)
        sigs = [e["signal"] for e in ev]
        self.assertIn("mem_valid", sigs)
        mem_ev = [e for e in ev if e["signal"] == "mem_valid"][0]
        self.assertIn("x", mem_ev["line"])
        self.assertEqual(mem_ev["cycle"], 120)
        self.assertEqual(mem_ev["lineno"], 3)

    def test_clean_trace_yields_no_evidence(self):
        self.assertEqual(xprop.scan_log_for_commit_x(CLEAN_TRACE), [])

    def test_no_x_trace_yields_no_evidence(self):
        self.assertEqual(xprop.scan_log_for_commit_x(NO_X_TRACE), [])

    def test_tohost_line_with_x_is_evidence(self):
        trace = "tohost: PASSX? no\ntohost: 0xxxxxxx\nCYCLES=5\n"
        ev = xprop.scan_log_for_commit_x(trace)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["signal"], "tohost")


class TestHuntClean(unittest.TestCase):
    def test_x_contained_off_commit_is_clean(self):
        report = xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                            ["reg_op1"],
                            build_fn=_fake_build_ok, run_fn=_fake_run_clean)
        self.assertEqual(len(report["results"]), 1)
        res = report["results"][0]
        self.assertEqual(res["target"], "reg_op1")
        self.assertEqual(res["status"], "clean")
        self.assertTrue(res["x_injected"])
        self.assertEqual(res["evidence"], [])
        self.assertTrue(report["all_clean"])
        self.assertEqual(report["n_clean"], 1)


class TestHuntLeak(unittest.TestCase):
    def test_x_at_commit_is_leak_with_evidence(self):
        report = xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                            ["reg_op1"],
                            build_fn=_fake_build_ok, run_fn=_fake_run_leak)
        res = report["results"][0]
        self.assertEqual(res["status"], "leak")
        self.assertTrue(res["x_injected"])
        self.assertTrue(res["evidence"])
        sigs = [e["signal"] for e in res["evidence"]]
        self.assertIn("mem_valid", sigs)
        self.assertFalse(report["all_clean"])
        self.assertEqual(report["n_leak"], 1)

    def test_mixed_targets_reported_per_target(self):
        def run(binary, target, seed):
            return LEAK_TRACE if target == "bad_reg" else CLEAN_TRACE
        report = xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                            ["bad_reg", "good_reg"],
                            build_fn=_fake_build_ok, run_fn=run)
        by_t = {r["target"]: r for r in report["results"]}
        self.assertEqual(by_t["bad_reg"]["status"], "leak")
        self.assertEqual(by_t["good_reg"]["status"], "clean")
        self.assertFalse(report["all_clean"])


class TestHuntLoudFailures(unittest.TestCase):
    def test_empty_trace_raises(self):
        with self.assertRaises(RuntimeError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                       ["r1"], build_fn=_fake_build_ok,
                       run_fn=lambda b, t, s: "")
        with self.assertRaises(RuntimeError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                       ["r1"], build_fn=_fake_build_ok,
                       run_fn=lambda b, t, s: "   \n  \n")

    def test_build_failure_raises(self):
        def boom(tree, case, target):
            raise RuntimeError("verilator build failed (exit 1)")
        with self.assertRaises(RuntimeError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                       ["r1"], build_fn=boom, run_fn=_fake_run_clean)

    def test_run_error_raises_loudly(self):
        def boom(binary, target, seed):
            raise RuntimeError("sim timeout after 300s")
        with self.assertRaises(RuntimeError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                       ["r1"], build_fn=_fake_build_ok, run_fn=boom)

    def test_non_string_log_raises(self):
        with self.assertRaises(TypeError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                       ["r1"], build_fn=_fake_build_ok,
                       run_fn=lambda b, t, s: None)


class TestHuntInconclusiveNeverClean(unittest.TestCase):
    def test_no_x_anywhere_is_inconclusive(self):
        report = xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                            ["reg_op1"],
                            build_fn=_fake_build_ok, run_fn=_fake_run_no_x)
        res = report["results"][0]
        self.assertEqual(res["status"], "inconclusive")
        self.assertFalse(res["x_injected"])
        self.assertNotEqual(res["status"], "clean")
        self.assertFalse(report["all_clean"])
        self.assertEqual(report["n_inconclusive"], 1)
        self.assertIn("reason", res)
        self.assertTrue(res["reason"])


class TestHuntValidation(unittest.TestCase):
    def test_empty_targets_raises(self):
        with self.assertRaises(ValueError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]}, [],
                       build_fn=_fake_build_ok, run_fn=_fake_run_clean)

    def test_blank_target_raises(self):
        with self.assertRaises(ValueError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                       ["  "], build_fn=_fake_build_ok,
                       run_fn=_fake_run_clean)

    def test_duplicate_targets_raise(self):
        with self.assertRaises(ValueError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                       ["r1", "r1"], build_fn=_fake_build_ok,
                       run_fn=_fake_run_clean)

    def test_non_list_targets_raises(self):
        with self.assertRaises(ValueError):
            xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                       "reg_op1", build_fn=_fake_build_ok,
                       run_fn=_fake_run_clean)


class TestScratchNeverPristine(unittest.TestCase):
    def test_build_with_x_leaves_pristine_untouched(self):
        import tempfile
        pristine = tempfile.mkdtemp(prefix="xprop-pristine-")
        dut = os.path.join(pristine, "dut.v")
        with open(dut, "w", encoding="utf-8") as fh:
            fh.write("module dut; reg [31:0] reg_op1; endmodule\n")
        with open(dut, "rb") as fh:
            before = hashlib.sha256(fh.read()).hexdigest()
        seen = {}

        def fake_build(tree, case):
            seen["tree"] = tree
            seen["vflags"] = list(case.get("vflags", []))
            self.assertNotEqual(os.path.abspath(tree),
                               os.path.abspath(pristine))
            return os.path.join(tree, "Vtb")

        case = {"name": "c", "files": ["dut.v"], "vflags": []}
        binary = xprop.build_with_x(pristine, case, "reg_op1",
                                    build_fn=fake_build)
        self.assertEqual(binary, os.path.join(seen["tree"], "Vtb"))
        self.assertIn("--x-initial-edge", seen["vflags"])
        # Caller case dict never mutated.
        self.assertEqual(case["vflags"], [])
        # Pristine file byte-identical.
        with open(dut, "rb") as fh:
            after = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(before, after)

    def test_build_with_x_missing_target_file_raises(self):
        import tempfile
        pristine = tempfile.mkdtemp(prefix="xprop-pristine-")
        with self.assertRaises((FileNotFoundError, ValueError)):
            xprop.build_with_x(pristine, {"name": "c", "files": ["nope.v"]},
                               "r1", build_fn=lambda t, c: "bin")


class TestReportWriter(unittest.TestCase):
    def test_report_names_target_and_status_no_verdicts(self):
        report = xprop.hunt("/pristine", {"name": "c", "files": ["f.v"]},
                            ["reg_op1"],
                            build_fn=_fake_build_ok, run_fn=_fake_run_leak)
        path = "/tmp/xprop-test-report.md"
        try:
            out = xprop.write_discovery_report(path, "picorv32-fetch",
                                               report)
            self.assertEqual(out, path)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("reg_op1", text)
            self.assertIn("leak", text)
            self.assertIn("mem_valid", text)
            # Discovery reports NEVER carry verdicts.
            self.assertNotIn("verdict", text.lower())
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
