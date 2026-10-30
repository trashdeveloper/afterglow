#!/usr/bin/env python3
"""Task H6 (cost-aware model router, plan item 86) tests:
agent/repair/modelrouter.py.

Interface (plan text, verbatim):

  route(failure_class) -> "cheap"|"escalate";
  run_with_escalation(task_fn, cheap_fn, strong_fn).

Cheap model first, escalate on defined failure classes only. All
model legs are injected callables (counting fns); no live calls, no
API, no keys.

Contract:
- Fixture: task_fn yielding the defined failure class escalates
  EXACTLY once (strong_fn called once, cheap_fn never); any other
  class takes the cheap path (cheap_fn once, strong_fn never).
- route returns "escalate" only for the defined class, "cheap" for
  everything else.
- Bad failure_class / bad fns raise TypeError/ValueError loudly
  before any call; fn exceptions propagate unchanged (never a
  fabricated result).
- Pure stdlib (no imports); no API, no keys, no network, no I/O.
  Default-off: explicit call only; agent/loop.py and agent/guard.py
  untouched (no modelrouter wiring).

RED-first TDD: this file was written BEFORE
agent/repair/modelrouter.py existed, so the first run must FAIL on
the missing module (proving the test exercises real code, not a
stub).

Run: python3 tests/test_repair_modelrouter.py -v   (from repo root)
"""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import modelrouter  # noqa: E402  (RED before implementation)

# The defined failure class (the one class that escalates). Must stay
# in sync with modelrouter.ESCALATE_CLASSES.
HARD_CLASS = "hard"
CHEAP_CLASSES = ["uart-div", "fetch-misalign", "trap-stuck", "typo"]


def _counter(name, calls, result=None, failure_class=None):
    def _fn(*args):
        calls.append((name,) + tuple(args))
        return result
    return _fn


class TestRoute(unittest.TestCase):
    def test_defined_class_escalates(self):
        self.assertEqual(modelrouter.route(HARD_CLASS), "escalate")

    def test_other_classes_stay_cheap(self):
        for cls in CHEAP_CLASSES:
            with self.subTest(cls=cls):
                self.assertEqual(modelrouter.route(cls), "cheap")

    def test_bad_failure_class_raises_loudly(self):
        for bad in (None, 123, "", "   ", [], ["hard"], b"hard"):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    modelrouter.route(bad)


class TestRunWithEscalation(unittest.TestCase):
    def test_escalates_exactly_once_on_defined_class(self):
        calls = []
        out = modelrouter.run_with_escalation(
            _counter("task", calls, result=HARD_CLASS),
            _counter("cheap", calls, result="cheap-answer"),
            _counter("strong", calls, result="strong-answer"),
        )
        self.assertEqual(out["route"], "escalate")
        self.assertEqual(out["result"], "strong-answer")
        kinds = [c[0] for c in calls]
        self.assertEqual(kinds.count("task"), 1)
        self.assertEqual(kinds.count("strong"), 1)
        self.assertEqual(kinds.count("cheap"), 0)

    def test_cheap_path_used_otherwise(self):
        for cls in CHEAP_CLASSES:
            with self.subTest(cls=cls):
                calls = []
                out = modelrouter.run_with_escalation(
                    _counter("task", calls, result=cls),
                    _counter("cheap", calls, result="cheap-answer"),
                    _counter("strong", calls, result="strong-answer"),
                )
                self.assertEqual(out["route"], "cheap")
                self.assertEqual(out["result"], "cheap-answer")
                kinds = [c[0] for c in calls]
                self.assertEqual(kinds.count("task"), 1)
                self.assertEqual(kinds.count("cheap"), 1)
                self.assertEqual(kinds.count("strong"), 0)

    def test_model_fns_receive_failure_class(self):
        seen = []

        def _cheap(failure_class):
            seen.append(failure_class)
            return "ok"

        out = modelrouter.run_with_escalation(
            lambda: "uart-div", _cheap, lambda fc: "strong")
        self.assertEqual(out["result"], "ok")
        self.assertEqual(seen, ["uart-div"])

    def test_non_callable_fns_raise_before_any_call(self):
        calls = []
        for bad in (None, 123, "fn"):
            with self.subTest(bad=bad):
                for args in (
                    (bad, lambda fc: None, lambda fc: None),
                    (lambda: HARD_CLASS, bad, lambda fc: None),
                    (lambda: HARD_CLASS, lambda fc: None, bad),
                ):
                    with self.assertRaises(TypeError):
                        modelrouter.run_with_escalation(*args)
        self.assertEqual(calls, [])

    def test_fn_exceptions_propagate_unchanged(self):
        boom = RuntimeError("task blew up")

        def _task():
            raise boom

        with self.assertRaises(RuntimeError) as ctx:
            modelrouter.run_with_escalation(
                _task, lambda fc: None, lambda fc: None)
        self.assertIs(ctx.exception, boom)

        cheap_boom = ValueError("cheap blew up")
        with self.assertRaises(ValueError) as ctx2:
            modelrouter.run_with_escalation(
                lambda: "uart-div",
                lambda fc: (_ for _ in ()).throw(cheap_boom),
                lambda fc: "strong")
        self.assertIs(ctx2.exception, cheap_boom)


class TestUntouchedLoopGuard(unittest.TestCase):
    def test_loop_and_guard_do_not_wire_modelrouter(self):
        # H6 is additive + default-off: loop.py / guard.py must not
        # import the repair package (existing behavior byte-identical).
        for rel in (os.path.join("agent", "loop.py"),
                    os.path.join("agent", "guard.py")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(
                re.search(r"^\s*(import|from)\s+\S*(repair|modelrouter)",
                          src, re.M),
                "%s wires modelrouter" % rel)


class TestStdlibOnly(unittest.TestCase):
    def test_module_is_stdlib_only(self):
        # No sockets, no HTTP, no API clients: pure stdlib (no repo
        # imports at all -- routing is a local dispatch decision).
        with open(os.path.join(ROOT, "agent", "repair", "modelrouter.py"),
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
        self.assertEqual(imported, set(),
                         "modelrouter must be import-free stdlib-only, "
                         "got: %s" % sorted(imported))


if __name__ == "__main__":
    unittest.main()
