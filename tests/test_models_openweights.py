"""Open-weights (Baseten-style OpenAI-compatible) client tests.

Run: python3 tests/test_models_openweights.py -v   (from repo root)

Covers agent/models/openweights.py:
- build_request: exact JSON body shape (model/messages/max_tokens).
- parse_response: strict validation -- raises loudly on missing
  choices/message/content, empty content, non-JSON input.
- call_endpoint: stdlib-urllib POST against a LOCAL throwaway
  http.server (real HTTP over loopback, no mocks); maps HTTP 429/500
  to OpenWeightsError with .status, and timeouts to OpenWeightsError.
- live test: hits the real hosted endpoint ONLY when BASETEN_API_KEY
  (and BASETEN_BASE_URL) are set; otherwise skips loudly. NEVER
  commit a key, never fake a response.
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.models import openweights


def _good_body(text="diag: ok", req_id="req-test-1"):
    return {
        "id": req_id,
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": text}}],
    }


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"  # ok | err429 | err500 | slow | badjson

    def _send(self, status, payload, ctype="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("x-request-id", "hdr-req-1")
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
        elif self.mode == "slow":
            import time as _t
            _t.sleep(2.0)
            self._send(200, _good_body())
        elif self.mode == "badjson":
            self._send(200, b"not json{{", ctype="text/plain")
        else:
            self._send(200, _good_body())

    def log_message(self, *args):
        pass


def _serve(mode):
    _Handler.mode = mode
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.last_path = None
    srv.last_auth = None
    srv.last_body = None
    thr = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01})
    thr.daemon = True
    thr.start()
    return srv, thr


def _stop(srv, thr):
    srv.shutdown()
    thr.join()
    srv.server_close()


class TestBuildRequest(unittest.TestCase):
    def test_exact_body_shape(self):
        body = openweights.build_request(
            "trace window here", model="meta-models/muse-spark-open-1026", max_tokens=512
        )
        self.assertEqual(body["model"], "meta-models/muse-spark-open-1026")
        self.assertEqual(body["max_tokens"], 512)
        self.assertIsInstance(body["messages"], list)
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"], "trace window here")
        # must be JSON-serializable -- this IS the wire body
        json.dumps(body)

    def test_defaults(self):
        body = openweights.build_request("hi")
        self.assertEqual(body["model"], openweights.DEFAULT_MODEL)
        self.assertIn("max_tokens", body)
        self.assertGreater(body["max_tokens"], 0)

    def test_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            openweights.build_request("   ")
        with self.assertRaises(ValueError):
            openweights.build_request("", max_tokens=10)


class TestParseResponse(unittest.TestCase):
    def test_valid_returns_text(self):
        self.assertEqual(parse_ok := openweights.parse_response(_good_body("hello")), "hello")
        self.assertTrue(parse_ok)

    def test_missing_choices_raises(self):
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response({"id": "x"})

    def test_empty_choices_raises(self):
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response({"choices": []})

    def test_missing_message_raises(self):
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response({"choices": [{}]})
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response({"choices": [{"message": None}]})

    def test_missing_content_raises(self):
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response({"choices": [{"message": {"role": "assistant"}}]})

    def test_empty_content_raises(self):
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response({"choices": [{"message": {"content": "  "}}]})

    def test_non_json_raises(self):
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response("not json{{")
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response(None)
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response([1, 2, 3])

    def test_error_payload_raises(self):
        with self.assertRaises(openweights.OpenWeightsError):
            openweights.parse_response({"error": {"message": "bad key"}})


class TestCallEndpoint(unittest.TestCase):
    def _url(self, srv):
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def test_success_tuple_and_wire_shape(self):
        srv, thr = _serve("ok")
        try:
            body = openweights.build_request("ping", max_tokens=16)
            text, latency_ms, request_id = openweights.call_endpoint(
                "k-test", self._url(srv), body, timeout=10
            )
            self.assertEqual(text, "diag: ok")
            self.assertIsInstance(latency_ms, int)
            self.assertGreaterEqual(latency_ms, 0)
            self.assertTrue(request_id)  # body id or x-request-id header
            self.assertTrue(srv.last_path.endswith("/v1/chat/completions"), srv.last_path)
            self.assertEqual(srv.last_auth, "Bearer k-test")
            sent = json.loads(srv.last_body.decode("utf-8"))
            self.assertEqual(sent, body)
        finally:
            _stop(srv, thr)

    def test_http_429_maps_to_status_error(self):
        srv, thr = _serve("err429")
        try:
            with self.assertRaises(openweights.OpenWeightsError) as ctx:
                openweights.call_endpoint("k", self._url(srv),
                                          openweights.build_request("p"), timeout=10)
            self.assertEqual(ctx.exception.status, 429)
        finally:
            _stop(srv, thr)

    def test_http_500_maps_to_status_error(self):
        srv, thr = _serve("err500")
        try:
            with self.assertRaises(openweights.OpenWeightsError) as ctx:
                openweights.call_endpoint("k", self._url(srv),
                                          openweights.build_request("p"), timeout=10)
            self.assertEqual(ctx.exception.status, 500)
        finally:
            _stop(srv, thr)

    def test_timeout_raises_loudly(self):
        srv, thr = _serve("slow")
        try:
            with self.assertRaises(openweights.OpenWeightsError):
                openweights.call_endpoint("k", self._url(srv),
                                          openweights.build_request("p"), timeout=1)
        finally:
            _stop(srv, thr)

    def test_non_json_body_raises(self):
        srv, thr = _serve("badjson")
        try:
            with self.assertRaises(openweights.OpenWeightsError):
                openweights.call_endpoint("k", self._url(srv),
                                          openweights.build_request("p"), timeout=10)
        finally:
            _stop(srv, thr)


@unittest.skipUnless(os.environ.get("BASETEN_API_KEY"), "no BASETEN_API_KEY -- live test skipped (no key committed, no response faked)")
class TestLive(unittest.TestCase):
    def test_live_chat_completions(self):
        base_url = os.environ.get("BASETEN_BASE_URL", "")
        if not base_url:
            self.skipTest("no BASETEN_BASE_URL -- live test skipped")
        body = openweights.build_request("Reply with exactly: ok", max_tokens=32)
        text, latency_ms, request_id = openweights.call_endpoint(
            os.environ["BASETEN_API_KEY"], base_url, body, timeout=120
        )
        self.assertTrue(text.strip(), "live endpoint returned empty content")
        print("\nlive ok: latency_ms=%d request_id=%s text=%r" % (latency_ms, request_id, text[:120]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
