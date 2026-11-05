"""Red-first contract for --waveform-slicer {fixed,sam} (default fixed).

Flag OFF / slicer fixed == today's byte-identical behavior. slicer sam
calls sam-3.1 ONCE per run and attaches lane crops; any SAM failure
degrades LOUDLY to fixed grid (meta.sam.status=degraded), never silent.
"""
import io
import os
import struct
import sys
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _rgb_png(w, h, rows):
    def chunk(t, d):
        c = t + d
        return (struct.pack(">I", len(d)) + c +
                struct.pack(">I", zlib.crc32(c) & 0xffffffff))
    raw = b"".join(b"\x00" + bytes(v for px in r for v in px)
                    for r in rows)
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw)) +
            chunk(b"IEND", b""))


SYNTH = _rgb_png(8, 4, [[(i * 32, 0, 0)] * 8 for i in range(4)])


class TestSlicerFlag(unittest.TestCase):
    def test_default_is_fixed(self):
        from agent import loop
        ns = loop.parse_args(["--spec", "x"])
        self.assertEqual(ns.waveform_slicer, "fixed")

    def test_sam_selectable(self):
        from agent import loop
        ns = loop.parse_args(["--spec", "x", "--waveform-slicer", "sam"])
        self.assertEqual(ns.waveform_slicer, "sam")


class TestSliceWaveform(unittest.TestCase):
    def test_fixed_slices_no_network(self):
        from agent import loop
        crops, info = loop.slice_waveform(
            SYNTH, ["a", "b"], slicer="fixed", sam_caller=None)
        self.assertEqual(len(crops), 2)
        self.assertEqual(info["slicer"], "fixed")
        self.assertEqual(info["status"], "attached")

    def test_sam_calls_once_and_crops(self):
        from agent import loop
        calls = []

        def fake_caller(bearer, png):
            calls.append((bearer, png))
            return [{"x0": 0, "y0": 0, "x1": 8, "y1": 2},
                    {"x0": 0, "y0": 2, "x1": 8, "y1": 4}]

        crops, info = loop.slice_waveform(
            SYNTH, ["a", "b"], slicer="sam", sam_caller=fake_caller,
            bearer="BEARER")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(crops), 2)
        self.assertEqual(info["slicer"], "sam")
        self.assertEqual(info["status"], "attached")

    def test_sam_failure_degrades_to_grid_loudly(self):
        from agent import loop
        from agent.waveform import samlanes

        def boom(bearer, png):
            raise samlanes.SamError("transport down", status=0)

        crops, info = loop.slice_waveform(
            SYNTH, ["a", "b"], slicer="sam", sam_caller=boom,
            bearer="BEARER")
        self.assertEqual(len(crops), 2)  # GRID fallback still slices
        self.assertEqual(info["status"], "degraded")
        self.assertIn("transport down", info["reason"])


if __name__ == "__main__":
    unittest.main()
