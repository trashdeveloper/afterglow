"""Task E5 (Reset-domain explorer) red-first tests.

- enumerate_resets() -> [sequence]: covers async / sync / multi-cycle
  reset shapes (deterministic order, no args, no I/O).
- explore(tree, case, sequences, run_fn) -> {findings}: runs each
  reset sequence via the injected run_fn(tree, case, sequence) and
  attributes the exposure (RED) to exactly the sequences whose
  result is failing.

Fixture: reset-release bug exposed by exactly ONE enumerated
sequence (async short release mid-cycle -> stale state -> FAIL);
all other sequences PASS.

Run: python3 tests/test_discover_resetexplorer.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import resetexplorer  # noqa: E402  (RED before implementation: ModuleNotFoundError)


# ---------------------------------------------------------------------------
# Fixture: reset-release bug.
#
# The DUT releases reset asynchronously: a 1-cycle async assert released
# mid-cycle leaves a stale control flop (simulated here as FAIL), while
# on-edge (synchronous) releases and multi-cycle holds settle cleanly.
# Only the "async_short" shape exposes it.
# ---------------------------------------------------------------------------
EXPOSING_NAME = "async_short"


def _fixture_run(tree, case, sequence):
    """Fixture sim: RED iff the reset shape hits the release bug."""
    name = sequence["name"] if isinstance(sequence, dict) else sequence
    if name == EXPOSING_NAME:
        return "tohost: FAIL 0xdead (reset-release: stale ctrl flop)"
    return "tohost: PASS"


def _bool_run(tree, case, sequence):
    name = sequence["name"] if isinstance(sequence, dict) else sequence
    return name == EXPOSING_NAME


class TestEnumerateShapes(unittest.TestCase):
    def test_returns_nonempty_list_of_dicts(self):
        seqs = resetexplorer.enumerate_resets()
        self.assertIsInstance(seqs, list)
        self.assertGreaterEqual(len(seqs), 3)
        for seq in seqs:
            self.assertIsInstance(seq, dict)
            self.assertIn("name", seq)
            self.assertIn("kind", seq)

    def test_covers_async_sync_multicycle(self):
        seqs = resetexplorer.enumerate_resets()
        kinds = {s["kind"] for s in seqs}
        self.assertIn("async", kinds)
        self.assertIn("sync", kinds)
        cycles = [s.get("assert_cycles", 1) for s in seqs]
        self.assertTrue(any(c > 1 for c in cycles),
                        "enumeration must include a multi-cycle shape: %r" % (seqs,))

    def test_names_unique_and_deterministic(self):
        first = resetexplorer.enumerate_resets()
        second = resetexplorer.enumerate_resets()
        self.assertEqual(first, second)
        names = [s["name"] for s in first]
        self.assertEqual(len(names), len(set(names)),
                         "sequence names must be unique: %r" % (names,))
        for name in names:
            self.assertIsInstance(name, str)
            self.assertTrue(name)

    def test_does_not_mutate_across_calls(self):
        first = resetexplorer.enumerate_resets()
        first.append({"name": "bogus", "kind": "async"})
        second = resetexplorer.enumerate_resets()
        self.assertNotIn("bogus", [s["name"] for s in second])


class TestExploreFindsResetBug(unittest.TestCase):
    def test_exactly_one_sequence_exposes_bug(self):
        seqs = resetexplorer.enumerate_resets()
        findings = resetexplorer.explore("tree", "case", seqs, _fixture_run)
        self.assertEqual(findings["exposed"], [EXPOSING_NAME])
        self.assertEqual(sorted(findings["exposed"] + findings["passed"]),
                         sorted(s["name"] for s in seqs))
        self.assertEqual(len(findings["passed"]), len(seqs) - 1)

    def test_explore_calls_run_fn_once_per_sequence(self):
        seqs = resetexplorer.enumerate_resets()
        calls = []

        def _recording(tree, case, sequence):
            calls.append(sequence["name"])
            return _fixture_run(tree, case, sequence)

        findings = resetexplorer.explore("tree", "case", seqs, _recording)
        self.assertEqual(sorted(calls), sorted(s["name"] for s in seqs))
        self.assertEqual(findings["total"], len(seqs))

    def test_details_carry_raw_results(self):
        seqs = resetexplorer.enumerate_resets()
        findings = resetexplorer.explore("tree", "case", seqs, _fixture_run)
        self.assertIn("details", findings)
        for seq in seqs:
            self.assertIn(seq["name"], findings["details"])
        self.assertIn("FAIL", findings["details"][EXPOSING_NAME])

    def test_bool_results_accepted(self):
        seqs = resetexplorer.enumerate_resets()
        findings = resetexplorer.explore("tree", "case", seqs, _bool_run)
        self.assertEqual(findings["exposed"], [EXPOSING_NAME])

    def test_dict_results_accepted(self):
        seqs = resetexplorer.enumerate_resets()

        def _dict_run(tree, case, sequence):
            if sequence["name"] == EXPOSING_NAME:
                return {"red": True, "log": "FAIL stale flop"}
            return {"red": False, "log": "PASS"}

        findings = resetexplorer.explore("tree", "case", seqs, _dict_run)
        self.assertEqual(findings["exposed"], [EXPOSING_NAME])

    def test_all_pass_reports_no_exposure(self):
        seqs = resetexplorer.enumerate_resets()

        def _always_pass(tree, case, sequence):
            return "tohost: PASS"

        findings = resetexplorer.explore("tree", "case", seqs, _always_pass)
        self.assertEqual(findings["exposed"], [])
        self.assertEqual(len(findings["passed"]), len(seqs))

    def test_explore_does_not_mutate_sequences(self):
        seqs = resetexplorer.enumerate_resets()
        snapshot = [dict(s) for s in seqs]
        resetexplorer.explore("tree", "case", seqs, _fixture_run)
        self.assertEqual(seqs, snapshot)


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_explore_rejects_non_callable_run_fn(self):
        seqs = resetexplorer.enumerate_resets()
        with self.assertRaises(TypeError):
            resetexplorer.explore("tree", "case", seqs, "not-fn")

    def test_explore_rejects_bad_sequences(self):
        with self.assertRaises((TypeError, ValueError)):
            resetexplorer.explore("tree", "case", "not-a-list", _fixture_run)
        with self.assertRaises((TypeError, ValueError, KeyError)):
            resetexplorer.explore("tree", "case", [{"kind": "async"}],
                                  _fixture_run)
        with self.assertRaises(ValueError):
            dupes = [{"name": "x", "kind": "async"},
                     {"name": "x", "kind": "sync"}]
            resetexplorer.explore("tree", "case", dupes, _fixture_run)

    def test_run_error_propagates_never_pass(self):
        seqs = resetexplorer.enumerate_resets()

        def _boom(tree, case, sequence):
            raise RuntimeError("sim error: binary crashed")

        with self.assertRaises(RuntimeError):
            resetexplorer.explore("tree", "case", seqs, _boom)

    def test_uninterpretable_result_raises(self):
        seqs = resetexplorer.enumerate_resets()[:1]

        def _weird(tree, case, sequence):
            return object()

        with self.assertRaises(TypeError):
            resetexplorer.explore("tree", "case", seqs, _weird)

    def test_empty_sequences_reports_empty(self):
        findings = resetexplorer.explore("tree", "case", [], _fixture_run)
        self.assertEqual(findings["exposed"], [])
        self.assertEqual(findings["passed"], [])
        self.assertEqual(findings["total"], 0)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "resetexplorer", text,
                "%s must not reference the resetexplorer module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # resetexplorer is in-memory only; it must never write files.
        with open(os.path.join(ROOT, "agent", "discover",
                               "resetexplorer.py"),
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
                         "resetexplorer must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "resetexplorer.py"),
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
