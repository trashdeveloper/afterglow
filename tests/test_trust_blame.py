"""Blameless RED attribution tests (Task D9-TRUE, plan item 39, Wave D).

`agent/trust/blame.py` classifies a RED bundle's evidence into one of
{tool, tb, model, infra} with reasons, written to meta::

  classify_red(evidence) -> {"attribution", "reasons"}
  record_blame(meta, evidence) -> same report; sets meta["blame"]

Pinned fixtures:
- transport/API empty errors -> infra
- guard-rejected patch -> tool
- resim PASS/FAIL mismatch vs oracle -> tb
- unparseable model output after retries -> model

Precedence on combined signals: infra > tool > tb > model.
Empty/unknown evidence defaults to model (RED stands, model last writer).

RED-first TDD: this file was written BEFORE agent/trust/blame.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_blame -v (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import blame  # noqa: E402  (RED before implementation)

ATTRIBUTIONS = {"tool", "tb", "model", "infra"}


def _assert_report_shape(tc, report):
    tc.assertIsInstance(report, dict)
    tc.assertEqual(set(report.keys()), {"attribution", "reasons"})
    tc.assertIn(report["attribution"], ATTRIBUTIONS)
    tc.assertIsInstance(report["reasons"], list)
    tc.assertGreater(len(report["reasons"]), 0)
    for r in report["reasons"]:
        tc.assertIsInstance(r, str)
        tc.assertTrue(r.strip())


class TestPinnedFixtures(unittest.TestCase):
    def test_transport_api_empty_errors_are_infra(self):
        cases = [
            {"api_error": "connection reset by peer"},
            {"api_error": True},
            {"transport_error": "timeout after 30s"},
            {"transport_error": True},
            {"api_empty_response": True},
            {"api_error_markers": ["EMPTY_BODY"]},
            {"api_error_markers": "HTTP_503"},
            {"api_error_markers": True},
        ]
        for ev in cases:
            with self.subTest(ev=ev):
                report = blame.classify_red(ev)
                _assert_report_shape(self, report)
                self.assertEqual(report["attribution"], "infra")

    def test_guard_rejected_patch_is_tool(self):
        cases = [
            {"guard": {"accepted": False, "reason": "touches non-allowlisted file"}},
            {"guard": {"accepted": False}},
            {"guard_verdict": "rejected"},
            {"guard_verdict": "REJECTED"},
            {"guard_rejected": True},
        ]
        for ev in cases:
            with self.subTest(ev=ev):
                report = blame.classify_red(ev)
                _assert_report_shape(self, report)
                self.assertEqual(report["attribution"], "tool")

    def test_resim_oracle_mismatch_is_tb(self):
        cases = [
            {"resim_tohost": "PASS", "oracle_tohost": "FAIL"},
            {"resim_tohost": "FAIL", "oracle_tohost": "PASS"},
            {"resim": {"tohost": "PASS"}, "oracle": {"tohost": "FAIL"}},
            {"resim": {"tohost": "FAIL"}, "expected": {"tohost": "PASS"}},
            {"resim_mismatch": True},
        ]
        for ev in cases:
            with self.subTest(ev=ev):
                report = blame.classify_red(ev)
                _assert_report_shape(self, report)
                self.assertEqual(report["attribution"], "tb")

    def test_unparseable_model_output_after_retries_is_model(self):
        cases = [
            {"model_output": "garbage{{{ not json",
             "parse_ok": False, "retries": 2, "max_retries": 2},
            {"parse_ok": False, "retries": 2, "max_retries": 2},
            {"parse_ok": False},
            {"model_unparseable": True},
            {"retries_exhausted": True},
        ]
        for ev in cases:
            with self.subTest(ev=ev):
                report = blame.classify_red(ev)
                _assert_report_shape(self, report)
                self.assertEqual(report["attribution"], "model")


class TestPrecedence(unittest.TestCase):
    def test_infra_beats_tool(self):
        ev = {"api_error": "timeout",
              "guard": {"accepted": False, "reason": "too big"}}
        self.assertEqual(blame.classify_red(ev)["attribution"], "infra")

    def test_tool_beats_tb(self):
        ev = {"guard": {"accepted": False, "reason": "allowlist"},
              "resim_tohost": "PASS", "oracle_tohost": "FAIL"}
        self.assertEqual(blame.classify_red(ev)["attribution"], "tool")

    def test_tb_beats_model(self):
        ev = {"resim_tohost": "PASS", "oracle_tohost": "FAIL",
              "parse_ok": False, "retries": 2, "max_retries": 2}
        self.assertEqual(blame.classify_red(ev)["attribution"], "tb")


class TestShapeAndMeta(unittest.TestCase):
    def test_empty_evidence_defaults_to_model(self):
        report = blame.classify_red({})
        _assert_report_shape(self, report)
        self.assertEqual(report["attribution"], "model")

    def test_non_dict_evidence_raises(self):
        for bad in (None, "infra", 42, [("api_error", True)], True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    blame.classify_red(bad)

    def test_record_blame_writes_meta(self):
        meta = {}
        ev = {"api_error": "connection reset"}
        report = blame.record_blame(meta, ev)
        _assert_report_shape(self, report)
        self.assertEqual(report["attribution"], "infra")
        self.assertEqual(meta["blame"], report)

    def test_record_blame_bad_meta_raises_without_writing(self):
        for bad in (None, [], "meta", 42):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    blame.record_blame(bad, {"api_error": "x"})

    def test_record_blame_bad_evidence_raises_without_writing(self):
        meta = {}
        with self.assertRaises(ValueError):
            blame.record_blame(meta, None)
        self.assertNotIn("blame", meta)

    def test_context_fields_land_in_reasons(self):
        ev = {"parse_ok": False, "retries": 2, "max_retries": 2,
              "verdict_before": "FAIL", "exit_code": 1,
              "trace_lines": 200}
        report = blame.classify_red(ev)
        self.assertEqual(report["attribution"], "model")
        joined = " ".join(report["reasons"])
        self.assertIn("FAIL", joined)
        self.assertIn("1", joined)


if __name__ == "__main__":
    unittest.main()
