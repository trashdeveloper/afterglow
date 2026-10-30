"""Task G7 (fixer self-repair, plan item 77) tests:
agent/repair/selfrepair.py.

Interface (plan text, verbatim):

  Consumes: agent/trust/blame.py::classify_red failure classes.
  Produces: fallback_for(attribution) -> strategy;
            retry_with(failure, fallback_fn).

``attribution`` is one of blame.py's classify_red failure classes
("tool", "tb", "model", "infra") -- either the bare string or a
classify_red report dict {"attribution", "reasons"}. The module is a
standalone reader of that format (no blame import, mirroring
agent/repair/bisectreg.py's no-regguard-import precedent).

Contract:
- Unparseable-output failure (blame "model") maps to the "re-ask"
  fallback, and retry_with greens on a fixture fallback_fn.
- Every blame class maps to a pinned strategy: model -> re-ask,
  tool -> shrink-patch, tb -> strengthen-check, infra -> backoff-retry.
- retry_with returns {"strategy", "result"}; the fallback_fn receives
  the failure dict; fallback exceptions propagate (never a verdict).
- Malformed input (unknown/empty attribution, non-dict failure,
  non-callable fallback_fn) raises TypeError/ValueError loudly before
  any verdict -- never an invented fallback.
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no selfrepair wiring).

RED-first TDD: this file was written BEFORE
agent/repair/selfrepair.py existed, so the first run must FAIL on the
missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_selfrepair.py -v   (from repo root)
"""
import ast
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.repair import selfrepair  # noqa: E402  (RED before implementation)
from agent.trust import blame  # noqa: E402  real classifier, format-level use


def _model_failure():
    """An unparseable-output failure, attributed via the real classifier."""
    evidence = {"model_unparseable": True, "retries": 2, "max_retries": 2}
    report = blame.classify_red(evidence)
    assert report["attribution"] == "model"
    return {"attribution": report["attribution"],
            "reasons": report["reasons"],
            "evidence": evidence}


class UnparseableMapsToReask(unittest.TestCase):
    def test_bare_model_attribution_maps_to_reask(self):
        self.assertEqual(selfrepair.fallback_for("model"), "re-ask")

    def test_classify_red_report_maps_to_reask(self):
        evidence = {"model_unparseable": True, "retries": 2, "max_retries": 2}
        report = blame.classify_red(evidence)
        self.assertEqual(report["attribution"], "model")
        self.assertEqual(selfrepair.fallback_for(report), "re-ask")

    def test_retry_greens_on_fixture(self):
        failure = _model_failure()
        snapshot = copy.deepcopy(failure)

        def _fallback(fail):
            self.assertIs(fail, failure)
            return {"green": True, "patch": "re-asked-with-format"}

        outcome = selfrepair.retry_with(failure, _fallback)
        self.assertEqual(outcome["strategy"], "re-ask")
        self.assertTrue(outcome["result"]["green"])
        self.assertEqual(failure, snapshot)


class AllBlameClassesMap(unittest.TestCase):
    def test_tool_maps_to_shrink_patch(self):
        self.assertEqual(
            selfrepair.fallback_for("tool"), "shrink-patch")

    def test_tb_maps_to_strengthen_check(self):
        self.assertEqual(
            selfrepair.fallback_for("tb"), "strengthen-check")

    def test_infra_maps_to_backoff_retry(self):
        self.assertEqual(
            selfrepair.fallback_for("infra"), "backoff-retry")

    def test_reports_for_every_class_map(self):
        for attribution, strategy in (("tool", "shrink-patch"),
                                      ("tb", "strengthen-check"),
                                      ("model", "re-ask"),
                                      ("infra", "backoff-retry")):
            with self.subTest(attribution=attribution):
                report = {"attribution": attribution,
                          "reasons": ["pinned fixture reason"]}
                self.assertEqual(selfrepair.fallback_for(report), strategy)

    def test_real_classifier_outputs_map(self):
        cases = (({"guard_rejected": True}, "shrink-patch"),
                 ({"resim_mismatch": True}, "strengthen-check"),
                 ({"model_unparseable": True}, "re-ask"),
                 ({"api_error": "EMPTY_BODY"}, "backoff-retry"))
        for evidence, strategy in cases:
            with self.subTest(evidence=evidence):
                report = blame.classify_red(dict(evidence))
                self.assertEqual(
                    selfrepair.fallback_for(report), strategy)


class RefusesVacuousInput(unittest.TestCase):
    def test_unknown_attribution_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.fallback_for("person")

    def test_empty_attribution_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.fallback_for("")

    def test_none_attribution_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.fallback_for(None)

    def test_non_string_non_dict_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.fallback_for(42)

    def test_report_without_attribution_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.fallback_for({"reasons": ["no attribution here"]})

    def test_unclassified_evidence_dict_raises_tells_caller_to_classify(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.fallback_for({"model_unparseable": True})

    def test_retry_rejects_non_dict_non_string_failure(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.retry_with(42, lambda fail: {"green": True})
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.retry_with(["model"], lambda fail: True)

    def test_retry_accepts_bare_attribution_string(self):
        outcome = selfrepair.retry_with(
            "model", lambda fail: {"green": True})
        self.assertEqual(outcome["strategy"], "re-ask")
        self.assertTrue(outcome["result"]["green"])

    def test_retry_rejects_failure_without_attribution(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.retry_with({"nope": True}, lambda fail: True)

    def test_retry_rejects_non_callable_fallback(self):
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.retry_with(_model_failure(), "not-callable")
        with self.assertRaises((TypeError, ValueError)):
            selfrepair.retry_with(_model_failure(), None)


class HonestyContract(unittest.TestCase):
    def test_fallback_exceptions_propagate_never_a_verdict(self):
        def _boom(fail):
            raise RuntimeError("fallback exploded: boom")
        with self.assertRaises(RuntimeError):
            selfrepair.retry_with(_model_failure(), _boom)

    def test_failure_passed_through_untouched(self):
        seen = []

        def _spy(fail):
            seen.append(fail)
            return {"green": True}

        failure = _model_failure()
        snapshot = copy.deepcopy(failure)
        selfrepair.retry_with(failure, _spy)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0], failure)
        self.assertEqual(failure, snapshot)

    def test_deterministic_same_inputs_same_strategy(self):
        first = selfrepair.fallback_for("model")
        second = selfrepair.fallback_for({"attribution": "model",
                                          "reasons": ["r"]})
        self.assertEqual(first, second)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for name in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("selfrepair", text,
                             "%s must not wire in selfrepair" % name)

    def test_no_live_api(self):
        src = os.path.join(ROOT, "agent", "repair", "selfrepair.py")
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
                             "selfrepair.py must not touch live APIs: %r"
                             % token)

    def test_standalone_reader_no_loop_guard_trust_imports_no_io(self):
        path = os.path.join(ROOT, "agent", "repair", "selfrepair.py")
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
        for banned in ("agent.loop", "agent.guard", "agent.trust",
                       "loop", "guard", "blame", "regguard"):
            self.assertFalse(any(banned in name for name in imported),
                             "selfrepair must be a standalone reader "
                             "(no loop/guard/trust import), got %r" % imported)
        for banned in ("try_apply_diff", "save_registry", "record_fixed",
                       "open("):
            self.assertNotIn(banned, src,
                             "selfrepair must stay default-off/read-only: %r"
                             % banned)


if __name__ == "__main__":
    unittest.main()
