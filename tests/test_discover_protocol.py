"""Task A5 (Protocol-assertion checker) red-first tests: agent/discover/protocol.py.

- Minimal stdlib-only VCD parser: signals + timestamped value changes.
- Signal-level protocol rules checked against parsed VCD data:
  UART frame (start bit low, 8 data bits, stop bit high) and
  ready/valid handshake stability (valid held until ready).
- Violations carry timestamp + signal + rule; a clean trace yields an
  explicit all-clear report (never silent); malformed VCD raises loudly.

Run: python3 tests/test_discover_protocol.py -v   (from repo root)
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from discover import protocol  # noqa: E402  (RED before implementation: ModuleNotFoundError)


def _uart_header():
    return "\n".join([
        "$date today $end",
        "$version test $end",
        "$timescale 1ns $end",
        "$scope module tb $end",
        "$var wire 1 ! ser_tx $end",
        "$upscope $end",
        "$enddefinitions $end",
    ]) + "\n"


def _violating_vcd():
    # One UART frame, bit_period=100: falling edge at t=100, line held
    # low through the stop-bit sample at t=1050 -> framing error.
    return (_uart_header() + "\n".join([
        "#0",
        "1!",
        "#100",
        "0!",
        "#1100",
        "1!",
    ]) + "\n")


def _clean_vcd():
    # One clean UART frame for byte 0xA5 (LSB first: 1,0,1,0,0,1,0,1),
    # bit_period=100, t0=100: start [100,200), data [200,1000), stop [1000,1100).
    return (_uart_header() + "\n".join([
        "#0",
        "1!",
        "#100",
        "0!",     # start bit
        "#200",
        "1!",     # d0=1
        "#300",
        "0!",     # d1=0
        "#400",
        "1!",     # d2=1
        "#500",
        "0!",     # d3=0, d4=0 (no edge at #600)
        "#700",
        "1!",     # d5=1
        "#800",
        "0!",     # d6=0
        "#900",
        "1!",     # d7=1, stop=1 (held; bare stamp below ends the trace)
        "#1150",  # trace end past stop-bit sample t=1050, no new frame
    ]) + "\n")


def _handshake_vcd(clean):
    lines = [
        "$timescale 1ns $end",
        "$scope module tb $end",
        "$var wire 1 \" valid $end",
        "$var wire 1 # ready $end",
        "$upscope $end",
        "$enddefinitions $end",
        "#0",
        '0"',
        "0#",
        "#50",
        '1"',     # valid rises
    ]
    if clean:
        lines += ["#100", "1#", "#150", '0"']  # ready then valid drops: OK
    else:
        lines += ["#150", '0"']  # valid drops while ready still low: VIOLATION
    lines += ["#200", "1#"]
    return "\n".join(lines) + "\n"


class TestParseVcd(unittest.TestCase):
    def test_signals_timescale_and_samples_parsed(self):
        trace = protocol.parse_vcd(_clean_vcd())
        self.assertEqual(trace["timescale"], "1ns")
        self.assertIn("tb.ser_tx", trace["samples"])
        self.assertIn("tb.ser_tx", trace["signals"])
        samples = trace["samples"]["tb.ser_tx"]
        self.assertEqual(samples[0], (0, "1"))
        self.assertIn((100, "0"), samples)
        self.assertIn((900, "1"), samples)

    def test_suffix_lookup_resolves_unique_signal(self):
        trace = protocol.parse_vcd(_clean_vcd())
        self.assertEqual(protocol.resolve_signal(trace, "ser_tx"), "tb.ser_tx")

    def test_unknown_signal_raises_never_silent(self):
        trace = protocol.parse_vcd(_clean_vcd())
        with self.assertRaises(ValueError):
            protocol.resolve_signal(trace, "no_such_wire")


    def test_verilator_aliasing_one_id_many_names(self):
        # Verilator aliases one net across scopes under a single id
        # (loopback TB: tb.ser_tx == dut.ser_tx == dut.ser_rx): every
        # name gets the id's changes, and the sharing is disclosed.
        vcd = "\n".join([
            "$timescale 1ps $end",
            "$scope module tb $end",
            "$var wire 1 * ser_tx $end",
            "$scope module dut $end",
            "$var wire 1 * ser_tx $end",
            "$var wire 1 * ser_rx $end",
            "$upscope $end",
            "$upscope $end",
            "$enddefinitions $end",
            "#0",
            "1*",
            "#100",
            "0*",
            "#200",
            "1*",
        ]) + "\n"
        trace = protocol.parse_vcd(vcd)
        for name in ("tb.ser_tx", "tb.dut.ser_tx", "tb.dut.ser_rx"):
            self.assertIn(name, trace["samples"])
            self.assertEqual(trace["samples"][name],
                             [(0, "1"), (100, "0"), (200, "1")])
        self.assertIn("*", trace["aliases"])
        self.assertEqual(sorted(trace["aliases"]["*"]),
                         ["tb.dut.ser_rx", "tb.dut.ser_tx", "tb.ser_tx"])


class TestMalformedVcd(unittest.TestCase):
    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            protocol.parse_vcd("")

    def test_missing_enddefinitions_raises(self):
        with self.assertRaises(ValueError):
            protocol.parse_vcd("$timescale 1ns $end\n#0\n1!\n")

    def test_change_for_unknown_id_raises(self):
        bad = (_uart_header() + "#0\n1!\n#50\n0$\n")
        with self.assertRaises(ValueError):
            protocol.parse_vcd(bad)

    def test_unterminated_var_raises(self):
        bad = ("$timescale 1ns $end\n$scope module tb $end\n"
               "$var wire 1 ! ser_tx\n$enddefinitions $end\n")
        with self.assertRaises(ValueError):
            protocol.parse_vcd(bad)

    def test_non_string_input_raises(self):
        with self.assertRaises(TypeError):
            protocol.parse_vcd(None)


class TestUartRule(unittest.TestCase):
    def test_stop_bit_low_flagged_with_exact_timestamp(self):
        trace = protocol.parse_vcd(_violating_vcd())
        report = protocol.check_trace(
            trace, [{"kind": "uart", "signal": "ser_tx", "bit_period": 100}])
        self.assertFalse(report["all_clear"])
        self.assertEqual(len(report["violations"]), 1)
        v = report["violations"][0]
        self.assertEqual(v["time"], 1050)  # stop-bit middle: t0 + 9.5 * period
        self.assertEqual(v["signal"], "tb.ser_tx")
        self.assertEqual(v["rule"], "uart-stop-bit-low")

    def test_start_bit_high_flagged_with_exact_timestamp(self):
        # Line glitches high mid-start-bit: sample at t0+period/2 is 1.
        vcd = (_uart_header() + "\n".join([
            "#0", "1!",
            "#100", "0!",
            "#120", "1!",   # glitch: start-bit middle (t=150) reads high
            "#1100", "1!",
        ]) + "\n")
        trace = protocol.parse_vcd(vcd)
        report = protocol.check_trace(
            trace, [{"kind": "uart", "signal": "ser_tx", "bit_period": 100}])
        self.assertFalse(report["all_clear"])
        rules = [v["rule"] for v in report["violations"]]
        self.assertIn("uart-start-bit-high", rules)
        v = next(v for v in report["violations"]
                 if v["rule"] == "uart-start-bit-high")
        self.assertEqual(v["time"], 150)

    def test_clean_frame_yields_explicit_all_clear(self):
        trace = protocol.parse_vcd(_clean_vcd())
        report = protocol.check_trace(
            trace, [{"kind": "uart", "signal": "ser_tx", "bit_period": 100}])
        self.assertEqual(report["violations"], [])
        self.assertTrue(report["all_clear"])
        self.assertIn("ALL CLEAR", report["summary"])
        # Decoded byte pinned: 0xA5.
        self.assertEqual(report["frames"][0]["byte"], 0xA5)
        self.assertEqual(report["frames"][0]["start"], 100)

    def test_trace_starting_mid_frame_skips_to_idle(self):
        # Trace begins with the line already low (power-on dummy char
        # in progress): no assumed-idle false start; the checker
        # synchronizes on the first observed idle->low edge and the
        # following clean 0xA5 frame is all-clear.
        vcd = (_uart_header() + "\n".join([
            "#0", "0!",     # mid-frame low at trace start, not a start bit
            "#90", "1!",    # line goes idle; dummy char ends
            "#100", "0!",   # real frame start: falling edge from idle
            "#200", "1!",   # d0=1
            "#300", "0!",   # d1=0
            "#400", "1!",   # d2=1
            "#500", "0!",   # d3=0, d4=0
            "#700", "1!",   # d5=1
            "#800", "0!",   # d6=0
            "#900", "1!",   # d7=1, stop=1 held
            "#1150",
        ]) + "\n")
        trace = protocol.parse_vcd(vcd)
        report = protocol.check_trace(
            trace, [{"kind": "uart", "signal": "ser_tx", "bit_period": 100}])
        self.assertEqual(report["violations"], [])
        self.assertTrue(report["all_clear"])
        self.assertEqual(len(report["frames"]), 1)
        self.assertEqual(report["frames"][0]["byte"], 0xA5)

    def test_truncated_frame_flagged_not_silent(self):
        vcd = _uart_header() + "#0\n1!\n#100\n0!\n#200\n1!\n"
        trace = protocol.parse_vcd(vcd)
        report = protocol.check_trace(
            trace, [{"kind": "uart", "signal": "ser_tx", "bit_period": 100}])
        self.assertFalse(report["all_clear"])
        self.assertEqual(report["violations"][0]["rule"],
                         "uart-frame-truncated")

    def test_unknown_signal_rule_raises(self):
        trace = protocol.parse_vcd(_clean_vcd())
        with self.assertRaises(ValueError):
            protocol.check_trace(
                trace, [{"kind": "uart", "signal": "ghost",
                         "bit_period": 100}])

    def test_empty_rules_raise(self):
        trace = protocol.parse_vcd(_clean_vcd())
        with self.assertRaises(ValueError):
            protocol.check_trace(trace, [])


class TestHandshakeRule(unittest.TestCase):
    def test_valid_drop_without_ready_flagged_exact_time(self):
        trace = protocol.parse_vcd(_handshake_vcd(clean=False))
        report = protocol.check_trace(
            trace, [{"kind": "handshake", "valid": "valid",
                     "ready": "ready"}])
        self.assertFalse(report["all_clear"])
        self.assertEqual(len(report["violations"]), 1)
        v = report["violations"][0]
        self.assertEqual(v["time"], 150)
        self.assertEqual(v["signal"], "tb.valid")
        self.assertEqual(v["rule"], "handshake-valid-drop")

    def test_valid_held_until_ready_is_all_clear(self):
        trace = protocol.parse_vcd(_handshake_vcd(clean=True))
        report = protocol.check_trace(
            trace, [{"kind": "handshake", "valid": "valid",
                     "ready": "ready"}])
        self.assertEqual(report["violations"], [])
        self.assertTrue(report["all_clear"])
        self.assertIn("ALL CLEAR", report["summary"])


class TestDiscoveryReport(unittest.TestCase):
    def test_report_file_names_violations_and_all_clear(self):
        trace = protocol.parse_vcd(_violating_vcd())
        report = protocol.check_trace(
            trace, [{"kind": "uart", "signal": "ser_tx", "bit_period": 100}])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "discovery_protocol.md")
            protocol.write_discovery_report(path, "proto-fixture", report)
            text = open(path, encoding="utf-8").read()
        self.assertIn("uart-stop-bit-low", text)
        self.assertIn("1050", text)

        clean = protocol.check_trace(
            protocol.parse_vcd(_clean_vcd()),
            [{"kind": "uart", "signal": "ser_tx", "bit_period": 100}])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "discovery_protocol.md")
            protocol.write_discovery_report(path, "proto-fixture", clean)
            text = open(path, encoding="utf-8").read()
        self.assertIn("ALL CLEAR", text)


if __name__ == "__main__":
    unittest.main()
