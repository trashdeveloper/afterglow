"""Task G5 (auto-bisect on regression, plan item 75) tests:
agent/repair/bisectreg.py.

Interface (plan text, verbatim):

  bisect_regression(failing_entry, history) -> culprit_commit

Standalone reader over the agent/trust/regguard.py registry format
(read-only: the entry shape ``{case, checksum, ...}`` is validated,
never written) reusing the agent/discover/bisect.py honesty contract
(vacuous input raises, probe exceptions propagate, bounded probes).
``history`` is oldest -> newest commit ids; the return is the bare
first-bad commit id. No regguard/loop wiring.

Probe injection (``is_bad_fn``) is the $0 test seam: ``is_bad_fn(commit)``
returns truthy while that commit reproduces the recorded failure. The
graded log2 bound counts probe calls including the two endpoint checks.

Contract:
- Fixture history with a planted breaking commit: bisect pinpoints it
  exactly, with total probes <= 2 + ceil(log2(n-1)) (baseline + tip
  checks + binary-search steps).
- Monotonicity required: history[0] must be good and history[-1] bad.
  A good tip (vacuous: nothing reproduces) or a bad baseline (culprit
  predates the window) raises ValueError loudly -- never an invented
  culprit.
- Single-commit history: bad -> it is the culprit; good -> ValueError.
- Malformed failing_entry (non-dict, missing/blank case or checksum),
  malformed history (non-list, empty, non-string/blank commits), or a
  missing/non-callable probe raise TypeError/ValueError before any
  verdict.
- Probe exceptions propagate unchanged -- never caught, never a verdict.
- Read-only: failing_entry is never mutated and no registry file is
  touched (no regguard import at all).
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no bisectreg wiring).

RED-first TDD: this file was written BEFORE
agent/repair/bisectreg.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_bisectreg.py -v   (from repo root)
"""
import ast
import copy
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import bisectreg  # noqa: E402  (RED before implementation)


def _entry(case="picosoc-uart-div", checksum="sha256:abc123"):
    return {"case": case, "checksum": checksum, "tohost": "PASS",
            "seed": 42, "bundle": "runs/fake-green"}


def _history(n, tag="c"):
    return ["%s%04d" % (tag, i) for i in range(n)]


def _monotone_probe(culprit_idx, calls=None):
    def _probe(commit):
        if calls is not None:
            calls.append(commit)
        idx = int(commit[1:])
        return idx >= culprit_idx
    return _probe


def _log2_bound(n):
    if n <= 1:
        return 1
    return 2 + int(math.ceil(math.log2(n - 1)))


class PinpointsPlantedCommit(unittest.TestCase):
    def test_pinpoints_planted_breaking_commit(self):
        history = _history(16)
        culprit = history[10]
        got = bisectreg.bisect_regression(
            _entry(), history, is_bad_fn=_monotone_probe(10))
        self.assertEqual(got, culprit)

    def test_exhaustive_every_culprit_position_in_small_window(self):
        n = 8
        for culprit_idx in range(1, n):
            with self.subTest(culprit_idx=culprit_idx):
                history = _history(n)
                got = bisectreg.bisect_regression(
                    _entry(), history,
                    is_bad_fn=_monotone_probe(culprit_idx))
                self.assertEqual(got, history[culprit_idx])


class Log2Bound(unittest.TestCase):
    def test_probe_count_within_log2_bounds(self):
        for n, culprit_idx in ((16, 10), (16, 1), (16, 15),
                               (17, 9), (64, 37), (64, 1), (64, 63)):
            with self.subTest(n=n, culprit_idx=culprit_idx):
                history = _history(n)
                calls = []
                got = bisectreg.bisect_regression(
                    _entry(), history,
                    is_bad_fn=_monotone_probe(culprit_idx, calls))
                self.assertEqual(got, history[culprit_idx])
                self.assertLessEqual(
                    len(calls), _log2_bound(n),
                    "n=%d culprit=%d took %d probes, bound is %d"
                    % (n, culprit_idx, len(calls), _log2_bound(n)))

    def test_single_bad_commit_is_the_culprit(self):
        history = ["c0000"]
        calls = []
        got = bisectreg.bisect_regression(
            _entry(), history, is_bad_fn=lambda c: calls.append(c) or True)
        self.assertEqual(got, "c0000")
        self.assertEqual(len(calls), 1)


class RefusesVacuousInput(unittest.TestCase):
    def test_good_tip_raises_nothing_reproduces(self):
        history = _history(8)
        with self.assertRaises(ValueError):
            bisectreg.bisect_regression(
                _entry(), history, is_bad_fn=lambda c: False)

    def test_bad_baseline_raises_culprit_predates_window(self):
        history = _history(8)
        with self.assertRaises(ValueError):
            bisectreg.bisect_regression(
                _entry(), history, is_bad_fn=lambda c: True)

    def test_single_good_commit_raises(self):
        with self.assertRaises(ValueError):
            bisectreg.bisect_regression(
                _entry(), ["c0000"], is_bad_fn=lambda c: False)

    def test_empty_history_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            bisectreg.bisect_regression(
                _entry(), [], is_bad_fn=lambda c: True)

    def test_malformed_history_raises(self):
        for bad_history in (None, "c0001", ["c0000", "", "c0002"],
                            ["c0000", None, "c0002"], ["c0000", 42]):
            with self.subTest(bad_history=bad_history):
                with self.assertRaises((TypeError, ValueError)):
                    bisectreg.bisect_regression(
                        _entry(), bad_history, is_bad_fn=lambda c: True)

    def test_malformed_entry_raises(self):
        history = _history(4)
        for bad_entry in (None, [], "case",
                          {"checksum": "x"}, {"case": "picosoc-uart-div"},
                          {"case": "  ", "checksum": "x"},
                          {"case": "picosoc-uart-div", "checksum": "  "}):
            with self.subTest(bad_entry=bad_entry):
                with self.assertRaises((TypeError, ValueError)):
                    bisectreg.bisect_regression(
                        bad_entry, history, is_bad_fn=lambda c: True)

    def test_missing_probe_raises_loudly(self):
        with self.assertRaises((TypeError, ValueError)):
            bisectreg.bisect_regression(_entry(), _history(4))
        with self.assertRaises((TypeError, ValueError)):
            bisectreg.bisect_regression(
                _entry(), _history(4), is_bad_fn="not-callable")


class HonestyContract(unittest.TestCase):
    def test_probe_exceptions_propagate_never_a_verdict(self):
        def _boom(commit):
            raise RuntimeError("sim backend exploded: boom")
        with self.assertRaises(RuntimeError):
            bisectreg.bisect_regression(
                _entry(), _history(8), is_bad_fn=_boom)

    def test_entry_is_read_only_never_mutated(self):
        entry = _entry()
        snapshot = copy.deepcopy(entry)
        bisectreg.bisect_regression(
            entry, _history(8), is_bad_fn=_monotone_probe(5))
        self.assertEqual(entry, snapshot)

    def test_deterministic_same_inputs_same_culprit(self):
        history = _history(16)
        first = bisectreg.bisect_regression(
            _entry(), history, is_bad_fn=_monotone_probe(10))
        second = bisectreg.bisect_regression(
            _entry(), history, is_bad_fn=_monotone_probe(10))
        self.assertEqual(first, second)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("bisectreg", text,
                             "%s must not wire in bisectreg" % name)

    def test_no_live_api(self):
        src = os.path.join(ROOT, "agent", "repair", "bisectreg.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        tree = ast.parse(text)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "socket", "http",
                     "openai", "anthropic"}
        self.assertTrue(imported.isdisjoint(forbidden),
                        "non-stdlib/API imports: %s"
                        % (imported & forbidden))
        lowered = text.lower()
        for token in ("model_api_key", "requests", "urllib"):
            self.assertNotIn(token, lowered,
                             "bisectreg.py must not touch live APIs: %r"
                             % token)

    def test_no_regguard_loop_wiring_standalone_reader(self):
        path = os.path.join(ROOT, "agent", "repair", "bisectreg.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                for alias in node.names:
                    imported.add(alias.name)
        for banned in ("regguard", "agent.trust.regguard"):
            self.assertFalse(any(banned in name for name in imported),
                             "bisectreg must be a standalone reader "
                             "(no regguard import), got %r" % imported)
        for banned in ("from loop import", "from guard import",
                       "import loop", "import guard", "try_apply_diff",
                       "save_registry", "record_fixed", 'open('):
            self.assertNotIn(banned, src,
                             "bisectreg must stay default-off/read-only: %r"
                             % banned)


if __name__ == "__main__":
    unittest.main()
