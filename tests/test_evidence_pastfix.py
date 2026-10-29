"""Task B4 (Past-fix memory, plan item 14) red-first tests:
agent/evidence/pastfix.py.

- Retrieves similar past diagnoses from runs/*/meta.json by
  signal-name overlap: given a list of signal names (fault context
  from caller), scans meta.json files under a runs dir (path passed
  by caller, never hardcoded), scores by Jaccard overlap of
  signal/token sets extracted from stored attempt records, returns
  ranked run-ids + scores + the stored diagnosis snippet.
- Anchor test: fixture runs dir with 2 planted metas (one sharing
  signal names, one not) -- query returns the similar run first with
  pinned score; empty runs dir returns [].
- Never invents run-ids (only ids found on disk).
- Pure stdlib; no API, no keys.
- Default-off: only runs on explicit call (enable_pastfix=True); the
  existing loop behavior never changes (no loop edits).

Run: python3 tests/test_evidence_pastfix.py -v   (from repo root)
"""
import ast
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import pastfix  # noqa: E402  (RED before implementation)


def _write_meta(runs_dir, run_id, meta):
    d = os.path.join(runs_dir, run_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "meta.json"), "w",
              encoding="utf-8") as fh:
        json.dump(meta, fh)
    return d


def _make_two_run_fixture():
    """Two planted runs: one shares query signals, one does not.

    similar: summary + fault lines carry do_waitirq/fetch_pc/max_addr.
    other:   uart/divisor/baud world, zero overlap with the query.
    Pinned Jaccard (tokenize = lowercase [a-z0-9_]+ over attempt
    summaries + mutant before/after/file):
      similar tokens = {do_waitirq, fetch_pc, max_addr, stall,
                        0, 1, picorv32, v} (8)
      query tokens   = {do_waitirq, max_addr, fetch_pc} (3)
      inter = 3, union = 8 -> 3/8 = 0.375
      other tokens share nothing with the query -> 0.0 (dropped).
    """
    tmp = tempfile.mkdtemp(prefix="pastfix_fixture_")
    _write_meta(tmp, "run_similar", {
        "run_id": "run_similar",
        "diagnosis": "do_waitirq holds fetch PC never advances",
        "attempts": [
            {"summary": "do_waitirq fetch_pc max_addr stall"},
        ],
        "mutant_record": {
            "file": "picorv32.v",
            "before": "do_waitirq <= 0;",
            "after": "do_waitirq <= 1;",
        },
    })
    _write_meta(tmp, "run_other", {
        "run_id": "run_other",
        "diagnosis": "uart divisor wrong baud idle",
        "attempts": [
            {"summary": "uart divisor baud tx_busy idle"},
        ],
        "mutant_record": {
            "file": "simpleuart.v",
            "before": "divisor <= 0;",
            "after": "divisor <= 1;",
        },
    })
    return tmp


QUERY = ["do_waitirq", "max_addr", "fetch_pc"]


class TestJaccard(unittest.TestCase):
    def test_identical_sets_score_one(self):
        self.assertEqual(pastfix.jaccard({"a", "b"}, {"a", "b"}), 1.0)

    def test_disjoint_sets_score_zero(self):
        self.assertEqual(pastfix.jaccard({"a"}, {"b"}), 0.0)

    def test_empty_either_side_scores_zero(self):
        self.assertEqual(pastfix.jaccard(set(), {"a"}), 0.0)
        self.assertEqual(pastfix.jaccard({"a"}, set()), 0.0)


class TestRetrieve(unittest.TestCase):
    def test_similar_run_ranks_first_with_pinned_score(self):
        runs = _make_two_run_fixture()
        hits = pastfix.retrieve(QUERY, runs, top_k=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["run_id"], "run_similar")
        self.assertEqual(hits[0]["score"], 0.375)
        self.assertIn("do_waitirq", hits[0]["diagnosis"])

    def test_unrelated_query_returns_empty_never_invents(self):
        runs = _make_two_run_fixture()
        hits = pastfix.retrieve(["xqztk", "vbwjlm", "zqxkqv"], runs)
        self.assertEqual(hits, [])

    def test_empty_runs_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pastfix.retrieve(QUERY, tmp), [])

    def test_missing_runs_dir_returns_empty_never_crashes(self):
        self.assertEqual(
            pastfix.retrieve(QUERY, "/nonexistent-runs-xyz"), [])

    def test_corrupt_meta_is_skipped_not_invented(self):
        runs = _make_two_run_fixture()
        bad = os.path.join(runs, "run_bad")
        os.makedirs(bad, exist_ok=True)
        with open(os.path.join(bad, "meta.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        hits = pastfix.retrieve(QUERY, runs, top_k=5)
        run_ids = [h["run_id"] for h in hits]
        self.assertEqual(run_ids, ["run_similar"])

    def test_run_ids_come_from_disk_only(self):
        # A meta whose internal run_id lies must not surface the lie:
        # the reported run_id is the directory name found on disk.
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(tmp, "real_dir_name", {
                "run_id": "forged_id_elsewhere",
                "diagnosis": "do_waitirq holds fetch",
                "attempts": [
                    {"summary": "do_waitirq fetch_pc max_addr stall"},
                ],
                "mutant_record": {
                    "file": "picorv32.v",
                    "before": "do_waitirq <= 0;",
                    "after": "do_waitirq <= 1;",
                },
            })
            hits = pastfix.retrieve(QUERY, tmp)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["run_id"], "real_dir_name")

    def test_empty_query_returns_empty(self):
        runs = _make_two_run_fixture()
        self.assertEqual(pastfix.retrieve([], runs), [])


class TestDefaultOff(unittest.TestCase):
    def test_pack_defaults_to_disabled_without_touching_disk(self):
        pack = pastfix.retrieve_for_prompt(
            query_signals=QUERY, runs_dir="/nonexistent-runs-xyz")
        self.assertFalse(pack["pastfix_enabled"])
        self.assertEqual(pack["pastfix_hits"], [])
        self.assertEqual(pack["prompt_section"], "")

    def test_enabled_path_needs_query_loudly(self):
        runs = _make_two_run_fixture()
        with self.assertRaises(ValueError):
            pastfix.retrieve_for_prompt(
                query_signals=[], runs_dir=runs,
                enable_pastfix=True)

    def test_enabled_path_reports_pinned_hit(self):
        runs = _make_two_run_fixture()
        pack = pastfix.retrieve_for_prompt(
            query_signals=QUERY, runs_dir=runs,
            enable_pastfix=True)
        self.assertTrue(pack["pastfix_enabled"])
        self.assertEqual(len(pack["pastfix_hits"]), 1)
        self.assertEqual(pack["pastfix_hits"][0]["run_id"],
                         "run_similar")
        self.assertIn("run_similar", pack["prompt_section"])

    def test_loop_untouched_by_evidence_wiring(self):
        # B4 is additive + default-off: loop.py must not import the
        # evidence package (existing loop behavior byte-identical).
        with open(os.path.join(ROOT, "agent", "loop.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIsNone(re.search(r"^\s*(import|from)\s+\S*evidence",
                                    src, re.M))

    def test_module_is_stdlib_only(self):
        # No sockets, no HTTP, no API clients: local meta.json only.
        with open(os.path.join(ROOT, "agent", "evidence",
                               "pastfix.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "socket", "http",
                     "openai", "anthropic"}
        self.assertTrue(imported.isdisjoint(forbidden),
                        "non-stdlib/API imports: %s"
                        % (imported & forbidden))


if __name__ == "__main__":
    unittest.main()
