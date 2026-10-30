#!/usr/bin/env python3
"""Task H5 (prompt-strategy registry, plan item 85) tests:
agent/prompts/registry.py.

Interface (plan text, verbatim):

  register(version, stats) / pick(case_class) -> version
    (historical winner per class; ties -> newest).

Flat JSON file store, injected path defaulting to tmp in tests.

Contract:
- Fixture: two versions' A/B outcomes registered; pick(case_class)
  returns the historical winner for that class (higher win rate).
- Per-class: winner differs per case class (v1 wins fetch, v2 wins
  uart-div).
- Ties -> newest (most recently registered version wins).
- Unknown case class / empty registry -> None (a miss, not a guess).
- Store is a flat JSON file at the injected path (tmp dir in tests).
- Bad version / stats / case_class propagate TypeError/ValueError
  loudly; never a fabricated version string over errors.
- Pure stdlib; no API, no keys, no network. Default-off: explicit
  call only; agent/loop.py and agent/guard.py untouched.

RED-first TDD: this file was written BEFORE
agent/prompts/registry.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_prompts_registry.py -v   (from repo root)
"""
import ast
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.prompts import registry  # noqa: E402  (RED before implementation)


def _tmp_store():
    tmp = tempfile.mkdtemp(prefix="af-reg-test-")
    return os.path.join(tmp, "registry.json")


class TestPromptStrategyRegistry(unittest.TestCase):
    def test_ab_outcomes_pick_historical_winner(self):
        store = _tmp_store()
        registry.register(
            "v1", {"uart-div": {"wins": 1, "runs": 4}}, store=store)
        registry.register(
            "v2", {"uart-div": {"wins": 3, "runs": 4}}, store=store)
        self.assertEqual(registry.pick("uart-div", store=store), "v2")

    def test_winner_is_per_case_class(self):
        store = _tmp_store()
        registry.register("v1", {
            "uart-div": {"wins": 1, "runs": 4},
            "fetch-misalign": {"wins": 3, "runs": 3},
        }, store=store)
        registry.register("v2", {
            "uart-div": {"wins": 3, "runs": 4},
            "fetch-misalign": {"wins": 1, "runs": 3},
        }, store=store)
        self.assertEqual(registry.pick("uart-div", store=store), "v2")
        self.assertEqual(
            registry.pick("fetch-misalign", store=store), "v1")

    def test_tie_goes_to_newest(self):
        store = _tmp_store()
        registry.register(
            "v1", {"uart-div": {"wins": 2, "runs": 4}}, store=store)
        registry.register(
            "v2", {"uart-div": {"wins": 2, "runs": 4}}, store=store)
        self.assertEqual(registry.pick("uart-div", store=store), "v2")

    def test_reregister_bumps_newest_for_ties(self):
        store = _tmp_store()
        registry.register(
            "v1", {"uart-div": {"wins": 2, "runs": 4}}, store=store)
        registry.register(
            "v2", {"uart-div": {"wins": 2, "runs": 4}}, store=store)
        registry.register(
            "v1", {"uart-div": {"wins": 2, "runs": 4}}, store=store)
        self.assertEqual(registry.pick("uart-div", store=store), "v1")

    def test_unknown_class_and_empty_registry_return_none(self):
        store = _tmp_store()
        self.assertIsNone(registry.pick("uart-div", store=store))
        registry.register(
            "v1", {"uart-div": {"wins": 1, "runs": 1}}, store=store)
        self.assertIsNone(
            registry.pick("never-seen-class", store=store))

    def test_store_is_flat_json_file(self):
        store = _tmp_store()
        registry.register(
            "v1", {"uart-div": {"wins": 1, "runs": 4}}, store=store)
        self.assertTrue(os.path.isfile(store))
        with open(store, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsInstance(data, dict)
        self.assertIn("v1", data)

    def test_bad_version_stats_raise_loudly(self):
        store = _tmp_store()
        for bad_version in (None, 123, "", [], "  "):
            with self.subTest(bad_version=bad_version):
                with self.assertRaises((TypeError, ValueError)):
                    registry.register(
                        bad_version,
                        {"uart-div": {"wins": 1, "runs": 1}},
                        store=store)
        for bad_stats in (None, 123, "v2", [], {},
                          {"uart-div": None},
                          {"uart-div": {"wins": -1, "runs": 4}},
                          {"uart-div": {"wins": 5, "runs": 4}},
                          {"": {"wins": 1, "runs": 1}}):
            with self.subTest(bad_stats=bad_stats):
                with self.assertRaises((TypeError, ValueError)):
                    registry.register("v9", bad_stats, store=store)

    def test_bad_case_class_raises_loudly(self):
        store = _tmp_store()
        registry.register(
            "v1", {"uart-div": {"wins": 1, "runs": 1}}, store=store)
        for bad_class in (None, 123, "", [], "  "):
            with self.subTest(bad_class=bad_class):
                with self.assertRaises((TypeError, ValueError)):
                    registry.pick(bad_class, store=store)


class TestUntouchedLoopGuard(unittest.TestCase):
    def test_loop_and_guard_do_not_wire_registry(self):
        # H5 is additive + default-off: loop.py / guard.py must not
        # import the prompts registry (existing behavior byte-identical).
        import re
        for rel in (os.path.join("agent", "loop.py"),
                    os.path.join("agent", "guard.py")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(
                re.search(r"^\s*(import|from)\s+\S*(prompts|registry)",
                          src, re.M),
                "%s wires registry" % rel)


class TestStdlibOnly(unittest.TestCase):
    def test_module_is_stdlib_only(self):
        # No sockets, no HTTP, no API clients: only stdlib imports.
        with open(os.path.join(ROOT, "agent", "prompts", "registry.py"),
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
        stdlib_allowed = {"json", "os", "tempfile"}
        forbidden = imported - stdlib_allowed
        self.assertEqual(forbidden, set(),
                         "registry may import only stdlib "
                         "(json/os/tempfile), got: %s"
                         % sorted(imported))


if __name__ == "__main__":
    unittest.main()
