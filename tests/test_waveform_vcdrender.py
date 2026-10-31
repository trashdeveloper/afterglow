"""WAVEFORM-VISION PILOT red-first tests: agent/waveform/vcdrender.py.

The image model is load-bearing only if a failing window can be RENDERED
from a real VCD. This file pins the rendering contract before the
implementation exists (TDD red-first):

- Unit (no sim): synthetic tiny VCD -> PNG exists, correct rows,
  failing-window highlight present (pixel probe at the module's
  documented geometry constants).
- Integration (gated): builds the REAL uart TB with DUMP_WAVES + --trace,
  runs one failing case, asserts a non-empty VCD, and renders it.
  Skipped unless AFTERGLOW_LIVE_WAVES=1 (needs Verilator + pinned RTL
  trees + minutes of build time).

Run: python3 tests/test_waveform_vcdrender.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from waveform import vcdrender  # noqa: E402  (RED before implementation)


def _fixture_vcd():
    # tb.clk: free-running clock, period 10. tb.ser_tx: idle high, falls
    # at t=105 (inside the failing window), rises at t=195. tb.cfg: a
    # 2-bit vector counting 00 -> 01 -> 10.
    return "\n".join([
        "$timescale 1ns $end",
        "$scope module tb $end",
        "$var wire 1 ! clk $end",
        "$var wire 1 \" ser_tx $end",
        "$var wire 2 # cfg $end",
        "$upscope $end",
        "$enddefinitions $end",
        "#0",
        "0!",
        '1"',
        "b00 #",
        "#5",
        "1!",
        "#10",
        "0!",
        "#15",
        "1!",
        "#20",
        "0!",
        "b01 #",
        "#105",
        '0"',
        "#110",
        "b10 #",
        "#195",
        '1"',
        "#300",
    ]) + "\n"


def _tmp_png(name):
    d = os.path.join(ROOT, "runs", "scratch")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    if os.path.exists(p):
        os.remove(p)
    return p


class TestRenderBasics(unittest.TestCase):
    def test_png_exists_and_opens(self):
        out = _tmp_png("pilot_unit_basic.png")
        info = vcdrender.render(_fixture_vcd(), ["tb.clk", "tb.ser_tx"], out)
        self.assertTrue(os.path.isfile(out), "PNG was not written")
        self.assertGreater(os.path.getsize(out), 0, "PNG is empty")
        from PIL import Image
        with Image.open(out) as im:
            self.assertEqual(im.mode, "RGB")
            self.assertEqual(im.size, (info["width"], info["height"]))

    def test_correct_rows_in_request_order(self):
        out = _tmp_png("pilot_unit_rows.png")
        info = vcdrender.render(
            _fixture_vcd(), ["tb.ser_tx", "tb.clk", "tb.cfg"], out)
        self.assertEqual(info["rows"], 3)
        self.assertEqual(info["signals"],
                         ["tb.ser_tx", "tb.clk", "tb.cfg"])
        self.assertEqual(
            info["height"],
            vcdrender.TOP_H + 3 * vcdrender.ROW_H + vcdrender.BOTTOM_H)

    def test_short_name_resolves(self):
        out = _tmp_png("pilot_unit_short.png")
        info = vcdrender.render(_fixture_vcd(), ["ser_tx"], out)
        self.assertEqual(info["signals"], ["tb.ser_tx"])

    def test_vector_signal_renders(self):
        out = _tmp_png("pilot_unit_vector.png")
        info = vcdrender.render(_fixture_vcd(), ["tb.cfg"], out)
        self.assertEqual(info["rows"], 1)
        self.assertTrue(os.path.getsize(out) > 0)


class TestFailWindowHighlight(unittest.TestCase):
    def _probe(self, info, out):
        # Documented geometry: highlight spans the full row area; the
        # first ROW_TOP+4 scanline is background (traces run mid-row),
        # so the probe pixel is tint iff the window is highlighted.
        from PIL import Image
        x = (info["highlight"]["x0"] + info["highlight"]["x1"]) // 2
        y = vcdrender.TOP_H + 4
        with Image.open(out) as im:
            return im.getpixel((x, y))

    def test_highlight_present_with_window(self):
        out = _tmp_png("pilot_unit_hi.png")
        info = vcdrender.render(_fixture_vcd(), ["tb.clk", "tb.ser_tx"],
                                out, fail_start=90, fail_end=210)
        self.assertIsNotNone(info["highlight"])
        r, g, b = self._probe(info, out)
        self.assertGreater(r, 200)
        self.assertLess(g, 235, "probe pixel is untinted background, "
                                 "highlight missing: (%d,%d,%d)" % (r, g, b))

    def test_no_highlight_without_window(self):
        out = _tmp_png("pilot_unit_nohi.png")
        info = vcdrender.render(_fixture_vcd(), ["tb.clk", "tb.ser_tx"],
                                out)
        self.assertIsNone(info["highlight"])
        from PIL import Image
        with Image.open(out) as im:
            self.assertEqual(
                im.getpixel((vcdrender.LEFT_W + 10, vcdrender.TOP_H + 4)),
                vcdrender.BG_RGB)


class TestLoudFailures(unittest.TestCase):
    def test_empty_vcd_raises(self):
        with self.assertRaises(ValueError):
            vcdrender.render("", ["tb.clk"], _tmp_png("x.png"))

    def test_unknown_signal_raises(self):
        with self.assertRaises(ValueError):
            vcdrender.render(_fixture_vcd(), ["tb.nope"], _tmp_png("x.png"))

    def test_empty_signal_list_raises(self):
        with self.assertRaises(ValueError):
            vcdrender.render(_fixture_vcd(), [], _tmp_png("x.png"))

    def test_inverted_window_raises(self):
        with self.assertRaises(ValueError):
            vcdrender.render(_fixture_vcd(), ["tb.clk"],
                             _tmp_png("x.png"),
                             fail_start=200, fail_end=100)

    def test_tb_guard_present(self):
        # The pilot TB edit must be ifdef-guarded so default builds are
        # byte-identical in behavior: the dump block exists AND is
        # wrapped in `ifdef DUMP_WAVES.
        tb = os.path.join(ROOT, "sim", "tb", "picosoc-uart-div_tb.sv")
        with open(tb, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("$dumpfile", src)
        self.assertIn("$dumpvars", src)
        self.assertIn("`ifdef DUMP_WAVES", src)
        self.assertIn("`endif", src)


@unittest.skipUnless(os.environ.get("AFTERGLOW_LIVE_WAVES") == "1",
                     "live-waves integration: needs Verilator + pinned "
                     "RTL (set AFTERGLOW_LIVE_WAVES=1)")
class TestLiveWavesIntegration(unittest.TestCase):
    def test_uart_tb_dumps_nonempty_vcd(self):
        import subprocess
        import tempfile
        sys.path.insert(0, os.path.join(ROOT, "sim"))
        import vbackend
        from discover import protocol

        case = vbackend.load_case("uart-tx-div")
        self.assertEqual(case["tb"], "picosoc-uart-div_tb.sv")
        pins = vbackend.load_pins()
        url, sha = pins[case["project"]]
        tree = vbackend.ensure_tree(case["project"], sha, url,
                                    case["files"])
        vbackend.inject(tree, case)
        case["vflags"] = (list(case.get("vflags", []))
                          + ["--trace", "+define+DUMP_WAVES"])
        log_path = os.path.join(tempfile.gettempdir(),
                                "pilot_waves_build.log")
        binary = vbackend.build(tree, case, log_path=log_path)
        with tempfile.TemporaryDirectory(
                prefix="pilot_waves_") as tmp:
            proc = subprocess.run(
                [str(binary), "+seed=42"], cwd=tmp,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=300)
            self.assertIn("tohost:", proc.stdout,
                          "sim produced no verdict line")
            vcd = os.path.join(tmp, "waves.vcd")
            self.assertTrue(os.path.isfile(vcd), "no waves.vcd dumped")
            size = os.path.getsize(vcd)
            print("\nLIVE waves.vcd bytes: %d" % size)
            self.assertGreater(size, 0, "waves.vcd is empty")
            trace = protocol.parse_vcd_file(vcd)
            self.assertGreater(len(trace["samples"]), 0)
            # Render the REAL dump: the pilot's load-bearing claim.
            # NOTE: fully-qualified names -- Verilator traces tb.ser_tx
            # and dut.ser_tx under separate ids, so the short name is
            # (correctly, loudly) ambiguous and resolve_signal refuses
            # to guess. Never short-name a live dump.
            out = _tmp_png("pilot_live_uart.png")
            info = vcdrender.render_vcd_file(
                vcd, ["tb_picosoc_uart.ser_tx", "tb_picosoc_uart.clk"],
                out, fail_start=0, fail_end=trace["t_end"])
            self.assertTrue(os.path.isfile(out))
            print("LIVE render rows: %d png bytes: %d"
                  % (info["rows"], os.path.getsize(out)))


if __name__ == "__main__":
    unittest.main()
