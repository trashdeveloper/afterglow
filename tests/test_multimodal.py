"""Multimodal feedback pilot (TDD, fixtures only -- no API/keys/builds).

CONTEXT: image is one-way (text -> image -> human). This pilot feeds
explain.png BACK into a second diagnosis call per attempt (same trace
window + first diagnosis + the diagram) that CONFIRMs or REVISEs the
first diagnosis; REVISE supersedes for PATCH.

What this file proves (mocked model-call + image-call layers only):
- confirm-path proceeds with the identical patch (2 mock calls, 1 resim
  GREEN, `multimodal: CONFIRMED` in meta + text_request).
- revise-path supersedes for PATCH (2 mock calls, 1 resim GREEN, both
  diagnoses + delta in meta + text_request).
- image-fail degrades to single-diagnosis with
  `multimodal: OFF-image-failed` logged (1 mock call, never blocks).
- cost-cap degrades to single-diagnosis with
  `multimodal: OFF-cost-cap` logged (1 mock call).
- default-off byte-identical: without --multimodal, one mock call per
  attempt and no multimodal keys anywhere (existing diagnose tests
  pass unmodified).
- second-call failure degrades to single-diagnosis (never blocks).
- decision rule (pure): REVISE only on literal REVISE (case-sensitive;
  lowercase 'revise' CONFIRMs) + non-empty diagnosis + non-empty
  patch_diff different from the first (whitespace-insensitive).

Run: PYTHONPATH=. python3 tests/test_multimodal.py -v  (repo root)
"""
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASE = "uart-start-half"

FAKE_BOOT_RED = (
    "cycle 0: reset assert\n"
    "cycle 1: fetch pc=0x00\n"
    "TRAP: uart recv_divcnt mismatch got 0xE0 want 0xA5\n"
    "tohost: FAIL 0xdead\n"
    "CYCLES=1234\n"
)
FAKE_VERDICT = {"case": CASE, "pass": False, "tohost": "FAIL 0xdead",
                "checksum": "deadbeef", "cycles": 1234, "seed": 42}

DIFF_C1 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1446,4 +1446,4 @@
-wire decoder_trigger <= mem_do_rinst || mem_done;
+wire decoder_trigger <= mem_do_rinst;
"""

DIFF_C4 = """diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v
--- a/picosoc/simpleuart.v
+++ b/picosoc/simpleuart.v
@@ -10,1 +10,1 @@
-recv_divcnt <= recv_divcnt + 1;
+recv_divcnt <= recv_divcnt + 2;
"""


def _fake_red_sim(case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write(FAKE_BOOT_RED)
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(FAKE_VERDICT, case=case, seed=seed)) + "\n")
    return 1, "mock RED"


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _resp(diff, n, usage=None):
    return ({"content": json.dumps(
        {"diagnosis": "mock diagnosis %d" % n,
         "patch_diff": diff, "expected": "tohost: PASS"}),
        "raw": {}, "latency_ms": 5,
        "request_id": "mock-r%d" % n,
        "model": loop.TEXT_MODEL, "usage": dict(usage or {})}, [])


def _mm_resp(decision, diagnosis, diff, n=99):
    return ({"content": json.dumps(
        {"decision": decision, "diagnosis": diagnosis,
         "patch_diff": diff, "reason": "mock diagram review"}),
        "raw": {}, "latency_ms": 5,
        "request_id": "mock-mm-%d" % n,
        "model": loop.TEXT_MODEL, "usage": {}}, [])


def _run_loop(run_id, effects, resim_fn, extra_args=None,
              seed_cost_json=None, image_effect=None):
    """Run loop.main with mocked seams; returns (rc, run_dir, prompts)."""
    run_dir = os.path.join(ROOT, "runs", run_id)
    prompts = []
    applied = []

    def fake_call(prompt):
        prompts.append(prompt)
        eff = effects[len(prompts) - 1]
        if isinstance(eff, Exception):
            raise eff
        return eff

    def fake_apply(diff_text, tree, tmp_path, spec=None):
        applied.append(diff_text)
        return {"guard_ok": True, "reasons": [], "applied": True,
                "repaired": False, "patch_lines": 2,
                "files": ["mock.v"], "applied_diff": diff_text}

    patches = [
        mock.patch.object(loop, "run_sim_before",
                          side_effect=_fake_red_sim),
        mock.patch.object(loop, "rtl_excerpt",
                          return_value="line 1: fake excerpt"),
        mock.patch.object(loop, "call_text_with_backoff",
                          side_effect=fake_call),
        mock.patch.object(loop, "try_apply_diff",
                          side_effect=fake_apply),
        mock.patch.object(loop, "resim_after", side_effect=resim_fn),
    ]
    if isinstance(image_effect, Exception):
        patches.append(mock.patch.object(
            loop.visual, "generate", side_effect=image_effect))
    else:
        patches.append(mock.patch.object(
            loop.visual, "generate", return_value={
                "latency_ms": 1, "request_id": "img-mock", "bytes": 10,
                "sha256": "ab" * 32, "transport": "mock",
                "model": "mock-image"}))
    for p in patches:
        p.start()
    try:
        if seed_cost_json is not None:
            os.makedirs(run_dir, exist_ok=True)
            with open(os.path.join(run_dir, "cost.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(seed_cost_json) + "\n")
        v3 = os.path.join(ROOT, "agent", "prompts", "v3.txt")
        argv = ["--case", CASE, "--seed", "42",
                "--run-id", run_id, "--prompt", v3]
        argv.extend(extra_args or [])
        rc = loop.main(argv)
    finally:
        for p in patches:
            try:
                p.stop()
            except RuntimeError:
                pass
    return rc, run_dir, prompts, applied


def _green_resim(tree, case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write("mock boot after\ntohost: PASS 0x1\n")
    verdict = {"case": case["name"], "pass": True,
               "tohost": "PASS 0x1", "checksum": "cafef00d",
               "cycles": 2009, "seed": seed,
               "sim_time_utc": loop.utc_now()}
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(verdict) + "\n")
    return verdict


class MultimodalDecisionRule(unittest.TestCase):
    def test_revise_needs_literal_revise_plus_new_diff(self):
        out = loop.parse_multimodal_output(
            json.dumps({"decision": "REVISE", "diagnosis": "new cause",
                        "patch_diff": DIFF_C4, "reason": "diagram shows it"}),
            "old cause", DIFF_C1)
        self.assertTrue(out["revised"])
        self.assertEqual(out["decision"], "REVISE")
        self.assertEqual(out["diagnosis"], "new cause")
        self.assertEqual(out["patch_diff"].strip(), DIFF_C4.strip())

    def test_lowercase_revise_with_new_diff_confirms(self):
        out = loop.parse_multimodal_output(
            json.dumps({"decision": "revise", "diagnosis": "new cause",
                        "patch_diff": DIFF_C4, "reason": "diagram shows it"}),
            "old cause", DIFF_C1)
        self.assertFalse(out["revised"])
        self.assertEqual(out["decision"], "CONFIRM")
        self.assertEqual(out["diagnosis"], "old cause")
        self.assertEqual(out["patch_diff"], DIFF_C1)

    def test_confirm_keeps_first_on_identical_diff(self):
        out = loop.parse_multimodal_output(
            json.dumps({"decision": "REVISE", "diagnosis": "new cause",
                        "patch_diff": DIFF_C1, "reason": "same"}),
            "old cause", DIFF_C1)
        self.assertFalse(out["revised"])
        self.assertEqual(out["decision"], "CONFIRM")
        self.assertEqual(out["patch_diff"], DIFF_C1)

    def test_revise_without_diff_confirms(self):
        out = loop.parse_multimodal_output(
            json.dumps({"decision": "REVISE", "diagnosis": "new cause",
                        "patch_diff": "", "reason": "incomplete"}),
            "old cause", DIFF_C1)
        self.assertFalse(out["revised"])
        self.assertEqual(out["diagnosis"], "old cause")

    def test_garbage_confirms(self):
        out = loop.parse_multimodal_output("not json at all",
                                           "old cause", DIFF_C1)
        self.assertFalse(out["revised"])
        self.assertEqual(out["diagnosis"], "old cause")

    def test_prompt_binds_trace_and_diagram(self):
        prompt = loop.build_multimodal_prompt(
            CASE, "TRAP: line", "first cause", DIFF_C1, "ab" * 32)
        self.assertIn("ababab", prompt)
        self.assertIn("first cause", prompt)
        self.assertIn("TRAP: line", prompt)
        self.assertIn("CONFIRM", prompt)


class MultimodalLoop(unittest.TestCase):
    def tearDown(self):
        for rid in ("test-mm-confirm", "test-mm-revise",
                    "test-mm-imagefail", "test-mm-costcap",
                    "test-mm-defaultoff", "test-mm-secondfail"):
            shutil.rmtree(os.path.join(ROOT, "runs", rid),
                          ignore_errors=True)

    def _start(self, run_id, effects, resim_fn, extra_args=None,
               seed_cost_json=None, image_effect=None):
        run_dir = os.path.join(ROOT, "runs", run_id)
        shutil.rmtree(run_dir, ignore_errors=True)
        return _run_loop(run_id, effects, resim_fn, extra_args,
                         seed_cost_json, image_effect)

    def test_confirm_path_proceeds_with_identical_patch(self):
        effects = [_resp(DIFF_C1, 1),
                   _mm_resp("CONFIRM", "mock diagnosis 1", DIFF_C1)]
        rc, run_dir, prompts, applied = self._start(
            "test-mm-confirm", effects, _green_resim, ["--multimodal"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(prompts), 2)  # first + second call
        self.assertEqual(applied, [DIFF_C1])  # identical patch
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertIn("multimodal: CONFIRMED", json.dumps(meta))
        treq = _read_json(os.path.join(run_dir, "api",
                                       "text_request.json"))
        self.assertIn("multimodal: CONFIRMED", json.dumps(treq))
        mm_resp = _read_json(os.path.join(run_dir, "api",
                                          "multimodal_response.json"))
        self.assertEqual(mm_resp.get("decision"), "CONFIRM")
        self.assertFalse(mm_resp.get("revised"))

    def test_revise_path_supersedes_and_logs_delta(self):
        effects = [_resp(DIFF_C1, 1),
                   _mm_resp("REVISE", "revised cause", DIFF_C4)]
        rc, run_dir, prompts, applied = self._start(
            "test-mm-revise", effects, _green_resim, ["--multimodal"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(prompts), 2)
        self.assertEqual([a.strip() for a in applied],
                         [DIFF_C4.strip()])  # revised supersedes
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        blob = json.dumps(meta)
        self.assertIn("multimodal: REVISED", blob)
        attempt = meta["attempts"][0]
        detail = attempt.get("multimodal_detail", {})
        self.assertEqual(detail.get("decision"), "REVISE")
        self.assertTrue(detail.get("patch_changed"))
        self.assertNotEqual(detail.get("first_patch"),
                            detail.get("final_patch"))
        treq = _read_json(os.path.join(run_dir, "api",
                                       "text_request.json"))
        self.assertIn("multimodal: REVISED", json.dumps(treq))
        self.assertIn("first_patch", json.dumps(treq))
        tresp = _read_json(os.path.join(run_dir, "api",
                                        "text_response.json"))
        self.assertEqual(tresp.get("diagnosis"), "revised cause")

    def test_image_fail_degrades_to_single(self):
        effects = [_resp(DIFF_C1, 1)]
        rc, run_dir, prompts, applied = self._start(
            "test-mm-imagefail", effects, _green_resim, ["--multimodal"],
            image_effect=loop.visual.LiveImageError("mock image down"))
        self.assertEqual(rc, 0)  # never blocks
        self.assertEqual(len(prompts), 1)  # no second call
        self.assertEqual(applied, [DIFF_C1])
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("explain"), "FAILED")
        self.assertIn("multimodal: OFF-image-failed", json.dumps(meta))
        treq = _read_json(os.path.join(run_dir, "api",
                                       "text_request.json"))
        self.assertIn("multimodal: OFF-image-failed",
                      json.dumps(treq))

    def test_cost_cap_degrades_to_single(self):
        effects = [_resp(DIFF_C1, 1)]
        rc, run_dir, prompts, applied = self._start(
            "test-mm-costcap", effects, _green_resim, ["--multimodal"],
            seed_cost_json={"est_usd": 0.99, "text_tokens": 99999,
                            "image_calls": 3})
        self.assertEqual(rc, 0)
        self.assertEqual(len(prompts), 1)  # no second call
        self.assertEqual(applied, [DIFF_C1])
        treq = _read_json(os.path.join(run_dir, "api",
                                       "text_request.json"))
        self.assertIn("multimodal: OFF-cost-cap", json.dumps(treq))
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertIn("OFF-cost-cap", json.dumps(meta))

    def test_second_call_failure_degrades_to_single(self):
        effects = [_resp(DIFF_C1, 1),
                   loop.LiveAPIError("mock second call down")]
        rc, run_dir, prompts, applied = self._start(
            "test-mm-secondfail", effects, _green_resim,
            ["--multimodal"])
        self.assertEqual(rc, 0)  # never blocks
        self.assertEqual(len(prompts), 2)
        self.assertEqual(applied, [DIFF_C1])  # first diagnosis stands
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertIn("multimodal: OFF-second-call-failed",
                      json.dumps(meta))

    def test_default_off_is_byte_identical_single_path(self):
        effects = [_resp(DIFF_C1, 1)]
        rc, run_dir, prompts, applied = self._start(
            "test-mm-defaultoff", effects, _green_resim)  # no flag
        self.assertEqual(rc, 0)
        self.assertEqual(len(prompts), 1)  # no second call
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertNotIn("multimodal", json.dumps(meta).lower())
        treq = _read_json(os.path.join(run_dir, "api",
                                       "text_request.json"))
        self.assertNotIn("multimodal", json.dumps(treq).lower())
        tresp = _read_json(os.path.join(run_dir, "api",
                                        "text_response.json"))
        self.assertNotIn("multimodal", json.dumps(tresp).lower())


if __name__ == "__main__":
    unittest.main()
