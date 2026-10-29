"""Task B1 (VCD-as-evidence, plan item 11) red-first tests:
agent/evidence/vcd_slice.py.

- EvidencePack extension: enables --trace on a scratch build, slices
  the VCD window around the first FAIL cycle, summarizes top toggling
  signals (ranked by transition count in window).
- Reuses agent/discover/protocol.py's VCD parser (import, no duplicate).
- Default-off: only runs when explicitly requested via parameter; the
  existing loop behavior never changes (no loop edits).
- Sim-only validation; no API calls, no keys.

Run: python3 tests/test_evidence_vcd_slice.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import vcd_slice  # noqa: E402  (RED before implementation)


def _fixture_vcd():
    # tb.clk: free-running clock (toggles every 10 = 1 repo cycle).
    # tb.ser_tx: idle high, one falling edge at t=105 (the INJECTED
    # transition, inside any window around fail t=110), rises at t=195.
    # tb.quiet: single edge at t=10 (far outside the window).
    lines = [
        "$timescale 1ns $end",
        "$scope module tb $end",
        "$var wire 1 ! clk $end",
        "$var wire 1 \" ser_tx $end",
        "$var wire 1 # quiet $end",
        "$upscope $end",
        "$enddefinitions $end",
        "#0",
        "1!",
        '1"',
        "0#",
    ]
    for t in range(10, 200, 10):  # clk toggles t=10..190
        lines += ["#%d" % t, ("0!" if (t // 10) % 2 else "1!")]
    lines += [
        "#10",
        "1#",     # quiet rises once, far outside the window
        "#105",
        '0"',     # INJECTED ser_tx fall inside the window
        "#195",
        '1"',     # ser_tx rises again (still inside a wide window)
        "#300",
    ]
    return "\n".join(lines) + "\n"


FAIL_LOG = "\n".join([
    "- tb_picosoc_uart seed 42",
    "- divider 64 loopback send 0xa5",
    "tohost: FAIL 0xdead",
    "TRAP: uart data mismatch got 0x00 want 0xa5",
    "CYCLES=2209",
]) + "\n"

PASS_LOG = "\n".join([
    "- tb_picosoc_uart seed 42",
    "- divider 64 loopback send 0xa5",
    "tohost: PASS",
    "CYCLES=2209",
]) + "\n"


class TestFindFailTime(unittest.TestCase):
    def test_fail_marker_parses_lineno_and_end_cycle(self):
        found = vcd_slice.find_fail_time(FAIL_LOG)
        self.assertEqual(found["log_lineno"], 4)  # 1-based TRAP line
        self.assertEqual(found["end_cycle"], 2209)
        self.assertIn("TRAP:", found["marker"])

    def test_empty_log_raises_never_silent(self):
        with self.assertRaises(ValueError):
            vcd_slice.find_fail_time("")

    def test_passing_log_with_no_marker_raises_loud(self):
        # A passing log has no FAIL point: refusing to invent one.
        # Callers analyzing a passing end pass fail_time explicitly.
        with self.assertRaises(ValueError):
            vcd_slice.find_fail_time(PASS_LOG)

    def test_per_line_cycle_marker_is_used_when_present(self):
        log = "cycle 100 fetch ok\ncycle 101 TRAP: boom\nCYCLES=500\n"
        found = vcd_slice.find_fail_time(log)
        self.assertEqual(found["fail_cycle"], 101)
        self.assertEqual(found["end_cycle"], 500)


class TestSliceVcd(unittest.TestCase):
    def test_slice_contains_injected_transition(self):
        from discover import protocol
        trace = protocol.parse_vcd(_fixture_vcd())
        sliced = vcd_slice.slice_vcd(trace, fail_time=110,
                                     window_cycles=10,
                                     time_per_cycle=10)
        self.assertEqual((sliced["start"], sliced["end"]), (10, 210))
        tx = sliced["signals"]["tb.ser_tx"]["samples"]
        self.assertIn((105, "0"), tx)
        self.assertIn((195, "1"), tx)
        # Outside-window samples are excluded, never smeared in.
        self.assertFalse(any(t < 10 or t > 210 for (t, _v) in tx))

    def test_window_clamps_at_zero(self):
        from discover import protocol
        trace = protocol.parse_vcd(_fixture_vcd())
        sliced = vcd_slice.slice_vcd(trace, fail_time=5,
                                     window_cycles=10,
                                     time_per_cycle=10)
        self.assertEqual(sliced["start"], 0)
        self.assertEqual(sliced["end"], 105)

    def test_bad_args_raise_loudly(self):
        from discover import protocol
        trace = protocol.parse_vcd(_fixture_vcd())
        with self.assertRaises(ValueError):
            vcd_slice.slice_vcd(trace, fail_time=-1)
        with self.assertRaises(ValueError):
            vcd_slice.slice_vcd(trace, fail_time=110, window_cycles=0)
        with self.assertRaises(ValueError):
            vcd_slice.slice_vcd({"signals": {}, "samples": {}},
                                fail_time=110)


class TestRankToggles(unittest.TestCase):
    def test_injected_signal_ranks_first(self):
        from discover import protocol
        trace = protocol.parse_vcd(_fixture_vcd())
        summary = vcd_slice.summarize_slice(trace, fail_time=110,
                                            window_cycles=2,
                                            time_per_cycle=10)
        # window [90..130]: ser_tx falls once (1 transition); quiet
        # stays flat (0); clk toggles 4x but is excludable noise.
        names = [name for (name, _n) in summary["ranking_no_clk"]]
        self.assertEqual(names[0], "tb.ser_tx")
        self.assertIn("tb.ser_tx", summary["summary"])
        top = dict(summary["ranking_no_clk"])
        self.assertEqual(top["tb.ser_tx"], 1)
        self.assertNotIn("tb.quiet", top)

    def test_empty_window_reports_zero_loudly(self):
        from discover import protocol
        trace = protocol.parse_vcd(_fixture_vcd())
        summary = vcd_slice.summarize_slice(trace, fail_time=300,
                                            window_cycles=1,
                                            time_per_cycle=10)
        self.assertEqual(summary["ranking_no_clk"], [])
        self.assertIn("0", summary["summary"])


class TestDefaultOff(unittest.TestCase):
    def test_pack_defaults_to_disabled_without_touching_disk(self):
        pack = vcd_slice.build_evidence_pack(
            case={"name": "x"}, tree="/nonexistent-tree",
            vcd_path="/nonexistent.vcd", sim_log="nope")
        self.assertFalse(pack["vcd_enabled"])
        self.assertIsNone(pack["vcd_slice"])

    def test_enabled_path_needs_artifacts_loudly(self):
        with self.assertRaises(ValueError):
            vcd_slice.build_evidence_pack(
                case={"name": "x"}, tree="/nonexistent-tree",
                enable_vcd=True)

    def test_enabled_path_slices_fixture(self):
        pack = vcd_slice.build_evidence_pack(
            case={"name": "x"}, tree="/nonexistent-tree",
            enable_vcd=True, vcd_text=_fixture_vcd(), fail_time=110,
            window_cycles=2)
        self.assertTrue(pack["vcd_enabled"])
        self.assertEqual(pack["vcd_slice"]["fail_time"], 110)
        top = [n for (n, _c) in
               pack["vcd_slice"]["ranking_no_clk"]]
        self.assertEqual(top[0], "tb.ser_tx")

    def test_loop_untouched_by_evidence_wiring(self):
        # B1 is additive + default-off: loop.py must not import the
        # evidence package (existing loop behavior byte-identical).
        # ("evidence" dict keys predate B1 and are not wiring.)
        import re
        with open(os.path.join(ROOT, "agent", "loop.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIsNone(re.search(r"^\s*(import|from)\s+\S*evidence",
                                    src, re.M))


if __name__ == "__main__":
    unittest.main()
