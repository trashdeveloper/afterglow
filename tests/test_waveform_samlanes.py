"""SAM lane-slicing contract tests (TDD red-first for SAM-PILOT).

Run: python3 tests/test_waveform_samlanes.py -v   (from repo root)

RED-FIRST PROVENANCE: this file was written against the contract
(call_sam(bearer, png_bytes) -> lane boxes; crop_lanes(png, boxes) ->
PNG crops; stdlib urllib only) BEFORE agent/waveform/samlanes.py
existed -- it fails with ModuleNotFoundError until the implementation
lands. No test below was edited to match an implementation; the
implementation was written to satisfy them.

Hermetic by construction -- NO live SAM calls, NO keys, NO external
network, ever:
- SAM wire tests POST against a LOCAL throwaway http.server (real HTTP
  over loopback, canned SAM-response fixture bytes) with base_url
  pointed at 127.0.0.1. The fixture is canned JSON, never a recording
  of a live call (no live call has been fired; see
  eval/ablation-samlanes.md PREREG).
- Every test clears MODEL_API_KEY/SPARK_API_KEY/META_API_KEY in setUp
  and restores the environment in tearDown (zero-env-leak: the suite
  passes with the real keys absent AND leaves os.environ identical).
- Key-precedence + env-fallback tests use sentinel strings
  ("sentinel-*" -- never real credentials) and assert exception
  messages never echo them.

Covers agent/waveform/samlanes.py:
- build_request: exact JSON body shape (model sam-3.1, system+user
  messages, base64 data-URL image part, temperature 0); rejects
  empty/non-PNG input, bad model, bad max_tokens.
- call_sam via loopback: returns lane boxes sorted top-to-bottom;
  sends Bearer auth + POSTs the chat-completions path with model
  sam-3.1; the transmitted image decodes to the input PNG bytes.
- call_sam key rules: explicit bearer wins over env; bearer=None
  falls back to env with loop.py precedence
  (MODEL > SPARK > META); missing key fails loudly with no key
  material in the message.
- call_sam loud errors: API error field, non-JSON body, empty
  content, zero lanes, degenerate box, HTTP 429/500 (SamError.status
  set), timeout (status None).
- parse_lane_boxes pure unit: accepts {"lanes"}, {"boxes"}, bare
  list; rounds floats; sorts (y0,x0); rejects missing keys,
  non-numeric coords, degenerate boxes, empty lists.
- crop_lanes: Pillow-built banded fixture PNG (independent encoder)
  -> exact per-lane crops verified pixel-by-pixel with Pillow
  (independent decoder); stdlib decoder cross-checked against Pillow
  on a gradient; overflow edges clip; fully-outside / degenerate /
  non-PNG / empty inputs raise ValueError loudly.
- fixed_grid_lanes: N contiguous full-width strips covering the frame.
"""

import base64
import json
import os
import struct
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.waveform import samlanes  # noqa: E402  (RED before implementation)
SamError = samlanes.SamError  # explicit error-class gate, same RED


KEY_VARS = ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY")
BASE_VAR = "SPARK_API_URL"

# Canned SAM fixture: three lanes deliberately OUT of top-to-bottom order
# so the test pins the (y0,x0) sort. Coordinates are pixel ints.
CANNED_LANES_OOO = [
    {"x0": 0, "y0": 40, "x1": 100, "y1": 60},
    {"x0": 0, "y0": 0, "x1": 100, "y1": 20},
    {"x0": 0, "y0": 20, "x1": 100, "y1": 40},
]
CANNED_LANES_SORTED = [
    {"x0": 0, "y0": 0, "x1": 100, "y1": 20},
    {"x0": 0, "y0": 20, "x1": 100, "y1": 40},
    {"x0": 0, "y0": 40, "x1": 100, "y1": 60},
]


def _sam_body(lanes_obj, req_id="req-sam-1"):
    content = lanes_obj if isinstance(lanes_obj, str) else json.dumps(lanes_obj)
    return {
        "id": req_id,
        "model": "sam-3.1",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 60},
    }


def _tiny_png(w=8, h=8, rgb=(1, 2, 3)):
    # Valid 8-bit RGB PNG via the module's own encoder (deterministic;
    # the decode path is cross-checked against Pillow independently).
    return samlanes._encode_png_rgb(w, h, bytes(rgb) * w * h)


def _banded_png(w=100, h=60):
    # Independent fixture encoder (Pillow, NOT samlanes): three 20px
    # horizontal bands red/green/blue.
    from PIL import Image
    im = Image.new("RGB", (w, h))
    bands = [(200, 30, 30), (30, 200, 30), (30, 30, 200)]
    for y in range(h):
        for x in range(w):
            im.putpixel((x, y), bands[(y // 20) % 3])
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _gradient_png(w=64, h=32):
    # Exercises PNG filters: pixel values vary in x AND y.
    from PIL import Image
    import io
    im = Image.new("RGB", (w, h))
    for y in range(h):
        for x in range(w):
            im.putpixel((x, y), ((x * 4) % 256, (y * 8) % 256,
                                 ((x + y) * 3) % 256))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def _send(self, status, payload, ctype="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("x-request-id", "hdr-req-sam-1")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except BrokenPipeError:
            pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.last_path = self.path
        self.server.last_auth = self.headers.get("Authorization", "")
        try:
            self.server.last_body = self.rfile.read(length)
        except Exception:
            self.server.last_body = b""
        if self.mode == "err429":
            self._send(429, {"error": {"message": "rate limited"}})
        elif self.mode == "err500":
            self._send(500, {"error": {"message": "boom"}})
        elif self.mode == "badjson":
            self._send(200, b"not json{{", ctype="text/plain")
        elif self.mode == "empty_content":
            self._send(200, _sam_body({"lanes": []}) | {"choices": [
                {"message": {"role": "assistant", "content": "   "}}]})
        elif self.mode == "no_lanes":
            self._send(200, _sam_body({"lanes": []}))
        elif self.mode == "degenerate":
            self._send(200, _sam_body(
                {"lanes": [{"x0": 5, "y0": 5, "x1": 5, "y1": 9}]}))
        elif self.mode == "error_field":
            self._send(200, {"error": {"message": "bad image", "code": 400}})
        elif self.mode == "boxes_key":
            self._send(200, _sam_body({"boxes": CANNED_LANES_OOO}))
        elif self.mode == "bare_list":
            self._send(200, _sam_body(CANNED_LANES_OOO))
        elif self.mode == "slow":
            import time as _t
            _t.sleep(2.0)
            self._send(200, _sam_body({"lanes": CANNED_LANES_OOO}))
        else:
            self._send(200, _sam_body({"lanes": CANNED_LANES_OOO}))

    def log_message(self, *args):
        pass


def _serve(mode):
    _Handler.mode = mode
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.last_path = None
    srv.last_auth = None
    srv.last_body = None
    thr = threading.Thread(target=srv.serve_forever,
                           kwargs={"poll_interval": 0.01})
    thr.daemon = True
    thr.start()
    return srv, thr


def _stop(srv, thr):
    srv.shutdown()
    thr.join()
    srv.server_close()


class _EnvHygiene(unittest.TestCase):
    # Every test class inherits this: keys cleared, env restored.

    def setUp(self):
        self._saved = {v: os.environ.get(v) for v in KEY_VARS + (BASE_VAR,)}
        for v in KEY_VARS + (BASE_VAR,):
            os.environ.pop(v, None)

    def tearDown(self):
        for v in KEY_VARS + (BASE_VAR,):
            if self._saved[v] is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = self._saved[v]

    def test_env_restored_by_hygiene(self):
        for v in KEY_VARS + (BASE_VAR,):
            self.assertNotIn(v, os.environ,
                             "key/base env must be clear during tests")


class TestBuildRequest(_EnvHygiene):
    def test_exact_body_shape(self):
        png = _tiny_png()
        body = samlanes.build_request(png)
        self.assertEqual(body["model"], "sam-3.1")
        self.assertEqual(body["model"], samlanes.SAM_MODEL)
        self.assertEqual(body["temperature"], 0)
        self.assertGreater(body["max_tokens"], 0)
        msgs = body["messages"]
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("lanes", msgs[0]["content"])
        user = msgs[1]
        self.assertEqual(user["role"], "user")
        parts = user["content"]
        kinds = {p["type"] for p in parts}
        self.assertEqual(kinds, {"text", "image_url"})
        img = next(p for p in parts if p["type"] == "image_url")
        url = img["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), png)
        json.dumps(body)  # must be wire-serializable

    def test_rejects_empty_and_non_png(self):
        with self.assertRaises(ValueError):
            samlanes.build_request(b"")
        with self.assertRaises(ValueError):
            samlanes.build_request(b"definitely not a png")
        with self.assertRaises(ValueError):
            samlanes.build_request("not bytes")

    def test_rejects_bad_model_and_tokens(self):
        with self.assertRaises(ValueError):
            samlanes.build_request(_tiny_png(), model="  ")
        with self.assertRaises(ValueError):
            samlanes.build_request(_tiny_png(), max_tokens=0)
        with self.assertRaises(ValueError):
            samlanes.build_request(_tiny_png(), max_tokens=True)


class TestCallSamLoopback(_EnvHygiene):
    def _url(self, srv):
        return "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]

    def test_returns_sorted_lane_boxes(self):
        srv, thr = _serve("ok")
        try:
            boxes = samlanes.call_sam("sentinel-bearer-1", _tiny_png(),
                                      base_url=self._url(srv))
        finally:
            _stop(srv, thr)
        self.assertEqual(boxes, CANNED_LANES_SORTED)

    def test_wire_shape_auth_path_model_image(self):
        png = _tiny_png()
        srv, thr = _serve("ok")
        try:
            samlanes.call_sam("sentinel-bearer-2", png,
                              base_url=self._url(srv))
        finally:
            body, auth, path = srv.last_body, srv.last_auth, srv.last_path
            _stop(srv, thr)
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(auth, "Bearer sentinel-bearer-2")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["model"], "sam-3.1")
        parts = payload["messages"][1]["content"]
        img = next(p for p in parts if p["type"] == "image_url")
        self.assertEqual(base64.b64decode(
            img["image_url"]["url"].split(",", 1)[1]), png)

    def test_accepts_boxes_key_and_bare_list(self):
        for mode in ("boxes_key", "bare_list"):
            srv, thr = _serve(mode)
            try:
                boxes = samlanes.call_sam("sentinel-b", _tiny_png(),
                                          base_url=self._url(srv))
            finally:
                _stop(srv, thr)
            self.assertEqual(boxes, CANNED_LANES_SORTED, mode)

    def test_explicit_bearer_wins_over_env(self):
        os.environ["MODEL_API_KEY"] = "sentinel-env-key"
        srv, thr = _serve("ok")
        try:
            samlanes.call_sam("sentinel-explicit", _tiny_png(),
                              base_url=self._url(srv))
        finally:
            auth = srv.last_auth
            _stop(srv, thr)
        self.assertEqual(auth, "Bearer sentinel-explicit")

    def test_bearer_none_falls_back_to_env(self):
        os.environ["META_API_KEY"] = "sentinel-meta-only"
        srv, thr = _serve("ok")
        try:
            boxes = samlanes.call_sam(None, _tiny_png(),
                                      base_url=self._url(srv))
        finally:
            auth = srv.last_auth
            _stop(srv, thr)
        self.assertEqual(auth, "Bearer sentinel-meta-only")
        self.assertEqual(boxes, CANNED_LANES_SORTED)

    def test_env_precedence_matches_loop(self):
        os.environ["SPARK_API_KEY"] = "sentinel-spark"
        os.environ["META_API_KEY"] = "sentinel-meta"
        os.environ["MODEL_API_KEY"] = "sentinel-model"
        srv, thr = _serve("ok")
        try:
            samlanes.call_sam("", _tiny_png(), base_url=self._url(srv))
        finally:
            auth = srv.last_auth
            _stop(srv, thr)
        self.assertEqual(auth, "Bearer sentinel-model")

    def test_missing_key_fails_loud_without_network(self):
        with self.assertRaises(SamError) as ctx:
            samlanes.call_sam("", _tiny_png(),
                              base_url="http://127.0.0.1:1/none")
        self.assertIn("missing", str(ctx.exception).lower())

    def test_sam_base_url_resolution(self):
        self.assertEqual(samlanes.sam_base_url(),
                         "https://api.meta.ai/v1/chat/completions")
        os.environ["SPARK_API_URL"] = "https://example.invalid/base/"
        self.assertEqual(samlanes.sam_base_url(),
                         "https://example.invalid/base/v1/chat/completions")
        os.environ["SPARK_API_URL"] = \
            "https://example.invalid/v1/chat/completions"
        self.assertEqual(samlanes.sam_base_url(),
                         "https://example.invalid/v1/chat/completions")


class TestCallSamLoudErrors(_EnvHygiene):
    def _url(self, srv):
        return "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]

    def _expect_sam_error(self, mode, **kw):
        srv, thr = _serve(mode)
        try:
            with self.assertRaises(SamError):
                samlanes.call_sam("sentinel-bearer-x", _tiny_png(),
                                  base_url=self._url(srv), **kw)
        finally:
            _stop(srv, thr)

    def test_error_field(self):
        self._expect_sam_error("error_field")

    def test_non_json_body(self):
        self._expect_sam_error("badjson")

    def test_empty_content(self):
        self._expect_sam_error("empty_content")

    def test_zero_lanes(self):
        self._expect_sam_error("no_lanes")

    def test_degenerate_box(self):
        self._expect_sam_error("degenerate")

    def test_http_429_status(self):
        srv, thr = _serve("err429")
        try:
            with self.assertRaises(SamError) as ctx:
                samlanes.call_sam("sentinel-bearer-x", _tiny_png(),
                                  base_url=self._url(srv))
        finally:
            _stop(srv, thr)
        self.assertEqual(ctx.exception.status, 429)

    def test_http_500_status(self):
        srv, thr = _serve("err500")
        try:
            with self.assertRaises(SamError) as ctx:
                samlanes.call_sam("sentinel-bearer-x", _tiny_png(),
                                  base_url=self._url(srv))
        finally:
            _stop(srv, thr)
        self.assertEqual(ctx.exception.status, 500)

    def test_timeout_status_none(self):
        srv, thr = _serve("slow")
        try:
            with self.assertRaises(SamError) as ctx:
                samlanes.call_sam("sentinel-bearer-x", _tiny_png(),
                                  base_url=self._url(srv), timeout=0.2)
        finally:
            _stop(srv, thr)
        self.assertIsNone(ctx.exception.status)

    def test_key_never_echoed_in_errors(self):
        secret = "sentinel-distinct-38f2"
        srv, thr = _serve("err500")
        try:
            with self.assertRaises(SamError) as ctx:
                samlanes.call_sam(secret, _tiny_png(),
                                  base_url=self._url(srv))
        finally:
            _stop(srv, thr)
        self.assertNotIn(secret, str(ctx.exception))

    def test_empty_png_never_posts(self):
        srv, thr = _serve("ok")
        try:
            with self.assertRaises(ValueError):
                samlanes.call_sam("sentinel-b", b"",
                                  base_url=self._url(srv))
        finally:
            hit = srv.last_body
            _stop(srv, thr)
        self.assertIsNone(hit, "empty PNG must fail BEFORE any POST")


class TestParseLaneBoxes(_EnvHygiene):
    def test_sorts_top_to_bottom(self):
        self.assertEqual(
            samlanes.parse_lane_boxes({"lanes": CANNED_LANES_OOO}),
            CANNED_LANES_SORTED)

    def test_rounds_floats(self):
        boxes = samlanes.parse_lane_boxes(
            {"lanes": [{"x0": 0.4, "y0": 0.4, "x1": 9.6, "y1": 19.5}]})
        self.assertEqual(boxes, [{"x0": 0, "y0": 0, "x1": 10, "y1": 20}])
        for b in boxes:
            for v in b.values():
                self.assertIsInstance(v, int)

    def test_rejects_bad_shapes(self):
        with self.assertRaises(SamError):
            samlanes.parse_lane_boxes({"lanes": []})
        with self.assertRaises(SamError):
            samlanes.parse_lane_boxes({"nope": []})
        with self.assertRaises(SamError):
            samlanes.parse_lane_boxes("garbage")
        with self.assertRaises(SamError):
            samlanes.parse_lane_boxes(
                {"lanes": [{"x0": 0, "y0": 0, "x1": 10}]})  # missing y1
        with self.assertRaises(SamError):
            samlanes.parse_lane_boxes(
                {"lanes": [{"x0": "a", "y0": 0, "x1": 10, "y1": 10}]})
        with self.assertRaises(SamError):
            samlanes.parse_lane_boxes(
                {"lanes": [{"x0": 0, "y0": 0, "x1": 0, "y1": 10}]})
        with self.assertRaises(SamError):
            samlanes.parse_lane_boxes(
                {"lanes": [{"x0": 0, "y0": 8, "x1": 10, "y1": 2}]})
        with self.assertRaises(SamError):
            samlanes.parse_lane_boxes({"lanes": ["not-a-dict"]})

    def test_content_fences_and_blocks(self):
        fenced = "```json\n%s\n```" % json.dumps({"lanes": CANNED_LANES_OOO})
        self.assertEqual(samlanes.parse_sam_content(fenced),
                         CANNED_LANES_SORTED)
        blocks = [{"type": "text",
                   "text": json.dumps({"boxes": CANNED_LANES_OOO})}]
        self.assertEqual(samlanes.parse_sam_content(blocks),
                         CANNED_LANES_SORTED)
        with self.assertRaises(SamError):
            samlanes.parse_sam_content("   ")
        with self.assertRaises(SamError):
            samlanes.parse_sam_content("no json here at all ((( ")


class TestCropLanes(_EnvHygiene):
    def test_banded_crops_match_lane_colors(self):
        from PIL import Image
        import io
        png = _banded_png()
        boxes = [{"x0": 0, "y0": 0, "x1": 100, "y1": 20},
                 {"x0": 0, "y0": 20, "x1": 100, "y1": 40},
                 {"x0": 0, "y0": 40, "x1": 100, "y1": 60}]
        crops = samlanes.crop_lanes(png, boxes)
        self.assertEqual(len(crops), 3)
        want = [(200, 30, 30), (30, 200, 30), (30, 30, 200)]
        for raw, color in zip(crops, want):
            with Image.open(io.BytesIO(raw)) as im:
                self.assertEqual(im.size, (100, 20))
                self.assertEqual(im.getpixel((50, 10)), color)

    def test_stdlib_decoder_matches_pillow(self):
        from PIL import Image
        import io
        png = _gradient_png()
        w, h, rgb = samlanes._decode_png_rgb(png)
        with Image.open(io.BytesIO(png)) as im:
            self.assertEqual((w, h), im.size)
            self.assertEqual(rgb, im.tobytes())

    def test_png_size(self):
        self.assertEqual(samlanes.png_size(_tiny_png(8, 8)), (8, 8))
        with self.assertRaises(ValueError):
            samlanes.png_size(b"")

    def test_overflow_edges_clip(self):
        from PIL import Image
        import io
        png = _banded_png()  # 100x60
        crops = samlanes.crop_lanes(
            png, [{"x0": -10, "y0": -10, "x1": 1000, "y1": 1000}])
        with Image.open(io.BytesIO(crops[0])) as im:
            self.assertEqual(im.size, (100, 60))

    def test_loud_failures(self):
        png = _banded_png()
        with self.assertRaises(ValueError):
            samlanes.crop_lanes(png, [])
        with self.assertRaises(ValueError):
            samlanes.crop_lanes(png, ["nope"])
        with self.assertRaises(ValueError):
            samlanes.crop_lanes(
                png, [{"x0": 0, "y0": 0, "x1": 10, "y1": "high"}])
        with self.assertRaises(ValueError):  # fully outside
            samlanes.crop_lanes(
                png, [{"x0": 500, "y0": 500, "x1": 600, "y1": 600}])
        with self.assertRaises(ValueError):  # degenerate after clip
            samlanes.crop_lanes(
                png, [{"x0": 10, "y0": 10, "x1": 10, "y1": 20}])
        with self.assertRaises(ValueError):  # not a PNG
            samlanes.crop_lanes(b"junk-bytes", [{"x0": 0, "y0": 0,
                                                 "x1": 4, "y1": 4}])
        with self.assertRaises(ValueError):  # truncated PNG
            samlanes.crop_lanes(_banded_png()[:40],
                                [{"x0": 0, "y0": 0, "x1": 4, "y1": 4}])


class TestFixedGrid(_EnvHygiene):
    def test_three_strips_cover_frame(self):
        boxes = samlanes.fixed_grid_lanes(_banded_png(), 3)  # 100x60
        self.assertEqual(boxes, [{"x0": 0, "y0": 0, "x1": 100, "y1": 20},
                                 {"x0": 0, "y0": 20, "x1": 100, "y1": 40},
                                 {"x0": 0, "y0": 40, "x1": 100, "y1": 60}])
        crops = samlanes.crop_lanes(_banded_png(), boxes)
        self.assertEqual(len(crops), 3)
        for raw in crops:
            w, h = samlanes.png_size(raw)
            self.assertEqual((w, h), (100, 20))

    def test_remainder_goes_to_last_lane(self):
        boxes = samlanes.fixed_grid_lanes(_tiny_png(8, 8), 3)
        self.assertEqual([b["y0"] for b in boxes], [0, 2, 5])
        self.assertEqual(boxes[-1]["y1"], 8)

    def test_rejects_bad_counts(self):
        png = _tiny_png()
        for bad in (0, -2, True, "3", 2.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                samlanes.fixed_grid_lanes(png, bad)


class TestZeroEnvLeak(_EnvHygiene):
    def test_source_hardcodes_no_key(self):
        src_path = samlanes.__file__
        with open(src_path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(samlanes.KEY_ENV_VARS, KEY_VARS)
        for token in ("sk-", "sess-", "meta_ai_key", "BEGIN PRIVATE"):
            self.assertNotIn(token, src)
        # The only Bearer construction is the runtime header join.
        self.assertIn('"Bearer " + ', src)

    def test_no_live_default_base_in_tests(self):
        # Default base exists ONLY as a constant for the controller's
        # live fire; tests never use it (every loopback call passes an
        # explicit base_url).
        self.assertTrue(
            samlanes.DEFAULT_BASE.startswith("https://api.meta.ai"))


if __name__ == "__main__":
    unittest.main()
