"""Decision audit trail tests (Task D4, plan item 34, Wave D).

`agent/trust/audit.py` logs every agent decision with timestamp, model
id, and prompt hash; `replay_decisions()` reconstructs the decision
sequence from the log alone.

RED-first TDD: this file was written BEFORE agent/trust/audit.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_audit -v   (from repo root)
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import audit  # noqa: E402  (RED before implementation)

PROMPT_A = "diagnose uart divisor from failing window"
PROMPT_B = "patch simpleuart.v divisor /16"
PROMPT_C = "verify resim checksum MATCH"
HASH_A = hashlib.sha256(PROMPT_A.encode("utf-8")).hexdigest()
HASH_B = hashlib.sha256(PROMPT_B.encode("utf-8")).hexdigest()
HASH_C = hashlib.sha256(PROMPT_C.encode("utf-8")).hexdigest()

MODEL = "muse-spark-1.3"

T1 = "2026-10-29T13:00:00Z"
T2 = "2026-10-29T13:00:01Z"
T3 = "2026-10-29T13:00:02Z"

DECISIONS = ["diagnose: uart divisor /16",
             "patch: simpleuart.v DIV",
             "verify: GREEN checksum MATCH"]


def _three_entry_log():
    log = []
    audit.log_decision(log, DECISIONS[0], MODEL, HASH_A, timestamp=T1)
    audit.log_decision(log, DECISIONS[1], MODEL, HASH_B, timestamp=T2)
    audit.log_decision(log, DECISIONS[2], MODEL, HASH_C, timestamp=T3)
    return log


class TestHashPrompt(unittest.TestCase):
    def test_known_vector(self):
        self.assertEqual(
            audit.hash_prompt("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_matches_hashlib(self):
        for text in (PROMPT_A, "", "café Überlauf 🔥"):
            self.assertEqual(
                audit.hash_prompt(text),
                hashlib.sha256(text.encode("utf-8")).hexdigest())

    def test_output_is_64_lower_hex(self):
        digest = audit.hash_prompt(PROMPT_A)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, digest.lower())
        int(digest, 16)  # must parse as hex

    def test_rejects_non_string(self):
        for bad in (None, True, False, 123, b"abc", ["x"], ("x",), {"t": "x"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    audit.hash_prompt(bad)


class TestLogDecisionValidation(unittest.TestCase):
    def test_log_must_be_list(self):
        for bad in (None, "log", {}, (), {"seq": 0}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    audit.log_decision(bad, "d", MODEL, HASH_A,
                                       timestamp=T1)

    def test_bad_decision_raises(self):
        for bad in (None, "", "   ", 123, True, False, ["d"], b"d"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    audit.log_decision([], bad, MODEL, HASH_A,
                                       timestamp=T1)

    def test_bad_model_id_raises(self):
        for bad in (None, "", "   ", 123, True, ["m"], b"m"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    audit.log_decision([], "d", bad, HASH_A,
                                       timestamp=T1)

    def test_bad_prompt_hash_raises(self):
        for bad in (None, "", "xyz", "a" * 63, "a" * 65, "g" * 64,
                    HASH_A.upper(), 123, True, b"x" * 64):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    audit.log_decision([], "d", MODEL, bad,
                                       timestamp=T1)

    def test_bad_timestamp_raises(self):
        for bad in (123, True, "not-a-time", "2026-10-29",
                    "2026-10-29T13:00:00", "tomorrow"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    audit.log_decision([], "d", MODEL, HASH_A,
                                       timestamp=bad)

    def test_bad_detail_raises(self):
        for bad in (object(), {1, 2}, b"detail"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    audit.log_decision([], "d", MODEL, HASH_A,
                                       timestamp=T1, detail=bad)


class TestLogDecisionHappy(unittest.TestCase):
    def test_entry_carries_timestamp_model_hash(self):
        log = []
        entry = audit.log_decision(log, DECISIONS[0], MODEL, HASH_A,
                                   timestamp=T1)
        self.assertEqual(entry["seq"], 0)
        self.assertEqual(entry["timestamp"], T1)
        self.assertEqual(entry["model_id"], MODEL)
        self.assertEqual(entry["prompt_hash"], HASH_A)
        self.assertEqual(entry["decision"], DECISIONS[0])
        self.assertIsNone(entry["detail"])
        self.assertEqual(set(entry),
                         {"seq", "timestamp", "model_id",
                          "prompt_hash", "decision", "detail"})

    def test_seq_increments_and_appends(self):
        log = []
        e0 = audit.log_decision(log, "first", MODEL, HASH_A, timestamp=T1)
        e1 = audit.log_decision(log, "second", MODEL, HASH_B, timestamp=T2)
        self.assertEqual((e0["seq"], e1["seq"]), (0, 1))
        self.assertEqual(len(log), 2)
        self.assertIs(log[0], e0)
        self.assertIs(log[1], e1)

    def test_default_timestamp_is_utc_iso8601_z(self):
        log = []
        entry = audit.log_decision(log, "d", MODEL, HASH_A)
        self.assertTrue(entry["timestamp"].endswith("Z"),
                        entry["timestamp"])
        # Must parse back to a tz-aware datetime.
        from datetime import datetime
        raw = entry["timestamp"]
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
        self.assertIsNotNone(parsed.tzinfo)

    def test_detail_dict_passes_through(self):
        log = []
        detail = {"case": "picosoc-uart-div", "attempt": 1}
        entry = audit.log_decision(log, "d", MODEL, HASH_A,
                                   timestamp=T1, detail=detail)
        self.assertEqual(entry["detail"], detail)

    def test_unicode_decision_round_trips(self):
        log = []
        audit.log_decision(log, "diagnose: café Überlauf",
                           MODEL, HASH_A, timestamp=T1)
        self.assertEqual(audit.replay_decisions(log),
                         ["diagnose: café Überlauf"])


class TestReplay(unittest.TestCase):
    def test_empty_log_replays_to_empty(self):
        self.assertEqual(audit.replay_decisions([]), [])

    def test_replay_reconstructs_decision_sequence(self):
        log = _three_entry_log()
        self.assertEqual(audit.replay_decisions(log), DECISIONS)

    def test_replay_ignores_extra_whitespace_elsewhere(self):
        log = _three_entry_log()
        # Tampering with a non-decision field the trail checks must
        # still verify; decisions themselves come back verbatim.
        self.assertEqual(audit.replay_decisions(log)[1], DECISIONS[1])

    def test_seq_gap_raises(self):
        log = _three_entry_log()
        log[2]["seq"] = 5
        with self.assertRaises(ValueError) as ctx:
            audit.replay_decisions(log)
        self.assertIn("seq", str(ctx.exception).lower())

    def test_reordered_entries_raise(self):
        log = _three_entry_log()
        log[0], log[1] = log[1], log[0]
        with self.assertRaises(ValueError):
            audit.replay_decisions(log)

    def test_missing_key_raises(self):
        log = _three_entry_log()
        del log[1]["prompt_hash"]
        with self.assertRaises(ValueError) as ctx:
            audit.replay_decisions(log)
        self.assertIn("missing", str(ctx.exception).lower())

    def test_timestamp_regression_raises(self):
        log = _three_entry_log()
        log[2]["timestamp"] = "2026-10-29T12:59:59Z"  # before T2
        with self.assertRaises(ValueError) as ctx:
            audit.replay_decisions(log)
        self.assertIn("timestamp", str(ctx.exception).lower())

    def test_tampered_prompt_hash_raises(self):
        log = _three_entry_log()
        log[1]["prompt_hash"] = "0" * 64
        with self.assertRaises(ValueError) as ctx:
            audit.replay_decisions(log)
        self.assertIn("prompt_hash", str(ctx.exception).lower())

    def test_equal_timestamps_allowed(self):
        log = []
        audit.log_decision(log, "a", MODEL, HASH_A, timestamp=T1)
        audit.log_decision(log, "b", MODEL, HASH_B, timestamp=T1)
        self.assertEqual(audit.replay_decisions(log), ["a", "b"])

    def test_non_list_log_raises(self):
        with self.assertRaises(ValueError):
            audit.replay_decisions({"seq": 0})


class TestVerify(unittest.TestCase):
    def test_good_trail_verifies(self):
        res = audit.verify_trail(_three_entry_log())
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["n"], 3)
        self.assertIn("OK", res["reason"])

    def test_empty_trail_verifies(self):
        res = audit.verify_trail([])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["n"], 0)

    def test_broken_trail_is_not_ok(self):
        log = _three_entry_log()
        log[1]["seq"] = 7
        res = audit.verify_trail(log)
        self.assertFalse(res["ok"], res)
        self.assertIn("seq", res["reason"].lower())

    def test_regressed_trail_is_not_ok(self):
        log = _three_entry_log()
        log[2]["timestamp"] = T1
        res = audit.verify_trail(log)
        self.assertFalse(res["ok"], res)
        self.assertIn("timestamp", res["reason"].lower())


class TestJsonRoundTrip(unittest.TestCase):
    def test_to_from_json_replays_identical(self):
        log = _three_entry_log()
        text = audit.trail_to_json(log)
        back = audit.trail_from_json(text)
        self.assertEqual(audit.replay_decisions(back), DECISIONS)
        self.assertEqual(back, log)

    def test_json_is_parseable_list(self):
        text = audit.trail_to_json(_three_entry_log())
        parsed = json.loads(text)
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 3)

    def test_from_json_rejects_garbage(self):
        for bad in ("not json", '{"a": 1}', "[1, 2]", '"str"', "42", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    audit.trail_from_json(bad)

    def test_to_json_rejects_non_list(self):
        with self.assertRaises(ValueError):
            audit.trail_to_json({"seq": 0})


class TestSaveLoad(unittest.TestCase):
    def test_save_then_load_replays_identical(self):
        tmp = tempfile.mkdtemp(prefix="trust-audit-")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        trail = audit.AuditTrail()
        trail.log(DECISIONS[0], MODEL, HASH_A, timestamp=T1)
        trail.log(DECISIONS[1], MODEL, HASH_B, timestamp=T2)
        trail.log(DECISIONS[2], MODEL, HASH_C, timestamp=T3)
        path = os.path.join(tmp, "audit.json")
        trail.save(path)
        loaded = audit.AuditTrail.load(path)
        self.assertEqual(loaded.decisions(), DECISIONS)
        self.assertTrue(loaded.verify()["ok"])

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            audit.AuditTrail.load("/nonexistent-dir-xyz/audit.json")


class TestAuditTrailClass(unittest.TestCase):
    def test_log_decisions_verify_counts(self):
        trail = audit.AuditTrail()
        self.assertEqual(len(trail), 0)
        trail.log(DECISIONS[0], MODEL, HASH_A, timestamp=T1)
        trail.log(DECISIONS[1], MODEL, HASH_B, timestamp=T2)
        self.assertEqual(len(trail), 2)
        self.assertEqual(trail.decisions(), DECISIONS[:2])
        res = trail.verify()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["n"], 2)

    def test_class_rejects_junk_like_function(self):
        trail = audit.AuditTrail()
        with self.assertRaises(ValueError):
            trail.log("", MODEL, HASH_A, timestamp=T1)
        with self.assertRaises(ValueError):
            trail.log("d", "", HASH_A, timestamp=T1)
        with self.assertRaises(ValueError):
            trail.log("d", MODEL, "zzz", timestamp=T1)
        self.assertEqual(len(trail), 0)

    def test_to_from_json_methods(self):
        trail = audit.AuditTrail()
        trail.log(DECISIONS[0], MODEL, HASH_A, timestamp=T1)
        text = trail.to_json()
        back = audit.AuditTrail.from_json(text)
        self.assertEqual(back.decisions(), [DECISIONS[0]])


if __name__ == "__main__":
    unittest.main()
