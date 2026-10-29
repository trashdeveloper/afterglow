"""Task A6 (Assertion synthesizer) red-first tests: agent/discover/assertion_synth.py.

- synthesize(excerpt, spec_text, call_fn) calls the text model exactly ONCE
  (call_fn injectable; defaults to loop.text_call; never live in tests).
- Properties stored in a pinned file format (one block per property with
  name, signal list, SVA text); malformed files raise loudly.
- bound_check evaluates each property against per-mutant signal traces
  with a lightweight evaluator (subset documented in the module docstring);
  at least one property must be falsified by a known mutant fixture.

Run: python3 -m unittest tests.test_discover_assertion_synth -v (from repo root)
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from discover import assertion_synth  # noqa: E402  (RED before implementation: ModuleNotFoundError)


EXCERPT = """always @(posedge clk) begin
    if (send_divcnt > cfg_divider && send_bitcnt) begin
        send_pattern <= {1'b1, send_pattern[9:1]};
        send_bitcnt <= send_bitcnt - 1;
        send_divcnt <= 0;
    end
end
assign ser_tx = send_pattern[0];"""

SPEC_TEXT = ("simpleuart TX: while send_bitcnt is nonzero the 10-bit "
             "send_pattern shifts out LSB-first on ser_tx; when idle "
             "(send_bitcnt == 0) ser_tx must be high.")

MODEL_JSON = json.dumps({
    "properties": [
        {"name": "tx_idle_high",
         "signals": ["send_bitcnt", "ser_tx"],
         "sva": ("assert property (@(posedge clk) "
                 "(send_bitcnt == 0) |-> (ser_tx == 1));")},
        {"name": "tx_busy_eventually_idle",
         "signals": ["send_bitcnt"],
         "sva": ("assert property (@(posedge clk) "
                 "(send_bitcnt != 0) |-> ##[1:100] (send_bitcnt == 0));")},
    ]
})


def _stub_call_factory(payload, counter):
    def _stub(prompt):
        counter["calls"] += 1
        counter["prompt"] = prompt
        return {"content": payload, "model": "stub",
                "request_id": "stub-1", "latency_ms": 1, "usage": {}}
    return _stub


def _clean_trace():
    # 6 cycles: idle (bitcnt 0, tx 1), busy (bitcnt>0), back to idle.
    return {
        "send_bitcnt": [0, 10, 9, 8, 0, 0],
        "ser_tx": [1, 0, 1, 0, 1, 1],
    }


def _stuck_tx_mutant_trace():
    # KNOWN mutant shape: ser_tx stuck low even while idle (TX driver bug).
    return {
        "send_bitcnt": [0, 10, 9, 8, 0, 0],
        "ser_tx": [1, 0, 1, 0, 0, 0],
    }


class TestSynthesize(unittest.TestCase):
    def test_prompt_contains_excerpt_and_spec(self):
        prompt = assertion_synth.build_prompt(EXCERPT, SPEC_TEXT)
        self.assertIn("send_divcnt", prompt)
        self.assertIn("ser_tx must be high", prompt)
        self.assertIn("JSON", prompt)

    def test_synthesize_calls_model_exactly_once_and_parses(self):
        counter = {"calls": 0, "prompt": ""}
        props = assertion_synth.synthesize(
            EXCERPT, SPEC_TEXT,
            call_fn=_stub_call_factory(MODEL_JSON, counter))
        self.assertEqual(counter["calls"], 1)
        self.assertIn("send_divcnt", counter["prompt"])
        self.assertEqual(len(props), 2)
        self.assertEqual(props[0]["name"], "tx_idle_high")
        self.assertEqual(props[0]["signals"], ["send_bitcnt", "ser_tx"])
        self.assertIn("assert property", props[0]["sva"])

    def test_synthesize_empty_model_output_raises_loudly(self):
        counter = {"calls": 0, "prompt": ""}
        with self.assertRaises(ValueError):
            assertion_synth.synthesize(
                EXCERPT, SPEC_TEXT,
                call_fn=_stub_call_factory("   ", counter))
        self.assertEqual(counter["calls"], 1)

    def test_synthesize_garbage_model_output_raises_loudly(self):
        counter = {"calls": 0, "prompt": ""}
        with self.assertRaises(ValueError):
            assertion_synth.synthesize(
                EXCERPT, SPEC_TEXT,
                call_fn=_stub_call_factory("not json at all {{{", counter))

    def test_synthesize_empty_excerpt_raises(self):
        counter = {"calls": 0, "prompt": ""}
        with self.assertRaises(ValueError):
            assertion_synth.synthesize(
                "", SPEC_TEXT,
                call_fn=_stub_call_factory(MODEL_JSON, counter))
        self.assertEqual(counter["calls"], 0)


class TestPropertiesFile(unittest.TestCase):
    def test_write_parse_roundtrip_pinned_format(self):
        props = json.loads(MODEL_JSON)["properties"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "uart.properties")
            assertion_synth.write_properties_file(path, props)
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
            # Pinned format: one block per property with name, signals, sva.
            self.assertIn("property tx_idle_high", raw)
            self.assertIn("signals: send_bitcnt, ser_tx", raw)
            self.assertIn("sva: assert property", raw)
            self.assertIn("property tx_busy_eventually_idle", raw)
            back = assertion_synth.parse_properties_file(path)
        self.assertEqual(back, props)

    def test_malformed_properties_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.properties")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("property nameless_block\nsva: assert property (1);\n")
            with self.assertRaises(ValueError):
                assertion_synth.parse_properties_file(path)

    def test_empty_properties_write_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.properties")
            with self.assertRaises(ValueError):
                assertion_synth.write_properties_file(path, [])


class TestBoundCheck(unittest.TestCase):
    def test_known_mutant_falsifies_property(self):
        props = [json.loads(MODEL_JSON)["properties"][0]]  # tx_idle_high
        result = assertion_synth.bound_check(props, {
            "clean": _clean_trace(),
            "stuck_tx": _stuck_tx_mutant_trace(),
        })
        self.assertEqual(result["tx_idle_high"]["clean"], "holds")
        self.assertEqual(result["tx_idle_high"]["stuck_tx"], "falsified")

    def test_unsupported_sva_never_claims_hold(self):
        props = [json.loads(MODEL_JSON)["properties"][1]]  # temporal ## form
        result = assertion_synth.bound_check(props, {
            "clean": _clean_trace(),
        })
        self.assertEqual(result["tx_busy_eventually_idle"]["clean"],
                         "unsupported")

    def test_unknown_signal_is_missing_never_silent_hold(self):
        props = [{"name": "ghost",
                  "signals": ["no_such_wire"],
                  "sva": ("assert property (@(posedge clk) "
                          "(no_such_wire == 1));")}]
        result = assertion_synth.bound_check(props, {"clean": _clean_trace()})
        self.assertEqual(result["ghost"]["clean"], "missing-signal")

    def test_empty_inputs_raise(self):
        with self.assertRaises(ValueError):
            assertion_synth.bound_check([], {"clean": _clean_trace()})
        with self.assertRaises(ValueError):
            assertion_synth.bound_check(
                json.loads(MODEL_JSON)["properties"][:1], {})


if __name__ == "__main__":
    unittest.main()
