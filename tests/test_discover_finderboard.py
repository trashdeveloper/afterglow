"""Task E10 (Finder portfolio board) red-first tests.

Parent contract (binding):
- board(entries) -> ranked table where entries are
  {task_id, tool, seed, exposed, steps, tokens} and ranking is
  exposed-first, then fewer steps, then fewer tokens.
- summary(entries) -> {exposed_count, total, tokens_spent}.

Plan contract (item 60 compat):
- attribute(fault_id, finder, board) -> board (first-wins dedup).
- leaders(board) -> [(finder, count)] ranked by count desc.

Run: python3 -m unittest tests.test_discover_finderboard -v (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import finderboard  # noqa: E402  (RED before implementation: ModuleNotFoundError)


def _entries():
    # Mixed exposed/unexposed fixture. Expected board order:
    #   1. t3 (exposed, 5 steps, 100 tokens) -- exposed + fewest steps
    #   2. t1 (exposed, 10 steps, 200 tokens)
    #   3. t4 (exposed, 10 steps, 300 tokens) -- steps tie with t1, tokens break it
    #   4. t2 (unexposed, 3 steps, 50 tokens) -- unexposed sinks even though cheapest
    #   5. t5 (unexposed, 8 steps, 80 tokens)
    return [
        {"task_id": "t1", "tool": "fuzzer", "seed": 42, "exposed": True,
         "steps": 10, "tokens": 200},
        {"task_id": "t2", "tool": "fuzzer", "seed": 43, "exposed": False,
         "steps": 3, "tokens": 50},
        {"task_id": "t3", "tool": "cone2", "seed": 7, "exposed": True,
         "steps": 5, "tokens": 100},
        {"task_id": "t4", "tool": "seeddiff", "seed": 9, "exposed": True,
         "steps": 10, "tokens": 300},
        {"task_id": "t5", "tool": "cone2", "seed": 11, "exposed": False,
         "steps": 8, "tokens": 80},
    ]


class TestBoardOrdering(unittest.TestCase):
    def test_exposed_first(self):
        ranked = finderboard.board(_entries())
        ids = [e["task_id"] for e in ranked]
        # All exposed entries precede all unexposed ones.
        self.assertEqual(ids[:3], ["t3", "t1", "t4"])
        self.assertEqual(ids[3:], ["t2", "t5"])

    def test_ties_by_steps_then_tokens(self):
        ranked = finderboard.board(_entries())
        ids = [e["task_id"] for e in ranked]
        # t3 fewest steps among exposed; t1/t4 tie on steps -> fewer tokens wins.
        self.assertEqual(ids, ["t3", "t1", "t4", "t2", "t5"])

    def test_unexposed_tie_break_steps_then_tokens(self):
        entries = [
            {"task_id": "u1", "tool": "a", "seed": 1, "exposed": False,
             "steps": 5, "tokens": 9},
            {"task_id": "u2", "tool": "a", "seed": 2, "exposed": False,
             "steps": 5, "tokens": 7},
            {"task_id": "u3", "tool": "a", "seed": 3, "exposed": False,
             "steps": 4, "tokens": 99},
        ]
        ranked = finderboard.board(entries)
        self.assertEqual([e["task_id"] for e in ranked], ["u3", "u2", "u1"])

    def test_does_not_mutate_inputs(self):
        entries = _entries()
        snapshot = [dict(e) for e in entries]
        finderboard.board(entries)
        self.assertEqual(entries, snapshot)

    def test_empty_board(self):
        self.assertEqual(finderboard.board([]), [])

    def test_rejects_bad_entries(self):
        with self.assertRaises((TypeError, ValueError, KeyError)):
            finderboard.board([{"task_id": "x"}])
        with self.assertRaises(TypeError):
            finderboard.board("not-a-list")


class TestSummary(unittest.TestCase):
    def test_summary_counts(self):
        s = finderboard.summary(_entries())
        self.assertEqual(s["exposed_count"], 3)
        self.assertEqual(s["total"], 5)
        self.assertEqual(s["tokens_spent"], 200 + 50 + 100 + 300 + 80)

    def test_summary_empty(self):
        s = finderboard.summary([])
        self.assertEqual(s, {"exposed_count": 0, "total": 0,
                             "tokens_spent": 0})

    def test_summary_of_ranked_board(self):
        ranked = finderboard.board(_entries())
        s = finderboard.summary(ranked)
        self.assertEqual((s["exposed_count"], s["total"]), (3, 5))


class TestAttributeLeaders(unittest.TestCase):
    def test_first_finder_keeps_credit(self):
        b = {}
        finderboard.attribute("fault-A", "fuzzer", b)
        finderboard.attribute("fault-A", "cone2", b)  # dup: first wins
        self.assertEqual(b, {"fault-A": "fuzzer"})

    def test_leaders_ranks_correctly(self):
        b = {}
        finderboard.attribute("f1", "fuzzer", b)
        finderboard.attribute("f2", "cone2", b)
        finderboard.attribute("f3", "fuzzer", b)
        self.assertEqual(finderboard.leaders(b), [("fuzzer", 2), ("cone2", 1)])


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "finderboard", text,
                "%s must not reference the finderboard module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "finderboard.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint({"requests", "urllib"}),
                        "no live HTTP imports allowed ($0 gate): %r"
                        % (imported,))
        for token in ("MODEL_API_KEY", "urllib", "requests"):
            self.assertNotIn(token, src,
                             "no live-API usage allowed ($0 gate): %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
