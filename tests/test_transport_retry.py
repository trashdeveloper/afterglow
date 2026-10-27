"""Transport-flake retry tests (static, no model calls).

Run: python3 tests/test_transport_retry.py -v   (from repo root)

The live Text API flakes with empty responses (seen 3x on 2026-10-26:
SERV auto, stranger-fence-decode, rdata-latch first attempts). A
transport-level LiveAPIError must be retried with backoff+jitter inside
the attempt budget -- never instant-RED on the first flake, never
infinite, never silent.

- test_flake_then_success: raise, raise, ok -> returns ok, 2 waits.
- test_persistent_failure: raise x3 -> LiveAPIError escapes after
  exactly 3 calls and 2 waits (fails loud, bounded).
- test_immediate_success: ok -> 1 call, 0 waits.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop


class FlakeThenSuccess(unittest.TestCase):
    def test_flake_then_success(self):
        calls = []
        waits = []

        def fake_call(prompt):
            calls.append(prompt)
            if len(calls) < 3:
                raise loop.LiveAPIError("live text call returned EMPTY content")
            return {"content": "{}", "latency_ms": 61, "request_id": "r"}

        out, waits_done = loop.call_text_with_backoff(
            "prompt",
            call_fn=fake_call,
            sleep_fn=waits.append,
            rand_fn=lambda a, b: 0.0,
        )
        self.assertEqual(out["request_id"], "r")
        self.assertEqual(len(calls), 3)
        self.assertEqual(waits, waits_done)
        self.assertEqual(waits, [5.0, 15.0])

    def test_persistent_failure(self):
        calls = []
        waits = []

        def always_flake(prompt):
            calls.append(prompt)
            raise loop.LiveAPIError("live text call returned EMPTY content")

        with self.assertRaises(loop.LiveAPIError):
            loop.call_text_with_backoff(
                "prompt",
                call_fn=always_flake,
                sleep_fn=waits.append,
                rand_fn=lambda a, b: 0.0,
            )
        self.assertEqual(len(calls), 3)
        self.assertEqual(waits, [5.0, 15.0])

    def test_immediate_success(self):
        calls = []

        def fake_call(prompt):
            calls.append(prompt)
            return {"content": "{}", "latency_ms": 1, "request_id": "r0"}

        out, waits = loop.call_text_with_backoff(
            "prompt",
            call_fn=fake_call,
            sleep_fn=lambda s: (_ for _ in ()).throw(AssertionError("no sleep expected")),
            rand_fn=lambda a, b: 0.0,
        )
        self.assertEqual(out["request_id"], "r0")
        self.assertEqual(len(calls), 1)
        self.assertEqual(waits, [])


if __name__ == "__main__":
    unittest.main()
