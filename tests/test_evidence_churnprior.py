"""Task F6 (Churn-prioritized diagnosis, plan item 66) red-first tests:
agent/evidence/churnprior.py.

- Recent git-churn lines break ties between equally-scoring diagnosis
  candidates: break_ties(scored_lines, churn) -> ordered, where equal
  scores are ordered by churn recency (higher recency = more recent =
  ranks first) and different scores always keep score order.
- Line identity keys come from agent/evidence/blame_ctx.py porcelain
  output entries {"file", "line", ...}; churn maps that identity to a
  caller-provided numeric recency (higher = more recently churned).
- Anchor tests: two lines with equal scores -> recently-churned ranks
  first; different scores -> score order wins regardless of churn.
- Never invents lines (output is a reordering of the input items only).
- Pure stdlib; no API, no keys, no I/O.
- Default-off: only runs on explicit call (no loop/guard edits).

RED-first TDD: this file was written BEFORE
agent/evidence/churnprior.py existed, so the first run must FAIL on
the missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_evidence_churnprior.py -v   (from repo root)
"""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import churnprior  # noqa: E402  (RED before implementation)


def _item(line, score, fname="picorv32.v"):
    return {"file": fname, "line": line, "score": score}


class TestBreakTiesAnchor(unittest.TestCase):
    def test_equal_scores_recently_churned_ranks_first(self):
        scored = [_item(10, 0.5), _item(20, 0.5)]
        churn = {("picorv32.v", 10): 1, ("picorv32.v", 20): 9}
        ordered = churnprior.break_ties(scored, churn)
        self.assertEqual([d["line"] for d in ordered], [20, 10])

    def test_different_scores_keep_order_despite_churn(self):
        # Churn is a tie-break ONLY: a higher score always wins even
        # when the lower-scored line churned more recently.
        scored = [_item(10, 0.9), _item(20, 0.1)]
        churn = {("picorv32.v", 10): 1, ("picorv32.v", 20): 99}
        ordered = churnprior.break_ties(scored, churn)
        self.assertEqual([d["line"] for d in ordered], [10, 20])

    def test_three_way_tie_sorted_by_churn_desc(self):
        scored = [_item(1, 0.5), _item(2, 0.5), _item(3, 0.5)]
        churn = {("picorv32.v", 1): 3,
                 ("picorv32.v", 2): 30,
                 ("picorv32.v", 3): 7}
        ordered = churnprior.break_ties(scored, churn)
        self.assertEqual([d["line"] for d in ordered], [2, 3, 1])


class TestChurnKeys(unittest.TestCase):
    def test_plain_line_int_keys(self):
        scored = [_item(10, 0.5), _item(20, 0.5)]
        ordered = churnprior.break_ties(scored, {10: 1, 20: 2})
        self.assertEqual([d["line"] for d in ordered], [20, 10])

    def test_file_colon_line_string_keys(self):
        scored = [_item(10, 0.5), _item(20, 0.5)]
        churn = {"picorv32.v:10": 1, "picorv32.v:20": 2}
        ordered = churnprior.break_ties(scored, churn)
        self.assertEqual([d["line"] for d in ordered], [20, 10])

    def test_missing_churn_entry_is_oldest(self):
        # A line with no churn record ranks as never-churned (0),
        # below every recorded line among equals.
        scored = [_item(10, 0.5), _item(20, 0.5)]
        churn = {("picorv32.v", 20): 4}  # line 10 absent -> 0
        ordered = churnprior.break_ties(scored, churn)
        self.assertEqual([d["line"] for d in ordered], [20, 10])

    def test_none_churn_means_no_bump(self):
        scored = [_item(20, 0.5), _item(10, 0.5)]
        ordered = churnprior.break_ties(scored, None)
        self.assertEqual([d["line"] for d in ordered], [10, 20])

    def test_empty_scored_returns_empty(self):
        self.assertEqual(churnprior.break_ties([], {}), [])


class TestDeterminism(unittest.TestCase):
    def test_full_tie_breaks_by_file_then_line(self):
        scored = [_item(20, 0.5, "b.v"), _item(10, 0.5, "a.v"),
                  _item(15, 0.5, "a.v")]
        ordered = churnprior.break_ties(scored, {})
        self.assertEqual([(d["file"], d["line"]) for d in ordered],
                         [("a.v", 10), ("a.v", 15), ("b.v", 20)])

    def test_does_not_mutate_inputs(self):
        scored = [_item(10, 0.5), _item(20, 0.5)]
        churn = {("picorv32.v", 10): 1, ("picorv32.v", 20): 9}
        before = [dict(d) for d in scored]
        churnprior.break_ties(scored, churn)
        self.assertEqual(scored, before)
        self.assertEqual(churn,
                         {("picorv32.v", 10): 1, ("picorv32.v", 20): 9})

    def test_extra_keys_preserved(self):
        scored = [{"file": "picorv32.v", "line": 10, "score": 0.5,
                   "signal": "do_waitirq"},
                  {"file": "picorv32.v", "line": 20, "score": 0.5,
                   "signal": "fetch_pc"}]
        churn = {("picorv32.v", 10): 1, ("picorv32.v", 20): 2}
        ordered = churnprior.break_ties(scored, churn)
        self.assertEqual(ordered[0]["signal"], "fetch_pc")
        self.assertEqual(ordered[1]["signal"], "do_waitirq")


class TestValidation(unittest.TestCase):
    def test_none_scored_raises(self):
        with self.assertRaises(ValueError):
            churnprior.break_ties(None, {})

    def test_item_missing_score_raises(self):
        with self.assertRaises(ValueError):
            churnprior.break_ties([{"file": "a.v", "line": 1}], {})

    def test_item_missing_line_raises(self):
        with self.assertRaises(ValueError):
            churnprior.break_ties([{"file": "a.v", "score": 0.5}], {})

    def test_non_numeric_score_raises(self):
        with self.assertRaises(ValueError):
            churnprior.break_ties([_item(1, "high")], {})

    def test_non_numeric_churn_value_raises(self):
        scored = [_item(10, 0.5), _item(20, 0.5)]
        with self.assertRaises(ValueError):
            churnprior.break_ties(scored, {10: "yesterday", 20: 1})


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        # F6 is standalone: loop.py + guard.py must not reference it.
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("churnprior", text,
                             "%s must not wire in churnprior" % name)

    def test_no_live_api(self):
        # No MODEL_API_KEY / requests / urllib usage in the new module.
        src = os.path.join(ROOT, "agent", "evidence", "churnprior.py")
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
                             "churnprior.py must not touch live APIs: %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
