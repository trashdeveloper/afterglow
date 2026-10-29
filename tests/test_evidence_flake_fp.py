"""Task B8 (flake fingerprinting, plan item 18, Wave B SEE MORE) tests:
agent/evidence/flake_fp.py.

Classifies transport/API failures by signature (EMPTY vs timeout vs
truncation vs rate-limit vs unknown) from a caller-passed raw exception
string / response dump. Never calls the network; pure classification.

RED-first TDD: this file was written BEFORE agent/evidence/flake_fp.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Fixture provenance (real shapes seen in this repo's history):
- EMPTY: runs/opt-live-04/api/opt_text_response.json error verbatim
  ("live text call returned EMPTY content (latency 64027ms, ...)").
- TRUNCATION cut-off JSON: runs/opt-live-02/api/opt_text_response.json
  error verbatim ("unparseable opt output: model output is not a JSON
  object: ..."), and runs/mutbigN-v3-21 attempt-2 meta summary shape
  (model output cut inside a string, no closing brace).
- TRUNCATION finish_reason length: runs/mutbigN-v4-12/api/text_response.json
  raw dump shape ("finish_reason": "length", "salvaged": true).
- EMPTY attempts with no successful call: runs/mutpair-v8-05 and
  runs/mutbigN-v3-02 (meta: "3 transport failures ... EMPTY content").
- TIMEOUT / RATE_LIMIT: no real examples exist in runs/ history (grep
  for down/timeout and 429 is clean); fixtures are synthetic
  constructions of agent/loop.py's known format strings
  ("live text call failed (down/timeout): ...") and the standard
  429/too-many-requests payload shape, disclosed as such.

Run: python3 tests/test_evidence_flake_fp.py -v   (from repo root)
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import flake_fp  # noqa: E402  (RED before implementation)


# Real EMPTY shape: runs/opt-live-04/api/opt_text_response.json error.
EMPTY_REAL = (
    "live text call returned EMPTY content (latency 64027ms, "
    "request chatcmpl-01a17b44-6677-700c-98e8-9e218a945770); "
    "refusing to invent a diagnosis"
)

# Empty-choices shape: response dump with an empty choices array.
EMPTY_CHOICES = (
    '{"id": "chatcmpl-empty-choices-01", "choices": [], '
    '"model": "muse-spark-1.3-contributor"}'
)

# Reasoning-only shape (no usable content, per agent/loop.py text_call).
EMPTY_REASONING_ONLY = (
    "live text call returned reasoning-only with empty content; "
    "max_tokens=800 was insufficient or model refused"
)

# Synthetic timeout in agent/loop.py's down/timeout format (no real
# timeout string exists in runs/ history; format is pinned by code).
TIMEOUT_DOWN = (
    "live text call failed (down/timeout): "
    "TimeoutError('timed out')"
)

# Real timeout shape from agent/evidence/vcd_slice.py.
TIMEOUT_TRACE_RUN = "trace run timed out after 300s"

# Truncation shape modeled on runs/mutbigN-v4-12/api/text_response.json:
# max_tokens cut, finish_reason length, salvage repaired it.
TRUNC_LENGTH = (
    '{"id": "chatcmpl-01a17df0-5694-727b-aabd-5b2ed9a0bbb0", '
    '"choices": [{"finish_reason": "length", "index": 0, '
    '"message": {"content": "{\\"diagnosis\\": \\"Loopback sent 0xa5 '
    'but RX got 0xff (idle) want 0xa5 per TRAP. TX ser_tx stayed at 1 '
    'from send_pattern ~0 during dummy burst be"}}], '
    '"salvaged": true}'
)

# Real cut-off JSON shape: runs/opt-live-02/api/opt_text_response.json.
TRUNC_CUTOFF = (
    "unparseable opt output: model output is not a JSON object: "
    "'{\"diagnosis\": \"The'"
)

# Real v3-21 attempt-2 shape (meta summary): cut inside a string.
TRUNC_V321 = (
    "unparseable model output: model output is not a JSON object: "
    "'{\"diagnosis\": \"CONE_SLICE is empty so trace wins. 256-word "
    "NOP fetch should progress with no trap, but trace shows "
    "`TRAP: cpu trap asserted` and `tohost: FAIL 0xdead` at "
    "CYCLES=2009. Excerpt picorv32.'"
)

# Synthetic 429 shape (no 429 exists in runs/ history; standard payload).
RATE_LIMIT_429 = (
    'live text API error: {"code": 429, '
    '"message": "Rate limit exceeded, too many requests, retry later"}'
)

GARBAGE = "tohost: PASS all green, 2009 cycles, checksum MATCH deadbeef"


class OutputShape(unittest.TestCase):
    def test_result_has_exact_keys(self):
        res = flake_fp.classify_flake(EMPTY_REAL)
        self.assertEqual(set(res.keys()),
                         {"kind", "signature", "retryable", "suggested_wait_s"})

    def test_kinds_come_from_pinned_set(self):
        for raw in (EMPTY_REAL, TIMEOUT_DOWN, TRUNC_CUTOFF,
                    RATE_LIMIT_429, GARBAGE):
            self.assertIn(flake_fp.classify_flake(raw)["kind"],
                          {"empty", "timeout", "truncation",
                           "rate_limit", "unknown"})

    def test_wait_is_nonnegative_number(self):
        for raw in (EMPTY_REAL, TIMEOUT_DOWN, TRUNC_CUTOFF,
                    RATE_LIMIT_429, GARBAGE):
            wait = flake_fp.classify_flake(raw)["suggested_wait_s"]
            self.assertIsInstance(wait, (int, float))
            self.assertGreaterEqual(wait, 0)


class EmptyKind(unittest.TestCase):
    def test_empty_content_error_is_empty(self):
        res = flake_fp.classify_flake(EMPTY_REAL)
        self.assertEqual(res["kind"], "empty")
        self.assertTrue(res["retryable"])
        self.assertIn("EMPTY content", res["signature"])

    def test_empty_choices_array_is_empty(self):
        res = flake_fp.classify_flake(EMPTY_CHOICES)
        self.assertEqual(res["kind"], "empty")
        self.assertTrue(res["retryable"])

    def test_reasoning_only_is_empty(self):
        res = flake_fp.classify_flake(EMPTY_REASONING_ONLY)
        self.assertEqual(res["kind"], "empty")
        self.assertTrue(res["retryable"])

    def test_empty_wait_pinned(self):
        self.assertEqual(
            flake_fp.classify_flake(EMPTY_REAL)["suggested_wait_s"], 5.0)


class TimeoutKind(unittest.TestCase):
    def test_down_timeout_is_timeout(self):
        res = flake_fp.classify_flake(TIMEOUT_DOWN)
        self.assertEqual(res["kind"], "timeout")
        self.assertTrue(res["retryable"])
        self.assertTrue(res["signature"])

    def test_timed_out_trace_run_is_timeout(self):
        res = flake_fp.classify_flake(TIMEOUT_TRACE_RUN)
        self.assertEqual(res["kind"], "timeout")
        self.assertTrue(res["retryable"])

    def test_timeout_wait_pinned(self):
        self.assertEqual(
            flake_fp.classify_flake(TIMEOUT_DOWN)["suggested_wait_s"], 15.0)


class TruncationKind(unittest.TestCase):
    def test_finish_reason_length_is_truncation(self):
        res = flake_fp.classify_flake(TRUNC_LENGTH)
        self.assertEqual(res["kind"], "truncation")
        self.assertTrue(res["retryable"])
        self.assertTrue(res["signature"])

    def test_cut_off_json_is_truncation(self):
        res = flake_fp.classify_flake(TRUNC_CUTOFF)
        self.assertEqual(res["kind"], "truncation")
        self.assertTrue(res["retryable"])

    def test_v321_cut_inside_string_is_truncation(self):
        res = flake_fp.classify_flake(TRUNC_V321)
        self.assertEqual(res["kind"], "truncation")
        self.assertTrue(res["retryable"])

    def test_truncation_wait_pinned(self):
        self.assertEqual(
            flake_fp.classify_flake(TRUNC_CUTOFF)["suggested_wait_s"], 0.0)


class RateLimitKind(unittest.TestCase):
    def test_429_is_rate_limit(self):
        res = flake_fp.classify_flake(RATE_LIMIT_429)
        self.assertEqual(res["kind"], "rate_limit")
        self.assertTrue(res["retryable"])
        self.assertTrue(res["signature"])

    def test_rate_limit_wait_pinned(self):
        self.assertEqual(
            flake_fp.classify_flake(RATE_LIMIT_429)["suggested_wait_s"], 60.0)


class UnknownHonestly(unittest.TestCase):
    def test_garbage_yields_unknown_not_retryable(self):
        res = flake_fp.classify_flake(GARBAGE)
        self.assertEqual(res["kind"], "unknown")
        self.assertFalse(res["retryable"])
        self.assertEqual(res["suggested_wait_s"], 0.0)
        self.assertEqual(res["signature"], "")

    def test_empty_string_is_garbage_unknown(self):
        for raw in ("", "   "):
            res = flake_fp.classify_flake(raw)
            self.assertEqual(res["kind"], "unknown")
            self.assertFalse(res["retryable"])

    def test_no_successful_call_wrapper_alone_is_unknown(self):
        # runs/mutpair-v8-05/api/text_response.json on its own says why
        # nothing succeeded but not WHY it failed: refusing to invent
        # a signature is the honest answer here.
        res = flake_fp.classify_flake(
            '{"error": "no successful text call", "attempts": []}')
        self.assertEqual(res["kind"], "unknown")
        self.assertFalse(res["retryable"])

    def test_nonstring_refused_loudly(self):
        for bad in (None, 123, b"bytes", {"error": "x"}, ["x"]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    flake_fp.classify_flake(bad)


class PriorityOrder(unittest.TestCase):
    def test_rate_limit_beats_timeout_markers(self):
        res = flake_fp.classify_flake(
            "live text call failed (down/timeout) after 429 too many "
            "requests; backing off")
        self.assertEqual(res["kind"], "rate_limit")


class DefaultOffContract(unittest.TestCase):
    def test_loop_untouched(self):
        loop = os.path.join(ROOT, "agent", "loop.py")
        with open(loop, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("flake_fp", text,
                         "Wave B default-off: loop.py must not wire this in")

    def test_stdlib_only(self):
        src = os.path.join(ROOT, "agent", "evidence", "flake_fp.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        imports = [ln.strip() for ln in text.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        for ln in imports:
            self.assertRegex(
                ln, r"^(import re|from re import)",
                "flake_fp.py must stay pure stdlib (re only): %r" % ln)
        # Network-token scan is string-literal-aware: classification
        # MARKERS legitimately contain words like "requests" (in
        # "too many requests") and "socket" (in "socket.timeout") as
        # match data, not behavior. Strip string literals first, then
        # demand no network I/O remains in the code itself.
        code = re.sub(r'"""(.*?)"""', "", text, flags=re.DOTALL)
        code = re.sub(r"'''(.*?)'''", "", code, flags=re.DOTALL)
        code = re.sub(r"r?(?P<q>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")",
                      "", code)
        for token in ("requests", "urllib", "socket", "api_key", "API_KEY",
                      "urlopen"):
            self.assertNotIn(token, code)


if __name__ == "__main__":
    unittest.main()
