"""Task E7 (Coverage-hole prioritizer) red-first tests.

- rank(uncovered_lines, toggles, fanout) -> [line...]
  score = fanout x (1 + toggle history), descending; ties broken
  deterministically by str(line) asc.

Fixture: three uncovered lines where the top-ranked line kills when
mutated (simulated via injected run_fn) and the bottom-ranked line
does not.

Run: python3 tests/test_discover_holerank.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import holerank  # noqa: E402  (RED before implementation: ModuleNotFoundError)


# ---------------------------------------------------------------------------
# Fixture: scores pin TOP above MID above BOT.
#   TOP: fanout 10 x (1 + 5 toggles) = 60
#   MID: fanout 5  x (1 + 2 toggles) = 15
#   BOT: fanout 2  x (1 + 0 toggles) = 2
# ---------------------------------------------------------------------------
TOP = "uart_tx.v:120"
MID = "uart_tx.v:200"
BOT = "uart_tx.v:310"

TOGGLES = {TOP: 5, MID: 2, BOT: 0}
FANOUT = {TOP: 10, MID: 5, BOT: 2}


def _fixture_run(line):
    """Fixture mutant: mutating TOP kills, mutating BOT does not."""
    if line == TOP:
        return "tohost: FAIL 0xdead"
    return "tohost: PASS"


def _is_kill(log):
    return "FAIL" in log


class TestRankOrdersByScore(unittest.TestCase):
    def test_rank_orders_descending(self):
        ranked = holerank.rank([MID, BOT, TOP], TOGGLES, FANOUT)
        self.assertEqual(ranked, [TOP, MID, BOT])

    def test_score_formula_fanout_times_one_plus_toggles(self):
        # MID: 5 x (1 + 2) = 15 must beat a high-fanout zero-toggle line
        # (fanout 14 x 1 = 14) and lose to a low-fanout hot line
        # (fanout 3 x (1 + 5) = 18).
        lines = [MID, "a.v:1", "b.v:2"]
        toggles = {MID: 2, "a.v:1": 0, "b.v:2": 5}
        fanout = {MID: 5, "a.v:1": 14, "b.v:2": 3}
        ranked = holerank.rank(lines, toggles, fanout)
        self.assertEqual(ranked, ["b.v:2", MID, "a.v:1"])

    def test_missing_entries_default_zero(self):
        # Lines absent from toggles/fanout score 0 and sink to the bottom.
        ranked = holerank.rank([TOP, "unknown.v:9"], TOGGLES, FANOUT)
        self.assertEqual(ranked, [TOP, "unknown.v:9"])

    def test_none_maps_mean_all_zero_tie_break_by_name(self):
        ranked = holerank.rank(["b.v:2", "a.v:1"], None, None)
        self.assertEqual(ranked, ["a.v:1", "b.v:2"])

    def test_empty_uncovered_returns_empty(self):
        self.assertEqual(holerank.rank([], {}, {}), [])

    def test_tie_break_deterministic_by_name(self):
        # Equal scores (fanout 4 x (1+1) = 8 each): str(line) asc wins.
        lines = ["z.v:1", "a.v:1"]
        toggles = {"z.v:1": 1, "a.v:1": 1}
        fanout = {"z.v:1": 4, "a.v:1": 4}
        self.assertEqual(holerank.rank(lines, toggles, fanout),
                         ["a.v:1", "z.v:1"])
        # Input order must not change the outcome.
        self.assertEqual(holerank.rank(list(reversed(lines)), toggles,
                                       fanout),
                         ["a.v:1", "z.v:1"])

    def test_does_not_mutate_inputs(self):
        lines = [MID, BOT, TOP]
        snapshot = list(lines)
        toggles = dict(TOGGLES)
        fanout = dict(FANOUT)
        holerank.rank(lines, toggles, fanout)
        self.assertEqual(lines, snapshot)
        self.assertEqual(toggles, TOGGLES)
        self.assertEqual(fanout, FANOUT)


class TestFixtureKillProof(unittest.TestCase):
    def test_top_ranked_kills_when_mutated(self):
        ranked = holerank.rank([MID, BOT, TOP], TOGGLES, FANOUT)
        self.assertEqual(ranked[0], TOP)
        self.assertTrue(_is_kill(_fixture_run(ranked[0])))

    def test_bottom_ranked_does_not_kill(self):
        ranked = holerank.rank([MID, BOT, TOP], TOGGLES, FANOUT)
        self.assertEqual(ranked[-1], BOT)
        self.assertFalse(_is_kill(_fixture_run(ranked[-1])))

    def test_rank_then_run_fn_end_to_end(self):
        # The prioritizer's job: the first line it names is the one
        # worth mutating first -- the fixture proves it kills.
        ranked = holerank.rank([BOT, MID, TOP], TOGGLES, FANOUT)
        kills = [_is_kill(_fixture_run(line)) for line in ranked]
        self.assertEqual(kills, [True, False, False])


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_rejects_non_list_uncovered(self):
        with self.assertRaises(TypeError):
            holerank.rank("not-a-list", {}, {})

    def test_rejects_non_dict_maps(self):
        with self.assertRaises(TypeError):
            holerank.rank([TOP], [("x", 1)], {})
        with self.assertRaises(TypeError):
            holerank.rank([TOP], {}, [("x", 1)])

    def test_rejects_negative_counts(self):
        with self.assertRaises(ValueError):
            holerank.rank([TOP], {TOP: -1}, FANOUT)
        with self.assertRaises(ValueError):
            holerank.rank([TOP], TOGGLES, {TOP: -2})

    def test_rejects_non_int_counts(self):
        with self.assertRaises(TypeError):
            holerank.rank([TOP], {TOP: "many"}, FANOUT)
        with self.assertRaises(TypeError):
            holerank.rank([TOP], TOGGLES, {TOP: 1.5})


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "holerank", text,
                "%s must not reference the holerank module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # holerank is in-memory only; it must never write files.
        with open(os.path.join(ROOT, "agent", "discover",
                               "holerank.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        writes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in ("write_text", "dump", "dumpf", "copyfile",
                            "copy", "move", "rename", "makedirs", "mkdir"):
                    writes.add(name)
                if name == "open":
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value,
                                                           ast.Constant):
                            if any(c in str(kw.value.value) for c in "wax+"):
                                writes.add("open(%r)" % kw.value.value)
                    if (len(node.args) >= 2
                            and isinstance(node.args[1], ast.Constant)
                            and any(c in str(node.args[1].value)
                                    for c in "wax+")):
                        writes.add("open(%r)" % node.args[1].value)
        self.assertEqual(writes, set(),
                         "holerank must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "holerank.py"),
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
