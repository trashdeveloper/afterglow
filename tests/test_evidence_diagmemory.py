"""Task F7 (Diagnosis memory, plan item 67) red-first tests:
agent/evidence/diagmemory.py.

- record(attempt_log, diagnosis): append a failed diagnosis attempt.
- avoid(attempt_log) -> [banned_lines]: lines already tried and failed,
  so the next proposal must differ.
- Anchor: after recording a failed line-1446 diagnosis, avoid() bans
  1446 and the next proposal must differ from 1446.
- Pure stdlib; no API, no keys, no I/O.
- Default-off: only runs on explicit call (no loop/guard edits).

RED-first TDD: this file was written BEFORE
agent/evidence/diagmemory.py existed, so the first run must FAIL on
the missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_evidence_diagmemory.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import diagmemory  # noqa: E402  (RED before implementation)


class TestAnchor(unittest.TestCase):
    def test_failed_1446_is_banned_next_proposal_differs(self):
        log = []
        diagmemory.record(log, {"file": "picorv32.v", "line": 1446})
        banned = diagmemory.avoid(log)
        self.assertIn(1446, banned)
        # next proposal must differ from 1446: any candidate drawn
        # from outside the banned set differs.
        for candidate in (1445, 1447, 100):
            self.assertNotIn(candidate, banned)
        proposals = [c for c in (1446, 1445, 1447) if c not in banned]
        self.assertTrue(proposals)
        self.assertNotIn(1446, proposals)

    def test_avoid_empty_log_is_empty(self):
        self.assertEqual(diagmemory.avoid([]), [])


class TestRecordAvoid(unittest.TestCase):
    def test_record_appends_and_avoid_lists_all(self):
        log = []
        diagmemory.record(log, {"file": "a.v", "line": 10})
        diagmemory.record(log, {"file": "a.v", "line": 20})
        self.assertEqual(len(log), 2)
        banned = diagmemory.avoid(log)
        self.assertEqual(sorted(banned), [10, 20])

    def test_duplicate_line_banned_once(self):
        log = []
        diagmemory.record(log, {"file": "a.v", "line": 7})
        diagmemory.record(log, {"file": "a.v", "line": 7})
        banned = diagmemory.avoid(log)
        self.assertEqual(banned.count(7), 1)

    def test_avoid_order_is_first_seen(self):
        log = []
        diagmemory.record(log, {"file": "a.v", "line": 30})
        diagmemory.record(log, {"file": "a.v", "line": 10})
        diagmemory.record(log, {"file": "a.v", "line": 20})
        self.assertEqual(diagmemory.avoid(log), [30, 10, 20])

    def test_int_diagnosis_accepted(self):
        log = []
        diagmemory.record(log, 1446)
        self.assertIn(1446, diagmemory.avoid(log))

    def test_avoid_does_not_mutate_log(self):
        log = [{"file": "a.v", "line": 5}]
        before = [dict(d) if isinstance(d, dict) else d for d in log]
        diagmemory.avoid(log)
        self.assertEqual(log, before)

    def test_record_returns_none_or_log(self):
        log = []
        ret = diagmemory.record(log, {"file": "a.v", "line": 1})
        self.assertTrue(ret is None or ret is log)
        self.assertEqual(len(log), 1)


class TestValidation(unittest.TestCase):
    def test_record_none_log_raises(self):
        with self.assertRaises(ValueError):
            diagmemory.record(None, {"file": "a.v", "line": 1})

    def test_record_bad_line_raises(self):
        with self.assertRaises(ValueError):
            diagmemory.record([], {"file": "a.v", "line": -3})
        with self.assertRaises(ValueError):
            diagmemory.record([], {"file": "a.v"})
        with self.assertRaises(ValueError):
            diagmemory.record([], "not-a-line")

    def test_avoid_none_raises(self):
        with self.assertRaises(ValueError):
            diagmemory.avoid(None)

    def test_avoid_bad_entry_raises(self):
        with self.assertRaises(ValueError):
            diagmemory.avoid([{"file": "a.v"}])


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("diagmemory", text,
                             "%s must not wire in diagmemory" % name)

    def test_no_live_api(self):
        src = os.path.join(ROOT, "agent", "evidence", "diagmemory.py")
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
                             "diagmemory.py must not touch live APIs: %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
