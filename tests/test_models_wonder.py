"""Second-model diagnosis client tests (muse-wonder-1.0).

Run: python3 tests/test_models_wonder.py -v   (from repo root)

Covers agent/models/wonder.py + the loop.py --wonder routing:
- build_request: exact JSON body shape (model/messages/max_tokens);
  default model is muse-wonder-1.0.
- parse_response: strict validation -- raises loudly on missing
  choices/message/content, empty content, non-JSON input.
- call_endpoint: stdlib-urllib POST against a LOCAL throwaway
  http.server (real HTTP over loopback, no mocks); maps HTTP 429/500
  to WonderError with .status, and timeouts to WonderError.
- api_key/base_url: same key envs (MODEL_API_KEY/SPARK_API_KEY/
  META_API_KEY) and same base env (SPARK_API_URL) as the Spark path.
- attempt routing (injected transport, no network): attempt 1 = Spark,
  attempts 2+ = Wonder when --wonder is on; all-Spark when OFF.
- wire shape of loop.wonder_text_call: real local HTTP round-trip
  with SPARK_API_URL pointed at loopback; asserts the body carries
  muse-wonder-1.0 and usage (incl. reasoning_tokens) is preserved.
- live test: hits the real hosted endpoint ONLY when MODEL_API_KEY
  is set; otherwise skips loudly. NEVER commit a key, never fake a
  response. The controller fires the ablation; tests never do.
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop
from agent.models import wonder


def _good_body(text="wonder diag: ok", req_id="req-wonder-1"):
    return {
        "id": req_id,
        "model": "muse-wonder-1.0",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                  "reasoning_tokens": 7},
    }


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

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
        body = wonder.build_request(
            "trace window here", model="muse-wonder-1.0", max_tokens=512
        )
        self.assertEqual(body["model"], "muse-wonder-1.0")
        self.assertEqual(body["max_tokens"], 512)
        self.assertIsInstance(body["messages"], list)
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"], "trace window here")
        json.dumps(body)

    def test_defaults(self):
        body = wonder.build_request("hi")
        self.assertEqual(body["model"], wonder.DEFAULT_MODEL)
        self.assertEqual(body["model"], "muse-wonder-1.0")
        self.assertIn("max_tokens", body)
        self.assertGreater(body["max_tokens"], 0)

    def test_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            wonder.build_request("   ")
        with self.assertRaises(ValueError):
            wonder.build_request("", max_tokens=10)

    def test_rejects_bad_model_and_tokens(self):
        with self.assertRaises(ValueError):
            wonder.build_request("p", model="  ")
        with self.assertRaises(ValueError):
            wonder.build_request("p", max_tokens=0)


class TestParseResponse(unittest.TestCase):
    def test_valid_returns_text(self):
        self.assertEqual(wonder.parse_response(_good_body("hello")), "hello")

    def test_str_and_bytes_inputs(self):
        self.assertEqual(
            wonder.parse_response(json.dumps(_good_body("s"))), "s")
        self.assertEqual(
            wonder.parse_response(json.dumps(_good_body("b")).encode()), "b")

    def test_content_blocks_joined(self):
        body = _good_body()
        body["choices"][0]["message"]["content"] = [
            {"text": "ab"}, {"text": "cd"}, {"n": 1}]
        self.assertEqual(wonder.parse_response(body), "abcd")

    def test_missing_choices_raises(self):
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response({"id": "x"})

    def test_empty_choices_raises(self):
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response({"choices": []})

    def test_missing_message_raises(self):
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response({"choices": [{}]})
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response({"choices": [{"message": None}]})

    def test_missing_content_raises(self):
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response({"choices": [{"message": {"role": "assistant"}}]})

    def test_empty_content_raises(self):
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response({"choices": [{"message": {"content": "  "}}]})

    def test_non_json_raises(self):
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response("not json{{")
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response(None)
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response([1, 2, 3])

    def test_error_payload_raises(self):
        with self.assertRaises(wonder.WonderError):
            wonder.parse_response({"error": {"message": "bad key"}})


class TestCallEndpoint(unittest.TestCase):
    def _url(self, srv):
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def test_success_tuple_and_wire_shape(self):
        srv, thr = _serve("ok")
        try:
            body = wonder.build_request("ping", max_tokens=16)
            text, latency_ms, request_id = wonder.call_endpoint(
                "k-test", self._url(srv), body, timeout=10
            )
            self.assertEqual(text, "wonder diag: ok")
            self.assertIsInstance(latency_ms, int)
            self.assertGreaterEqual(latency_ms, 0)
            self.assertTrue(request_id)
            self.assertTrue(srv.last_path.endswith("/v1/chat/completions"), srv.last_path)
            self.assertEqual(srv.last_auth, "Bearer k-test")
            sent = json.loads(srv.last_body.decode("utf-8"))
            self.assertEqual(sent, body)
            self.assertEqual(sent["model"], "muse-wonder-1.0")
        finally:
            _stop(srv, thr)

    def test_http_429_maps_to_status_error(self):
        srv, thr = _serve("err429")
        try:
            with self.assertRaises(wonder.WonderError) as ctx:
                wonder.call_endpoint("k", self._url(srv),
                                     wonder.build_request("p"), timeout=10)
            self.assertEqual(ctx.exception.status, 429)
        finally:
            _stop(srv, thr)

    def test_http_500_maps_to_status_error(self):
        srv, thr = _serve("err500")
        try:
            with self.assertRaises(wonder.WonderError) as ctx:
                wonder.call_endpoint("k", self._url(srv),
                                     wonder.build_request("p"), timeout=10)
            self.assertEqual(ctx.exception.status, 500)
        finally:
            _stop(srv, thr)

    def test_timeout_raises_loudly(self):
        srv, thr = _serve("slow")
        try:
            with self.assertRaises(wonder.WonderError):
                wonder.call_endpoint("k", self._url(srv),
                                     wonder.build_request("p"), timeout=1)
        finally:
            _stop(srv, thr)

    def test_non_json_body_raises(self):
        srv, thr = _serve("badjson")
        try:
            with self.assertRaises(wonder.WonderError):
                wonder.call_endpoint("k", self._url(srv),
                                     wonder.build_request("p"), timeout=10)
        finally:
            _stop(srv, thr)

    def test_missing_key_and_base_raise(self):
        with self.assertRaises(wonder.WonderError):
            wonder.call_endpoint("", "http://127.0.0.1:1",
                                 wonder.build_request("p"), timeout=2)
        with self.assertRaises(wonder.WonderError):
            wonder.call_endpoint("k", "", wonder.build_request("p"), timeout=2)


class TestKeyBaseEnvs(unittest.TestCase):
    def test_missing_keys_raise_loudly(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY")}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(wonder.WonderError):
                wonder.api_key()

    def test_key_preference_order(self):
        env = {"MODEL_API_KEY": "m1", "SPARK_API_KEY": "s1",
               "META_API_KEY": "t1"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(wonder.api_key(), "m1")
        env = {"SPARK_API_KEY": "s1", "META_API_KEY": "t1"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(wonder.api_key(), "s1")

    def test_base_default_and_passthrough(self):
        env = {k: v for k, v in os.environ.items() if k != "SPARK_API_URL"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(wonder.base_url(),
                             "https://api.meta.ai/v1/chat/completions")
        with mock.patch.dict(os.environ,
                              {"SPARK_API_URL": "https://h/v1/chat/completions"}):
            self.assertEqual(wonder.base_url(), "https://h/v1/chat/completions")
        with mock.patch.dict(os.environ,
                              {"SPARK_API_URL": "https://h"}):
            self.assertEqual(wonder.base_url(), "https://h/v1/chat/completions")


def _env(content, model):
    return {"content": content, "raw": {}, "latency_ms": 1,
            "request_id": "r", "model": model, "usage": {}}


class TestAttemptRouting(unittest.TestCase):
    def test_flag_defaults_off(self):
        self.assertFalse(loop.parse_args([]).wonder)
        self.assertTrue(loop.parse_args(["--wonder"]).wonder)

    def test_model_constant(self):
        self.assertEqual(loop.WONDER_MODEL, "muse-wonder-1.0")
        self.assertEqual(wonder.DEFAULT_MODEL, loop.WONDER_MODEL)

    def test_predicates(self):
        off = SimpleNamespace(wonder=False)
        on = SimpleNamespace(wonder=True)
        self.assertFalse(loop.wonder_enabled(off))
        self.assertTrue(loop.wonder_enabled(on))
        self.assertFalse(loop.wonder_enabled(SimpleNamespace()))
        for n in (0, 1, -3, "x", None):
            self.assertFalse(loop.use_wonder_for_attempt(on, n))
        self.assertTrue(loop.use_wonder_for_attempt(on, 2))
        self.assertTrue(loop.use_wonder_for_attempt(on, 5))
        self.assertFalse(loop.use_wonder_for_attempt(off, 2))
        self.assertEqual(loop.diagnose_model_for_attempt(off, 3), loop.TEXT_MODEL)
        self.assertEqual(loop.diagnose_model_for_attempt(on, 1), loop.TEXT_MODEL)
        self.assertEqual(loop.diagnose_model_for_attempt(on, 2), loop.WONDER_MODEL)

    def test_callable_identity(self):
        off = SimpleNamespace(wonder=False)
        on = SimpleNamespace(wonder=True)
        self.assertIs(loop.diagnose_call_fn(off, 1), loop.text_call)
        self.assertIs(loop.diagnose_call_fn(off, 3), loop.text_call)
        self.assertIs(loop.diagnose_call_fn(on, 1), loop.text_call)
        self.assertIs(loop.diagnose_call_fn(on, 2), loop.wonder_text_call)
        self.assertIs(loop.diagnose_call_fn(on, 3), loop.wonder_text_call)

    def test_kwargs_empty_when_off(self):
        off = SimpleNamespace(wonder=False)
        on = SimpleNamespace(wonder=True)
        # Default OFF: no kwargs, so the loop calls
        # call_text_with_backoff(attempt_prompt) exactly as before.
        self.assertEqual(loop.diagnose_call_kwargs(off, 1), {})
        self.assertEqual(loop.diagnose_call_kwargs(off, 3), {})
        self.assertEqual(loop.diagnose_call_kwargs(on, 1), {})
        kw = loop.diagnose_call_kwargs(on, 2)
        self.assertIs(kw["call_fn"], loop.wonder_text_call)

    def _route(self, args, attempt_no):
        spark = lambda prompt: _env("spark-diag", loop.TEXT_MODEL)  # noqa: E731
        def boom(prompt):
            raise loop.LiveAPIError("must not fire on this attempt")
        if loop.use_wonder_for_attempt(args, attempt_no):
            fn = loop.diagnose_call_fn(args, attempt_no, spark_fn=boom,
                                        wonder_fn=lambda p: _env(
                                            "wonder-diag", loop.WONDER_MODEL))
        else:
            fn = loop.diagnose_call_fn(args, attempt_no, spark_fn=spark,
                                        wonder_fn=boom)
        call, _waits = loop.call_text_with_backoff(
            "prompt", call_fn=fn, sleep_fn=lambda s: None,
            rand_fn=lambda a, b: 0)
        return call

    def test_attempt1_is_spark_when_flag_on(self):
        call = self._route(SimpleNamespace(wonder=True), 1)
        self.assertEqual(call["model"], loop.TEXT_MODEL)
        self.assertEqual(call["content"], "spark-diag")

    def test_attempt2plus_is_wonder_when_flag_on(self):
        for n in (2, 3):
            call = self._route(SimpleNamespace(wonder=True), n)
            self.assertEqual(call["model"], loop.WONDER_MODEL)
            self.assertEqual(call["content"], "wonder-diag")

    def test_all_spark_when_flag_off(self):
        for n in (1, 2, 3):
            call = self._route(SimpleNamespace(wonder=False), n)
            self.assertEqual(call["model"], loop.TEXT_MODEL)
            self.assertEqual(call["content"], "spark-diag")


class TestWonderTextCallWire(unittest.TestCase):
    def test_body_model_and_reasoning_tokens_preserved(self):
        srv, thr = _serve("ok")
        try:
            url = "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1]
            with mock.patch.dict(os.environ, {"MODEL_API_KEY": "k-wonder-test",
                                               "SPARK_API_URL": url}):
                call = loop.wonder_text_call("trace window here", timeout=10)
            self.assertEqual(call["content"], "wonder diag: ok")
            self.assertEqual(call["model"], "muse-wonder-1.0")
            self.assertEqual(call["usage"].get("reasoning_tokens"), 7)
            sent = json.loads(srv.last_body.decode("utf-8"))
            self.assertEqual(sent["model"], "muse-wonder-1.0")
            self.assertEqual(srv.last_auth, "Bearer k-wonder-test")
            # same system prompt contract as the Spark path
            roles = [m["role"] for m in sent["messages"]]
            self.assertEqual(roles, ["system", "user"])
        finally:
            _stop(srv, thr)


@unittest.skipUnless(os.environ.get("MODEL_API_KEY"), "no MODEL_API_KEY -- live test skipped (no key committed, no response faked)")
class TestLive(unittest.TestCase):
    def test_live_chat_completions(self):
        body = wonder.build_request("Reply with exactly: ok", max_tokens=32)
        text, latency_ms, request_id = wonder.call_endpoint(
            os.environ["MODEL_API_KEY"], wonder.base_url(), body, timeout=120
        )
        self.assertTrue(text.strip(), "live endpoint returned empty content")
        print("\nlive ok: latency_ms=%d request_id=%s text=%r" % (latency_ms, request_id, text[:120]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
