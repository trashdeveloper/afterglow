"""Task 1 contract tests: Verdict comes from the oracle marker, never the exit code.

Run: python3 tests/test_verdict.py -v   (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import CaseSpec, SimBackend, Verdict, decide_verdict


class TestVerdict(unittest.TestCase):
    def test_verdict_ignores_exit_code(self):
        # A clean-looking RED log: build/sim report exit 0, no crash line,
        # but the oracle pass marker is absent -> verdict must be False.
        # NOTE: `pass` is a Python keyword, so the field is read via getattr.
        log_text = (
            "build complete, exit 0\n"
            "simulation finished, exit code 0\n"
            "cycles: 1234\n"
        )
        verdict = decide_verdict(log_text, oracle_pass="tohost: PASS")
        self.assertIs(getattr(verdict, "pass"), False)
        self.assertIsInstance(verdict.evidence, dict)

    def test_verdict_pass(self):
        # Oracle marker present -> verdict is True, regardless of exit-code text.
        log_text = (
            "simulation finished\n"
            "tohost: PASS\n"
            "cycles: 1234\n"
        )
        verdict = decide_verdict(log_text, oracle_pass="tohost: PASS")
        self.assertIs(getattr(verdict, "pass"), True)
        self.assertIsInstance(verdict.evidence, dict)


if __name__ == "__main__":
    unittest.main()
