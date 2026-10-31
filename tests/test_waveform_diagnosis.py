"""Waveform-diagnosis wiring tests (TDD red-first for --waveform).

Run: python3 tests/test_waveform_diagnosis.py -v   (from repo root)

Static fixtures + unittest.mock seams only -- no live API calls, no keys,
no real sim builds, no real VCD renders:

- flag OFF (default): loop.py behavior is byte-identical to the
  pre-waveform path. Same run-id twice yields identical bundle
  checksums (wall-clock keys masked); no waveform capture/render runs;
  no new imports execute; no new bundle keys appear.
- flag ON + dump-capable TB: capture+render run once from the RED
  tree; waveform.png is attached to the diagnosis image-model call.
  The image call is mocked at the loop.visual.generate boundary and
  we assert the PNG BYTES passed in -- never model output.
- flag ON + missing/failed VCD: degrades LOUDLY to text-only (meta
  note + reason), the run continues, the image call goes out
  attachment-free.
- flag ON + unsupported TB: same loud degrade, no capture attempted.
- pure unit tests: waveform_plan go/no-go, tb_supports_dump_waves,
  _wave_fail_window marker rule (TRAP wins, tohost: FAIL fallback,
  CYCLES= fallback; raises loudly when unanchored).
"""
import hashlib
import json
import os
import re
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASE = "uart-start-half"  # TB picosoc-uart-div_tb.sv (DUMP_WAVES-capable)

FAKE_BOOT_RED = (
    "cycle 0: reset assert\n"
    "cycle 1: fetch pc=0x00\n"
    "TRAP: test trap\n"
    "tohost: FAIL 0x1\n"
    "CYCLES=1234\n"
)

MODEL_OK = {
    "content": json.dumps({
        "diagnosis": "receiver clock tree glitch delays bit capture",
        "expected": "tohost: PASS",
        "patch_diff": (
            "diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v\n"
            "--- a/picosoc/simpleuart.v\n"
            "+++ b/picosoc/simpleuart.v\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    }),
    "raw": {"id": "mock-id"},
    "latency_ms": 7,
    "request_id": "mock-req",
    "model": loop.TEXT_MODEL,
    "usage": {},
}

IMG_OK = {"latency_ms": 5, "request_id": "mock-img", "bytes": 100,
          "sha256": "abc123", "transport": "mock",
          "model": "muse-image-1.0"}

# Deterministic stand-ins for rendered PNG bytes (never real images;
# the wiring -- not pixels -- is under test).
FAKE_EXPLAIN_PNG = b"\x89FAKE-EXPLAIN-PNG" * 64
FAKE_WAVE_PNG = b"\x89FAKE-WAVEFORM-PNG" * 64

# Wall-clock tokens masked before checksum comparison in the replay test.
CLOCK_RES = [
    re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"),
    re.compile(rb"time-to-green `\d+(?:\.\d+)?s`"),
    re.compile(rb'"time_to_green_s": \d+(?:\.\d+)?'),
]


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _fake_red_sim(case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write(FAKE_BOOT_RED)
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case": case, "pass": False,
                             "tohost": "FAIL 0x1",
                             "checksum": "deadbeef", "cycles": 1234,
                             "seed": seed}) + "\n")
    return 1, "mock RED"


def _fake_resim_red(tree, case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write("tohost: FAIL 0x1\nCYCLES=10\n")
    verdict = {"case": case["name"], "pass": False,
               "tohost": "FAIL 0x1", "checksum": "c" * 64,
               "cycles": 10, "seed": seed}
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(verdict) + "\n")
    return verdict


def _fake_apply_ok(*args, **kwargs):
    return {"guard_ok": True, "reasons": [], "applied": True,
            "repaired": False, "patch_lines": 2,
            "files": ["picosoc/simpleuart.v"],
            "applied_diff": "diff text\n"}


def _make_generate_fake(record):
    """Fake loop.visual.generate: writes fixed PNG bytes, records the
    attachments kwarg (the boundary assertion target), and returns
    provenance mirroring the real generate (attachments record only
    when attachments were passed)."""
    def _fake(prompt, out_path, model=loop.visual.IMAGE_MODEL,
              timeout=180, attachments=None):
        record["attachments"] = attachments
        with open(out_path, "wb") as fh:
            fh.write(FAKE_EXPLAIN_PNG)
        info = dict(IMG_OK)
        if attachments:
            rec = []
            for att in attachments:
                blob = att.get("png_bytes", b"")
                rec.append({"name": att.get("name", "?"),
                            "sha256": hashlib.sha256(blob).hexdigest(),
                            "bytes": len(blob)})
            info["attachments"] = rec
        return info
    return _fake


def _fake_capture_ok(**kwargs):
    return {"vcd": os.path.join(kwargs.get("out_dir", ""), "waves.vcd"),
            "log": "tohost: FAIL 0x1\nCYCLES=1234\n",
            "tohost": "FAIL 0x1", "seed": kwargs.get("seed", 42),
            "binary": "/nonexistent/Vtb_picosoc_uart"}


def _make_render_fake(record):
    def _fake(*, vcd_path, out_path, signals, fail_start=None,
              fail_end=None, title=None):
        record["render"] = {"vcd_path": vcd_path, "signals": list(signals),
                            "fail_window": [fail_start, fail_end],
                            "title": title}
        with open(out_path, "wb") as fh:
            fh.write(FAKE_WAVE_PNG)
        return {"signals": list(signals), "out": out_path}
    return _fake


def _masked_digest(path):
    with open(path, "rb") as fh:
        blob = fh.read()
    for rx in CLOCK_RES:
        blob = rx.sub(b"<clock>", blob)
    return hashlib.sha256(blob).hexdigest()


def _bundle_digests(run_dir):
    digests = {}
    for base, _dirs, files in os.walk(run_dir):
        for name in sorted(files):
            if name in ("waveform.png",):
                continue  # binary attach covered separately, byte-exact
            full = os.path.join(base, name)
            rel = os.path.relpath(full, run_dir)
            digests[rel] = _masked_digest(full)
    return digests


class TestWaveformFlag(unittest.TestCase):
    def test_flag_defaults_off(self):
        args = loop.parse_args(["--case", "dummy"])
        self.assertIs(args.waveform, False)

    def test_flag_on_parses(self):
        args = loop.parse_args(["--case", "dummy", "--waveform"])
        self.assertIs(args.waveform, True)

    def test_no_new_top_level_imports(self):
        # The default (flag-OFF) path must execute no new imports: the
        # only waveform/vcdrender import in loop.py is the lazy one
        # inside render_waveform_png (indented, never module top-level).
        with open(os.path.join(ROOT, "agent", "loop.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        hits = [ln for ln in src.splitlines()
                if re.match(r"^\s*(import|from)\s+", ln)
                and re.search(r"\bwaveform\b|\bvcdrender\b", ln)]
        self.assertTrue(hits, "expected waveform wiring to exist")
        for ln in hits:
            self.assertTrue(ln.startswith((" ", "\t")),
                            "top-level waveform import: %r" % (ln,))

    def test_plan_disabled_by_default(self):
        plan = loop.waveform_plan(SimpleNamespace(), {"tb": "x", "top": "y"})
        self.assertEqual(plan["status"], "disabled")

    def test_plan_ready_for_dump_capable_tb(self):
        args = SimpleNamespace(waveform=True)
        case = {"tb": "picosoc-uart-div_tb.sv", "top": "tb_picosoc_uart"}
        plan = loop.waveform_plan(args, case)
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(loop.tb_supports_dump_waves(case["tb"]))

    def test_plan_degraded_for_plain_tb(self):
        args = SimpleNamespace(waveform=True)
        plan = loop.waveform_plan(
            args, {"tb": "picorv32-fetch_tb.sv", "top": "tb_picorv32"})
        self.assertEqual(plan["status"], "degraded")
        self.assertIn("DUMP_WAVES", plan["reason"])
        self.assertFalse(
            loop.tb_supports_dump_waves("picorv32-fetch_tb.sv"))

    def test_plan_degraded_for_missing_tb(self):
        args = SimpleNamespace(waveform=True)
        plan = loop.waveform_plan(
            args, {"tb": "no-such_tb.sv", "top": "tb_picosoc_uart"})
        self.assertEqual(plan["status"], "degraded")
        self.assertFalse(loop.tb_supports_dump_waves("no-such_tb.sv"))

    def test_plan_degraded_for_unknown_top(self):
        args = SimpleNamespace(waveform=True)
        plan = loop.waveform_plan(
            args, {"tb": "picosoc-uart-div_tb.sv", "top": "tb_unknown"})
        self.assertEqual(plan["status"], "degraded")
        self.assertIn("tb_unknown", plan["reason"])

    def test_fail_window_uses_cycles_end_count(self):
        start, end = loop._wave_fail_window(FAKE_BOOT_RED, "TRAP:")
        self.assertEqual((start, end), (1234 * 10 - 200, 1234 * 10 + 200))

    def test_fail_window_trap_line_wins(self):
        log = ("cycle 5: x\n"
               "TRAP: trap at cycles=77\n"
               "tohost: FAIL 0x1\n"
               "CYCLES=9999\n")
        start, end = loop._wave_fail_window(log, "TRAP:")
        self.assertEqual((start, end), (77 * 10 - 200, 77 * 10 + 200))

    def test_fail_window_tohost_fallback(self):
        log = "cycle 3: tohost: FAIL 0x2\nCYCLES=50\n"
        start, end = loop._wave_fail_window(log, "TRAP:")
        # fail_t=30 minus pad clamps at VCD time 0 (never negative).
        self.assertEqual((start, end), (0, 3 * 10 + 200))

    def test_fail_window_raises_loudly_when_unanchored(self):
        with self.assertRaises(loop.WaveformError):
            loop._wave_fail_window("", "TRAP:")
        with self.assertRaises(loop.WaveformError):
            loop._wave_fail_window("cycle 1: all good\nCYCLES=10\n", "TRAP:")
        with self.assertRaises(loop.WaveformError):
            loop._wave_fail_window("TRAP: stuck, no stamps anywhere\n",
                                   "TRAP:")


class TestWaveformLoopWiring(unittest.TestCase):
    def _base_patches(self, record, extra=()):
        patches = [
            mock.patch.object(loop, "run_sim_before",
                              side_effect=_fake_red_sim),
            mock.patch.object(loop, "rtl_excerpt",
                              return_value="line 1: fake excerpt"),
            mock.patch.object(loop, "call_text_with_backoff",
                              return_value=(dict(MODEL_OK), [])),
            mock.patch.object(loop.visual, "generate",
                              side_effect=_make_generate_fake(record)),
            mock.patch.object(loop, "try_apply_diff",
                              side_effect=_fake_apply_ok),
            mock.patch.object(loop, "resim_after",
                              side_effect=_fake_resim_red),
        ]
        return patches + list(extra)

    def _start(self, patches):
        for p in patches:
            self.addCleanup(p.stop)
            p.start()

    def test_flag_off_replay_byte_identical(self):
        # Same run-id twice, flag OFF: identical bundle checksums
        # (wall-clock keys masked), no waveform keys, no capture/render,
        # no new modules imported on the default path.
        import contextlib
        run_id = "test-waveform-replay-off"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        cap = mock.Mock(side_effect=AssertionError("capture must not run"))
        ren = mock.Mock(side_effect=AssertionError("render must not run"))
        digest0, rc0 = None, None
        for rep in range(2):
            record = {}
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    loop, "run_sim_before", side_effect=_fake_red_sim))
                stack.enter_context(mock.patch.object(
                    loop, "rtl_excerpt",
                    return_value="line 1: fake excerpt"))
                stack.enter_context(mock.patch.object(
                    loop, "call_text_with_backoff",
                    return_value=(dict(MODEL_OK), [])))
                stack.enter_context(mock.patch.object(
                    loop.visual, "generate",
                    side_effect=_make_generate_fake(record)))
                stack.enter_context(mock.patch.object(
                    loop, "try_apply_diff", side_effect=_fake_apply_ok))
                stack.enter_context(mock.patch.object(
                    loop, "resim_after", side_effect=_fake_resim_red))
                stack.enter_context(mock.patch.object(
                    loop, "capture_waves_vcd", cap))
                stack.enter_context(mock.patch.object(
                    loop, "render_waveform_png", ren))
                mods_before = set(sys.modules)
                rc = loop.main(["--case", CASE, "--seed", "42",
                                "--run-id", run_id])
                new_mods = sorted(
                    m for m in (set(sys.modules) - mods_before)
                    if "waveform" in m or "vcdrender" in m)
            digests = _bundle_digests(run_dir)
            self.assertFalse(
                [m for m in digests if "waveform" in m.lower()],
                "flag-OFF bundle must carry no waveform files")
            meta = _read_json(os.path.join(run_dir, "meta.json"))
            self.assertNotIn("waveform", meta)
            img_req = _read_json(os.path.join(
                run_dir, "api", "image_request.json"))
            self.assertNotIn("waveform", img_req)
            img_resp = _read_json(os.path.join(
                run_dir, "api", "image_response.json"))
            self.assertNotIn("attachments", img_resp)
            self.assertEqual(record.get("attachments"), None)
            self.assertEqual(new_mods, [])
            if rep == 0:
                (digest0, rc0) = (digests, rc)
            else:
                self.assertEqual(rc, rc0)
                self.assertEqual(digests, digest0)
        self.assertFalse(cap.called)
        self.assertFalse(ren.called)
        # Non-trivial bundle: the replay compared real files.
        self.assertGreaterEqual(len(digest0), 10)

    def test_flag_on_attaches_png_bytes(self):
        # Flag ON, dump-capable TB: waveform.png rendered once from the
        # RED tree and its BYTES passed to the image call boundary.
        run_id = "test-waveform-attached"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        record = {}
        patches = self._base_patches(record, extra=[
            mock.patch.object(loop, "capture_waves_vcd",
                              side_effect=_fake_capture_ok),
            mock.patch.object(loop, "render_waveform_png",
                              side_effect=_make_render_fake(record)),
        ])
        self._start(patches)
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id, "--waveform"])
        self.assertEqual(rc, 1)  # resim stays RED (fake), wiring held
        # PNG bytes travelled the whole path, byte-exact.
        png_path = os.path.join(run_dir, "waveform.png")
        self.assertTrue(os.path.isfile(png_path))
        with open(png_path, "rb") as fh:
            self.assertEqual(fh.read(), FAKE_WAVE_PNG)
        atts = record.get("attachments")
        self.assertIsNotNone(atts)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["name"], "waveform.png")
        self.assertEqual(atts[0]["png_bytes"], FAKE_WAVE_PNG)
        # Bundle provenance, not model output.
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["waveform"]["status"], "attached")
        self.assertEqual(meta["waveform"]["png_sha256"],
                         hashlib.sha256(FAKE_WAVE_PNG).hexdigest())
        img_req = _read_json(os.path.join(run_dir, "api",
                                          "image_request.json"))
        self.assertEqual(img_req["waveform"]["status"], "attached")
        self.assertEqual(img_req["waveform"]["png_sha256"],
                         hashlib.sha256(FAKE_WAVE_PNG).hexdigest())
        # No raw payload in JSON: byte count travels as an int, and the
        # record round-trips through json (bytes would raise TypeError).
        self.assertEqual(img_req["waveform"]["png_bytes"],
                         len(FAKE_WAVE_PNG))
        img_resp = _read_json(os.path.join(run_dir, "api",
                                           "image_response.json"))
        self.assertEqual(img_resp["attachments"][0]["sha256"],
                         hashlib.sha256(FAKE_WAVE_PNG).hexdigest())
        # Render saw the fail window + the TB signal map.
        self.assertEqual(record["render"]["signals"],
                         list(loop.WAVEFORM_SIGNALS["tb_picosoc_uart"]))
        self.assertEqual(record["render"]["fail_window"],
                         [1234 * 10 - 200, 1234 * 10 + 200])

    def test_flag_on_missing_vcd_degrades_loudly(self):
        # Capture blows up (no waves.vcd): run continues text-only,
        # meta carries the loud note, image call goes out clean.
        run_id = "test-waveform-degraded"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        record = {}
        patches = self._base_patches(record, extra=[
            mock.patch.object(
                loop, "capture_waves_vcd",
                side_effect=loop.WaveformError(
                    "trace run produced no waves.vcd")),
        ])
        self._start(patches)
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id, "--waveform"])
        self.assertEqual(rc, 1)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["waveform"]["status"], "degraded")
        self.assertIn("waves.vcd", meta["waveform"]["reason"])
        self.assertEqual(record.get("attachments"), None)
        img_req = _read_json(os.path.join(run_dir, "api",
                                          "image_request.json"))
        self.assertEqual(img_req["waveform"]["status"], "degraded")
        img_resp = _read_json(os.path.join(run_dir, "api",
                                           "image_response.json"))
        self.assertNotIn("attachments", img_resp)
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "waveform.png")))

    def test_flag_on_unsupported_tb_degrades_without_capture(self):
        # TB with no DUMP_WAVES block: no capture attempted, loud note.
        run_id = "test-waveform-unsupported"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        record = {}
        cap = mock.Mock(side_effect=AssertionError("must not capture"))
        patches = self._base_patches(record, extra=[
            mock.patch.object(loop, "waveform_plan", return_value={
                "status": "degraded",
                "reason": "TB 'picorv32-fetch_tb.sv' has no DUMP_WAVES "
                          "dump block: text-only"}),
            mock.patch.object(loop, "capture_waves_vcd", cap),
        ])
        self._start(patches)
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id, "--waveform"])
        self.assertEqual(rc, 1)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["waveform"]["status"], "degraded")
        self.assertIn("DUMP_WAVES", meta["waveform"]["reason"])
        self.assertEqual(record.get("attachments"), None)
        self.assertFalse(cap.called)


if __name__ == "__main__":
    unittest.main()
