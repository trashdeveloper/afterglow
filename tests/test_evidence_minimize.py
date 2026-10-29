"""Task B10 (counterexample minimizer, plan item 20, Wave B SEE MORE) tests:
agent/evidence/minimize.py.

A failing trace window is shrunk to minimal lines while preserving the
FAIL signature (oracle marker, e.g. ``tohost: FAIL`` or a ``TRAP:``
line): line chunks are removed delta-minimizer style as long as the
signature is still present. Returns the minimized window plus the kept
original line indices. When the signature depends on the fault line
region, that region survives (1-minimality: every kept line is
necessary).

RED-first TDD: this file was written BEFORE agent/evidence/minimize.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_evidence_minimize.py -v   (from repo root)
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import minimize  # noqa: E402  (RED before implementation)


def _trace(lines, trailing_newline=True):
    text = "\n".join(lines)
    return text + "\n" if trailing_newline else text


class SignaturePresentBasics(unittest.TestCase):
    def test_present_substring_is_true(self):
        self.assertTrue(
            minimize.signature_present("cycle 10 ok\ntohost: FAIL 0xdead\n",
                                       "tohost: FAIL"))

    def test_absent_signature_is_false(self):
        self.assertFalse(
            minimize.signature_present("cycle 10 ok\ntohost: PASS\n",
                                       "tohost: FAIL"))

    def test_oracle_markers_detected(self):
        boot = ("- tb seed 42\n"
                "TRAP: bool lut mismatch got=1 want=0\n"
                "tohost: FAIL 0xdead\n")
        self.assertTrue(minimize.signature_present(boot, "tohost: FAIL"))
        self.assertTrue(minimize.signature_present(boot, "TRAP:"))

    def test_match_is_case_sensitive(self):
        # Oracle markers are exact: lowercase must NOT match.
        self.assertFalse(
            minimize.signature_present("tohost: FAIL 0xdead\n",
                                       "tohost: fail"))
        self.assertFalse(
            minimize.signature_present("TRAP: stuck\n", "trap:"))

    def test_multiline_signature_spans_lines(self):
        text = "cycle 1 ok\nlineA\nlineB\ncycle 2 ok\n"
        self.assertTrue(
            minimize.signature_present(text, "lineA\nlineB"))
        self.assertFalse(
            minimize.signature_present(text, "lineA\ncycle 2"))


class SignaturePresentRejectsBadInput(unittest.TestCase):
    def test_non_string_text_raises(self):
        for bad in (None, 123, ["tohost: FAIL"], b"tohost: FAIL"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    minimize.signature_present(bad, "tohost: FAIL")

    def test_non_string_or_empty_signature_raises(self):
        for bad in (None, 123, ["TRAP:"], ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    minimize.signature_present("TRAP: x\n", bad)


class MinimizeValidation(unittest.TestCase):
    def test_bad_input_raises(self):
        with self.assertRaises(ValueError):
            minimize.minimize_trace(None, "tohost: FAIL")
        with self.assertRaises(ValueError):
            minimize.minimize_trace("tohost: FAIL\n", "")
        with self.assertRaises(ValueError):
            minimize.minimize_trace("tohost: FAIL\n", None)
        with self.assertRaises(ValueError):
            minimize.minimize_trace(123, "tohost: FAIL")

    def test_absent_signature_raises(self):
        # Minimizing a passing trace is a caller bug: fail loudly,
        # never return an empty "minimal" window as if it proved
        # anything.
        with self.assertRaises(ValueError):
            minimize.minimize_trace("cycle 1 ok\ntohost: PASS\n",
                                    "tohost: FAIL")

    def test_single_line_trace_is_already_minimal(self):
        text = "tohost: FAIL 0xdead\n"
        short, kept = minimize.minimize_trace(text, "tohost: FAIL")
        self.assertEqual(short, text)
        self.assertEqual(kept, [0])

    def test_minimized_window_matches_kept_indices(self):
        lines = ["cycle %d ok" % i for i in range(8)]
        lines[5] = "tohost: FAIL 0xdead"
        text = _trace(lines)
        short, kept = minimize.minimize_trace(text, "tohost: FAIL")
        self.assertIn("tohost: FAIL", short)
        self.assertEqual(sorted(kept), list(kept))
        self.assertEqual(short, _trace([lines[i] for i in kept]))
        for i in kept:
            self.assertGreaterEqual(i, 0)
            self.assertLess(i, len(lines))

    def test_result_is_one_minimal(self):
        # Every kept line is necessary: dropping any single kept
        # line must lose the signature.
        lines = ["cycle %d ok" % i for i in range(12)]
        lines[3] = "TRAP: misalign pc=0x00000001"
        lines[9] = "tohost: FAIL 0xdead"
        text = _trace(lines)
        short, kept = minimize.minimize_trace(text, "TRAP:")
        self.assertIn("TRAP:", short)
        kept_lines = short.splitlines()
        for drop in range(len(kept_lines)):
            reduced = "\n".join(kept_lines[:drop] + kept_lines[drop + 1:])
            self.assertFalse(
                minimize.signature_present(reduced, "TRAP:"),
                "kept line %d was removable: result not 1-minimal" % drop)

    def test_deterministic(self):
        lines = ["cycle %d ok" % i for i in range(50)]
        lines[37] = "TRAP: fault injected here"
        text = _trace(lines)
        first = minimize.minimize_trace(text, "TRAP:")
        second = minimize.minimize_trace(text, "TRAP:")
        self.assertEqual(first, second)

    def test_multiline_signature_keeps_adjacent_region(self):
        # A signature spanning two lines forces BOTH lines (and their
        # adjacency) to survive minimization.
        lines = ["cycle %d ok" % i for i in range(20)]
        lines[7] = "fault addr=0xDEAD"
        lines[8] = "TRAP: illegal instruction"
        text = _trace(lines)
        short, kept = minimize.minimize_trace(
            text, "fault addr=0xDEAD\nTRAP: illegal instruction")
        self.assertIn("TRAP: illegal instruction", short)
        self.assertIn(7, kept)
        self.assertIn(8, kept)


class FaultLineSurvives(unittest.TestCase):
    def _fault_trace(self, nlines, fault_at):
        lines = ["cycle %3d fetch ok pc=0x%08x" % (i, 0x1000 + 4 * i)
                 for i in range(nlines)]
        lines[fault_at] = ("TRAP: illegal instruction at 0xDEADBEEF "
                           "(fault injected)")
        lines.append("tohost: FAIL 0xdead")
        return lines

    def test_200_line_trace_keeps_fault_line(self):
        # The plan's acceptance test: 200-line trace with an injected
        # fault line minimizes to a window containing the fault line,
        # size-bounded (<= 40 lines).
        nlines, fault_at = 200, 137
        text = _trace(self._fault_trace(nlines, fault_at))
        short, kept = minimize.minimize_trace(text, "TRAP:")
        self.assertIn("TRAP:", short)
        self.assertIn(fault_at, kept,
                      "fault line %d was minimized away" % fault_at)
        self.assertLessEqual(len(kept), 40,
                             "minimized window not bounded: %d lines"
                             % len(kept))

    def test_fault_at_first_and_last_line(self):
        for fault_at in (0, 199):
            with self.subTest(fault_at=fault_at):
                text = _trace(self._fault_trace(200, fault_at))
                short, kept = minimize.minimize_trace(text, "TRAP:")
                self.assertIn(fault_at, kept)
                self.assertLessEqual(len(kept), 40)

    def test_unrelated_lines_are_dropped(self):
        # Signature depends ONLY on the fault line: everything else
        # must go, leaving exactly the fault line.
        nlines, fault_at = 200, 137
        text = _trace(self._fault_trace(nlines, fault_at))
        short, kept = minimize.minimize_trace(text, "0xDEADBEEF")
        self.assertEqual(kept, [fault_at])
        self.assertEqual(len(short.splitlines()), 1)


class DefaultOffContract(unittest.TestCase):
    def test_loop_untouched(self):
        loop = os.path.join(ROOT, "agent", "loop.py")
        with open(loop, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("minimize", text,
                         "Wave B default-off: loop.py must not wire this in")

    def test_stdlib_only(self):
        src = os.path.join(ROOT, "agent", "evidence", "minimize.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        imports = [ln.strip() for ln in text.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        # Pure algorithm: no imports at all is fine; anything
        # imported must be beyond-reproach stdlib with no I/O or net.
        for ln in imports:
            self.assertRegex(
                ln, r"^(import re|from re import"
                    r"|import typing|from typing import"
                    r"|from dataclasses import|import dataclasses)$",
                "minimize.py must stay pure stdlib (no I/O): %r" % ln)
        code = re.sub(r'"""(.*?)"""', "", text, flags=re.DOTALL)
        code = re.sub(r"'''(.*?)'''", "", code, flags=re.DOTALL)
        code = re.sub(r"r?(?P<q>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")",
                      "", code)
        for token in ("requests", "urllib", "socket", "api_key", "API_KEY",
                      "urlopen", "open(", "subprocess", "os."):
            self.assertNotIn(token, code)


if __name__ == "__main__":
    unittest.main()
