"""Task G1 (dependent multi-hunk planner, plan item 71) tests:
agent/repair/planhunkdep.py.

Consumes the agent/repair/planhunk.py plan format (list of "file:line"
hunks; dict entries below only ADD explicit signal annotations):

  order_hunks(plan) -> ordered | raises on unorderable

Ordering constraint (plan text, verbatim): the second hunk referencing
the first's signal must come after (the definer).

Contract:
- Correctly ordered dependent plan passes (returned unchanged).
- Out-of-order dependent plan is REJECTED (ValueError).
- Independent hunks (bare "file:line" strings, no signals) pass in any
  order.
- Cyclic dependency (A uses B's signal, B uses A's) raises ValueError
  in either order (truly unorderable).
- Empty plan / malformed entries -> ValueError.
- Uses of signals no hunk defines (external signals) are fine.
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no planhunkdep wiring).

RED-first TDD: this file was written BEFORE
agent/repair/planhunkdep.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_planhunkdep.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import planhunkdep  # noqa: E402  (RED before implementation)


# Hunk A defines mem_xfer; hunk B references (uses) it.
HUNK_DEF = {"hunk": "picorv32.v:373", "defines": ["mem_xfer"], "uses": []}
HUNK_USE = {"hunk": "picorv32.v:376", "defines": [], "uses": ["mem_xfer"]}


class OrderHunksDependent(unittest.TestCase):
    def test_correctly_ordered_dependent_passes(self):
        plan = [dict(HUNK_DEF), dict(HUNK_USE)]
        self.assertEqual(planhunkdep.order_hunks(plan),
                         ["picorv32.v:373", "picorv32.v:376"])

    def test_out_of_order_dependent_rejected(self):
        with self.assertRaises(ValueError):
            planhunkdep.order_hunks([dict(HUNK_USE), dict(HUNK_DEF)])

    def test_independent_hunks_any_order(self):
        fwd = ["picorv32.v:373", "picorv32.v:376"]
        rev = ["picorv32.v:376", "picorv32.v:373"]
        self.assertEqual(planhunkdep.order_hunks(fwd), fwd)
        self.assertEqual(planhunkdep.order_hunks(rev), rev)

    def test_external_signal_use_passes(self):
        plan = [dict(HUNK_USE)]  # mem_xfer defined nowhere in plan
        self.assertEqual(planhunkdep.order_hunks(plan),
                         ["picorv32.v:376"])

    def test_cyclic_dependency_raises_either_order(self):
        a = {"hunk": "picorv32.v:373", "defines": ["sig_a"],
             "uses": ["sig_b"]}
        b = {"hunk": "picorv32.v:376", "defines": ["sig_b"],
             "uses": ["sig_a"]}
        with self.assertRaises(ValueError):
            planhunkdep.order_hunks([dict(a), dict(b)])
        with self.assertRaises(ValueError):
            planhunkdep.order_hunks([dict(b), dict(a)])

    def test_self_use_does_not_raise(self):
        hunk = {"hunk": "picorv32.v:373", "defines": ["mem_xfer"],
                "uses": ["mem_xfer"]}
        self.assertEqual(planhunkdep.order_hunks([dict(hunk)]),
                         ["picorv32.v:373"])

    def test_empty_plan_raises(self):
        for bad in ([], ()):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    planhunkdep.order_hunks(bad)

    def test_malformed_plan_arg_raises(self):
        for bad in (None, "picorv32.v:373", 123, {"hunk": "x"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    planhunkdep.order_hunks(bad)

    def test_malformed_entries_raise(self):
        for bad in (["no-colon-here"], [123], [None],
                    [{"hunk": "picorv32.v:373", "defines": "mem_xfer"}],
                    [{"hunk": "picorv32.v:373", "uses": [42]}],
                    [{"defines": ["x"]}]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    planhunkdep.order_hunks(bad)


class DefaultOffNoGuardWiring(unittest.TestCase):
    def test_planhunkdep_imports_nothing_from_loop_or_guard(self):
        path = os.path.join(ROOT, "agent", "repair", "planhunkdep.py")
        with open(path) as fh:
            src = fh.read()
        for banned in ("from loop import", "from guard import",
                       "import loop", "import guard",
                       "try_apply_diff"):
            self.assertNotIn(banned, src,
                             "planhunkdep must stay default-off "
                             "(no loop/guard wiring): %r" % banned)

    def test_loop_and_guard_have_no_planhunkdep_wiring(self):
        for rel in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, rel)
            with open(path) as fh:
                src = fh.read()
            for banned in ("planhunkdep", "order_hunks"):
                with self.subTest(file=rel, banned=banned):
                    self.assertNotIn(
                        banned, src,
                        "%s untouched: no planhunkdep wiring" % rel)

    def test_no_api_or_keys_in_module(self):
        path = os.path.join(ROOT, "agent", "repair", "planhunkdep.py")
        with open(path) as fh:
            src = fh.read()
        for banned in ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY",
                       "urllib", "requests", "socket", "os.environ"):
            self.assertNotIn(banned, src,
                             "planhunkdep is SIM-ONLY pure stdlib "
                             "(no API/keys): %r" % banned)


if __name__ == "__main__":
    unittest.main()
