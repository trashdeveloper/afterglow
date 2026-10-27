"""Context-pack v2: failure fingerprint tests (static fixtures, no network).

Run: python3 tests/test_fingerprint.py -v   (from repo root)

Fingerprint inputs (parent spec): trap line + oracle mismatch signature
+ failing signal cone names -> one deterministic hex string.

- test_normalize_erases_volatile: cycle counters / hex case / whitespace
  runs collapse to the same normalized form.
- test_same_bug_same_fp: two traces differing only in volatile fields
  produce the SAME fingerprint.
- test_different_trap_different_fp: different anchor lines do NOT collide.
- test_signal_order_invariant: signal list order does not change the fp.
- test_golden_hash: one hand-computed sha256 golden value pins the scheme.
- test_for_failure_record: convenience record keeps verbatim trap line
  alongside the normalized hash (anti-collision rule).
"""
import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import fingerprint as fp


TRACE_A = """- tb seed 42
cycle 1234 fetch ok pc=0x1A2B
tohost: FAIL 0xdead
TRAP: uart data mismatch got 0xE0 want 0xA5
CYCLES=2112
"""

TRACE_B = """- tb seed 42
cycle 98765 fetch ok pc=0x1a2b
tohost:   FAIL   0xDEAD
TRAP: uart data mismatch got 0xe0 want 0xa5
CYCLES=9999
"""

TRACE_OTHER_TRAP = """- tb seed 42
cycle 1234 fetch ok pc=0x1A2B
tohost: FAIL 0xdead
TRAP: fetch misalign addr 0x00000002
CYCLES=2112
"""

VERDICT_TXT = '{"case": "picosoc-uart-div", "pass": false, "tohost": "FAIL 0xdead"}'


class Normalize(unittest.TestCase):
    def test_normalize_erases_volatile(self):
        self.assertEqual(fp.normalize_trace(TRACE_A), fp.normalize_trace(TRACE_B))

    def test_normalize_keeps_anchor_words(self):
        norm = fp.normalize_trace(TRACE_A)
        self.assertIn("trap:", norm)
        self.assertIn("uart", norm)


class FingerprintDeterminism(unittest.TestCase):
    def test_same_bug_same_fp(self):
        rec_a = fp.fingerprint_for_failure(TRACE_A, VERDICT_TXT)
        rec_b = fp.fingerprint_for_failure(TRACE_B, VERDICT_TXT)
        self.assertEqual(rec_a["fingerprint"], rec_b["fingerprint"])

    def test_different_trap_different_fp(self):
        rec_a = fp.fingerprint_for_failure(TRACE_A, VERDICT_TXT)
        rec_c = fp.fingerprint_for_failure(TRACE_OTHER_TRAP, VERDICT_TXT)
        self.assertNotEqual(rec_a["fingerprint"], rec_c["fingerprint"])

    def test_signal_order_invariant(self):
        h1 = fp.fingerprint("TRAP: x got 0xe0", "FAIL 0xdead", ["rdata", "recv_divcnt"])
        h2 = fp.fingerprint("TRAP: x got 0xe0", "FAIL 0xdead", ["recv_divcnt", "rdata"])
        self.assertEqual(h1, h2)

    def test_golden_hash(self):
        # Hand-computed pin of the exact hash scheme (catches silent changes).
        trap = "TRAP: uart data mismatch got 0xe0 want 0xa5"
        oracle = "FAIL 0xdead"
        signals = ["recv_divcnt", "rdata"]
        norm_trap = fp.normalize_text(trap)
        norm_oracle = fp.normalize_text(oracle)
        joined = "|".join(sorted(s.lower() for s in signals))
        expected = hashlib.sha256(
            ("|".join([norm_trap, trap, norm_oracle, joined])).encode("utf-8")
        ).hexdigest()
        self.assertEqual(fp.fingerprint(trap, oracle, signals), expected)
        self.assertEqual(len(expected), 64)

    def test_for_failure_record(self):
        rec = fp.fingerprint_for_failure(TRACE_A, VERDICT_TXT)
        # Verbatim anchor kept alongside the hash (anti-collision rule).
        self.assertIn("trap_line", rec)
        self.assertIn("TRAP:", rec["trap_line"])
        self.assertIn("oracle_sig", rec)
        self.assertIn("signals", rec)
        self.assertTrue(rec["signals"])
        self.assertEqual(len(rec["fingerprint"]), 64)


if __name__ == "__main__":
    unittest.main()
