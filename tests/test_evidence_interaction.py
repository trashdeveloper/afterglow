"""Task B7 (fault interaction graph, plan item 17, Wave B SEE MORE) tests:
agent/evidence/interaction.py.

Given a pair spec (faults-list format, see sim/targets/mut-pair-*.yaml)
plus the pair RED log and the two single-fault RED logs (caller passes
log TEXTS, this module never runs sims), determine masking order: fix
fault A first, does fault B's symptom persist?

Oracle markers: per-check ``CHECK <name>: PASS/FAIL`` lines (the B6
multisig format) plus ``tohost:`` / ``TRAP:`` markers. Returns an
ordered pair (first, second) + evidence lines, or "independent" when
neither fault masks the other, or "unknown" with a reason when the
logs are insufficient (order is never invented).

RED-first TDD: this file was written BEFORE agent/evidence/
interaction.py existed, so the first run must FAIL on the missing
module (proving the test exercises real code, not a stub).

Run: python3 tests/test_evidence_interaction.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import interaction  # noqa: E402  (RED before implementation)


def _pair_spec():
    """Pair spec mirroring sim/targets/mut-pair-01.yaml (faults-list)."""
    return {
        "mutant_id": "mut-pair-01",
        "parent_case": "picosoc-uart-div",
        "faults": [
            {"file": "picosoc/simpleuart.v", "line": 39,
             "anchor": "reg [3:0] recv_state;",
             "bug": "reg [3:1] recv_state;"},
            {"file": "picosoc/simpleuart.v", "line": 114,
             "anchor": "send_pattern <= ~0;",
             "bug": "send_pattern <= ~1;"},
        ],
    }


LOG_SINGLE_A = """\
- tb seed 42
CHECK control: FAIL ack-stuck pat=a5
CHECK data: PASS
CHECK control: FAIL
CHECK irq: PASS
tohost: FAIL 0xdead
TRAP: multisig control
CYCLES=120
"""

LOG_SINGLE_B = """\
- tb seed 42
CHECK data: FAIL pat=a5 got=00
CHECK data: FAIL
CHECK control: PASS
CHECK irq: PASS
tohost: FAIL 0xdead
TRAP: multisig data
CYCLES=118
"""

# Pair shows ONLY A's symptom: B's data failure is hidden while the
# control fault is present (A masks B -> fix A first, B persists).
LOG_PAIR_A_MASKS_B = """\
- tb seed 42
CHECK control: FAIL ack-stuck pat=a5
CHECK data: PASS
CHECK control: FAIL
CHECK irq: PASS
tohost: FAIL 0xdead
TRAP: multisig control
CYCLES=121
"""

# Pair shows ONLY B's symptom (B masks A -> fix B first).
LOG_PAIR_B_MASKS_A = """\
- tb seed 42
CHECK data: FAIL pat=a5 got=00
CHECK data: FAIL
CHECK control: PASS
CHECK irq: PASS
tohost: FAIL 0xdead
TRAP: multisig data
CYCLES=119
"""

# Pair shows BOTH distinctive symptoms -> independent faults.
LOG_PAIR_BOTH = """\
- tb seed 42
CHECK control: FAIL ack-stuck pat=a5
CHECK data: FAIL pat=a5 got=00
CHECK data: FAIL
CHECK control: FAIL
CHECK irq: PASS
tohost: FAIL 0xdead
TRAP: multisig control
TRAP: multisig data
CYCLES=122
"""

LOG_GREEN = """\
- tb seed 42
CHECK data: PASS
CHECK control: PASS
CHECK irq: PASS
tohost: PASS
CYCLES=100
"""

# Real-bundle style coarse log (runs/mutpair-v5-*/artifacts/boot.log):
# tohost/TRAP only, NO per-check CHECK lines.
LOG_COARSE_PAIR = """\
- tb_picosoc_uart seed 43
- divider 64 loopback send 0xa5
tohost: FAIL 0xdead
TRAP: uart timeout, no byte received
CYCLES=22023
"""

LOG_COARSE_SINGLE = """\
- tb_picosoc_uart seed 43
- divider 64 loopback send 0xa5
tohost: FAIL 0xdead
TRAP: uart framing error
CYCLES=19877
"""


class ParseMarkers(unittest.TestCase):
    def test_parses_check_lines_and_tohost(self):
        m = interaction.parse_log_markers(LOG_SINGLE_A)
        self.assertEqual(m["checks"]["control"], "FAIL")
        self.assertEqual(m["checks"]["data"], "PASS")
        self.assertEqual(m["tohost"], "FAIL")
        self.assertIn("control", m["failed"])
        self.assertNotIn("data", m["failed"])
        self.assertTrue(m["has_checks"])
        self.assertTrue(m["is_red"])

    def test_fail_wins_over_pass_for_same_check(self):
        m = interaction.parse_log_markers(LOG_SINGLE_A)
        # Detail "CHECK control: FAIL ..." plus summary "CHECK control:
        # FAIL": FAIL must survive even alongside other PASS lines.
        self.assertEqual(m["checks"]["control"], "FAIL")

    def test_ignores_non_verdict_check_chatter(self):
        m = interaction.parse_log_markers(
            "CHECK control: idle-ack-low ok\ntohost: PASS\n")
        self.assertNotIn("control", m["checks"])
        self.assertFalse(m["has_checks"])
        self.assertFalse(m["is_red"])

    def test_green_log_is_not_red(self):
        m = interaction.parse_log_markers(LOG_GREEN)
        self.assertFalse(m["is_red"])
        self.assertEqual(m["tohost"], "PASS")
        self.assertEqual(m["failed"], set())

    def test_coarse_log_has_markers_but_no_checks(self):
        m = interaction.parse_log_markers(LOG_COARSE_PAIR)
        self.assertFalse(m["has_checks"])
        self.assertEqual(m["tohost"], "FAIL")
        self.assertTrue(m["is_red"])

    def test_empty_text_has_no_markers(self):
        m = interaction.parse_log_markers("")
        self.assertFalse(m["has_checks"])
        self.assertIsNone(m["tohost"])
        self.assertFalse(m["is_red"])


class MaskingOrder(unittest.TestCase):
    def test_masked_pair_yields_pinned_order(self):
        spec = _pair_spec()
        res = interaction.determine_order(
            spec, LOG_PAIR_A_MASKS_B, LOG_SINGLE_A, LOG_SINGLE_B)
        self.assertEqual(res["verdict"], "ordered")
        self.assertEqual(res["first"],
                         interaction.fault_label(spec["faults"][0], 0))
        self.assertEqual(res["second"],
                         interaction.fault_label(spec["faults"][1], 1))
        self.assertTrue(res["evidence"], "ordered verdict needs evidence")

    def test_reverse_masking_orders_b_first(self):
        spec = _pair_spec()
        res = interaction.determine_order(
            spec, LOG_PAIR_B_MASKS_A, LOG_SINGLE_A, LOG_SINGLE_B)
        self.assertEqual(res["verdict"], "ordered")
        self.assertEqual(res["first"],
                         interaction.fault_label(spec["faults"][1], 1))
        self.assertEqual(res["second"],
                         interaction.fault_label(spec["faults"][0], 0))

    def test_independent_pair_when_both_symptoms_persist(self):
        res = interaction.determine_order(
            _pair_spec(), LOG_PAIR_BOTH, LOG_SINGLE_A, LOG_SINGLE_B)
        self.assertEqual(res["verdict"], "independent")
        self.assertIsNone(res["first"])
        self.assertIsNone(res["second"])
        self.assertTrue(res["evidence"])

    def test_fault_labels_cite_file_and_line(self):
        spec = _pair_spec()
        self.assertIn("simpleuart.v:39",
                      interaction.fault_label(spec["faults"][0], 0))
        self.assertIn("simpleuart.v:114",
                      interaction.fault_label(spec["faults"][1], 1))


class UnknownHonestly(unittest.TestCase):
    def test_empty_logs_yield_unknown_with_reason(self):
        for args in (
                ("", LOG_SINGLE_A, LOG_SINGLE_B),
                (LOG_PAIR_A_MASKS_B, "", LOG_SINGLE_B),
                (LOG_PAIR_A_MASKS_B, LOG_SINGLE_A, ""),
                ("", "", "")):
            with self.subTest(pair_empty=args[0] == ""):
                res = interaction.determine_order(_pair_spec(), *args)
                self.assertEqual(res["verdict"], "unknown")
                self.assertTrue(res["reason"],
                                "unknown verdict needs a reason")
                self.assertIsNone(res["first"])
                self.assertIsNone(res["second"])

    def test_coarse_logs_without_check_lines_yield_unknown(self):
        res = interaction.determine_order(
            _pair_spec(), LOG_COARSE_PAIR,
            LOG_COARSE_SINGLE, LOG_COARSE_SINGLE)
        self.assertEqual(res["verdict"], "unknown")
        self.assertIn("CHECK", res["reason"])

    def test_identical_single_symptoms_yield_unknown(self):
        # Both singles fail exactly the same checks: order unknowable,
        # never invented.
        res = interaction.determine_order(
            _pair_spec(), LOG_PAIR_BOTH, LOG_SINGLE_A, LOG_SINGLE_A)
        self.assertEqual(res["verdict"], "unknown")
        self.assertTrue(res["reason"])

    def test_pair_not_red_yields_unknown(self):
        res = interaction.determine_order(
            _pair_spec(), LOG_GREEN, LOG_SINGLE_A, LOG_SINGLE_B)
        self.assertEqual(res["verdict"], "unknown")
        self.assertTrue(res["reason"])

    def test_single_not_red_yields_unknown(self):
        res = interaction.determine_order(
            _pair_spec(), LOG_PAIR_A_MASKS_B, LOG_GREEN, LOG_SINGLE_B)
        self.assertEqual(res["verdict"], "unknown")
        self.assertTrue(res["reason"])


class LoudRefusals(unittest.TestCase):
    def test_bad_spec_refused_loudly(self):
        with self.assertRaises(ValueError):
            interaction.determine_order(
                {"faults": []}, LOG_PAIR_BOTH, LOG_SINGLE_A, LOG_SINGLE_B)
        with self.assertRaises(ValueError):
            interaction.determine_order(
                {"faults": [{"file": "a.v", "line": 1}]},
                LOG_PAIR_BOTH, LOG_SINGLE_A, LOG_SINGLE_B)
        with self.assertRaises(ValueError):
            interaction.determine_order(
                {"faults": [{"file": "a.v"}, {"file": "b.v", "line": 2}]},
                LOG_PAIR_BOTH, LOG_SINGLE_A, LOG_SINGLE_B)
        with self.assertRaises(ValueError):
            interaction.determine_order(
                {"no_faults": True},
                LOG_PAIR_BOTH, LOG_SINGLE_A, LOG_SINGLE_B)

    def test_nonstring_log_refused_loudly(self):
        with self.assertRaises(ValueError):
            interaction.determine_order(
                _pair_spec(), None, LOG_SINGLE_A, LOG_SINGLE_B)


class DefaultOffContract(unittest.TestCase):
    def test_loop_untouched(self):
        loop = os.path.join(ROOT, "agent", "loop.py")
        with open(loop, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("interaction", text,
                         "Wave B default-off: loop.py must not wire this in")

    def test_stdlib_only(self):
        src = os.path.join(ROOT, "agent", "evidence", "interaction.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        imports = [ln.strip() for ln in text.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        for ln in imports:
            self.assertRegex(
                ln, r"^(import re|from re import|import os)",
                "interaction.py must stay pure stdlib (re only): %r" % ln)
        for token in ("requests", "urllib", "socket", "api_key", "API_KEY"):
            self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
