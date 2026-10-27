"""Task 3 tests: triage-first loop mode (static fixtures, no model calls).

Run: python3 tests/test_triage.py -v   (from repo root)

- test_window_without_trap: TRAP-less 9-line RED window still yields a
  valid <=200-line window with the verdict attached.
- test_oversize_prompt_fails_loud: bloated trace blows the 2500-token cap
  -> collect() raises AssertionError, never silently truncates.
- test_cli_defaults: argparse default is FULL-AUTO (no flags -> live
  path, mode == "live"); --triage / --stop-before-patch select the
  old stop-before-PATCH path (mode == "triage", exit 2); --auto is
  accepted for compatibility and also means live. Pure argparse/mode
  constants, no live API.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import collect
from agent import loop
from afterglow_core import decide_verdict


def _nine_line_red():
    lines = [
        "cycle 0: reset assert",
        "cycle 1: fetch pc=0x00",
        "cycle 2: fetch pc=0x04",
        "cycle 3: mem request addr=0x10",
        "cycle 4: rdata=0xdead",
        "cycle 5: decode stall",
        "cycle 6: tohost: FAIL 0x1",
        "cycle 7: CYCLES=1234",
        "cycle 8: sim done",
    ]
    return "\n".join(lines) + "\n"


def _verdict_json():
    return (
        '{"case": "dummy", "pass": false, "tohost": "FAIL 0x1", '
        '"checksum": "abc123", "cycles": 1234, "seed": 42}'
    )


class TestTriage(unittest.TestCase):
    def test_window_without_trap(self):
        # TRAP-less RED fixture: collector must still center a valid
        # <=200-line window with the verdict attached (no silent drop).
        boot_log = _nine_line_red()
        self.assertNotIn("TRAP:", boot_log)
        verdict_json = _verdict_json()
        bundle = collect.collect(boot_log, verdict_json, "TEMPLATE {{TRACE}} {{VERDICT}}")
        counts = bundle["counts"]
        self.assertLessEqual(counts["trace_lines"], 200)
        self.assertEqual(counts["trace_lines"], 9)
        self.assertEqual(counts["boot_lines"], 9)
        self.assertLessEqual(counts["prompt_tokens"], 2500)
        # Verdict attached: builder verdict text lands in the full prompt.
        self.assertIn("FAIL 0x1", bundle["full_prompt"])
        self.assertIn("tohost: FAIL 0x1", bundle["trace_block"])
        # Same log is RED under the oracle marker (exit-code independent).
        # NOTE: `pass` is read via getattr (keyword, see types.Verdict).
        verdict = decide_verdict(boot_log, oracle_pass="tohost: PASS")
        self.assertIs(getattr(verdict, "pass"), False)

    def test_oversize_prompt_fails_loud(self):
        # Bloated trace blows the 2500-token cap -> hard AssertionError,
        # never a silently truncated prompt.
        big_lines = [" ".join(["word%d" % i for i in range(20)]) for _ in range(400)]
        boot_log = "\n".join(big_lines) + "\n"
        verdict_json = _verdict_json()
        with self.assertRaises(AssertionError):
            collect.collect(boot_log, verdict_json, "TEMPLATE {{TRACE}} {{VERDICT}}")

    def test_cli_defaults(self):
        # FULL-AUTO BY DEFAULT: no flags -> live path (patch attempted);
        # --triage / --stop-before-patch select the old withhold path.
        # --auto is accepted for compatibility and also means live.
        # Pure argparse + mode constants: no model calls, no subprocess.
        args_default = loop.parse_args(["--case", "dummy"])
        self.assertIs(args_default.auto, False)
        self.assertTrue(hasattr(args_default, "triage"))
        self.assertEqual(loop.resolve_mode(args_default), "live")
        self.assertIs(loop.should_withhold_patch(args_default), False)
        self.assertEqual(loop.MODE_LIVE, "live")
        self.assertEqual(loop.EXIT_TRIAGE, 2)

        args_auto = loop.parse_args(["--case", "dummy", "--auto"])
        self.assertIs(args_auto.auto, True)
        self.assertEqual(loop.resolve_mode(args_auto), "live")
        self.assertIs(loop.should_withhold_patch(args_auto), False)

        args_triage = loop.parse_args(["--case", "dummy", "--triage"])
        self.assertEqual(loop.resolve_mode(args_triage), "triage")
        self.assertIs(loop.should_withhold_patch(args_triage), True)
        self.assertEqual(loop.MODE_TRIAGE, "triage")

        args_stop = loop.parse_args(
            ["--case", "dummy", "--stop-before-patch"])
        self.assertEqual(loop.resolve_mode(args_stop), "triage")
        self.assertIs(loop.should_withhold_patch(args_stop), True)

        # Ambiguity fails closed: --auto + --triage withholds the patch.
        args_both = loop.parse_args(
            ["--case", "dummy", "--auto", "--triage"])
        self.assertEqual(loop.resolve_mode(args_both), "triage")
        self.assertIs(loop.should_withhold_patch(args_both), True)


if __name__ == "__main__":
    unittest.main()
