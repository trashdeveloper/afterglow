"""Provenance-chain trail tests (Task D4-TRUE, plan item 34, Wave D).

`agent/trust/provenance.py` rebuilds ONE JSON trail
fault -> diagnosis -> patch -> green from a finished `runs/` bundle
alone (bundle reads only, no API, no re-sim). Each step carries its
request id where one exists:

- fault:    case + mutant record (+ RED verdict); no API call, id None
- diagnosis: text api request id + prompt hash (+ image request id)
- patch:    patch.diff sha256 + guard verdict; authored by text call
- green:    resim checksum + verdict_after; local sim, id None

Ground truth: the real GREEN bundle runs/mutpair-v9-05
(task text says "mutbigN-v9-05"; no such dir exists -- mutpair-v9-05
is the v9-05 GREEN bundle with api/ request ids + verdicts).
Hardcoded ids/hashes below are read verbatim from that bundle, so a
passing test proves the trail regenerates from real proof, not stubs.

RED-first TDD: this file was written BEFORE agent/trust/provenance.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_provenance -v   (from repo root)
"""
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import provenance  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_BUNDLE = os.path.join(ROOT, "runs", "mutpair-v9-05")

# Ground truth read verbatim from runs/mutpair-v9-05 (immutable bundle).
TEXT_REQUEST_ID = "chatcmpl-01a182dd-0d17-71d2-9656-37b9189b8581"
TEXT_PROMPT_HASH = "ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233"
IMAGE_REQUEST_ID = "1d51332f-55a4-4550-b022-61c9f30538f0"
CHECKSUM_BEFORE = "563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15"
CHECKSUM_AFTER = "5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a"
CASE = "mut-pair-05"


def _read(bundle_rel):
    with open(os.path.join(LIVE_BUNDLE, bundle_rel), encoding="utf-8") as fh:
        return fh.read()


def _read_json(bundle_rel):
    return json.loads(_read(bundle_rel))


class TestLiveGreenTrail(unittest.TestCase):
    def test_four_steps_present_and_ordered(self):
        trail = provenance.build_trail(LIVE_BUNDLE)
        self.assertEqual(list(trail["steps"].keys()),
                         ["fault", "diagnosis", "patch", "green"])

    def test_fault_carries_case_and_mutant_record(self):
        trail = provenance.build_trail(LIVE_BUNDLE)
        fault = trail["steps"]["fault"]
        self.assertEqual(fault["case"], CASE)
        self.assertEqual(trail["case"], CASE)
        mutant = fault["mutant_record"]
        self.assertEqual(mutant["file"], "picorv32.v")
        lines = sorted(f["line"] for f in mutant["faults"])
        self.assertEqual(lines, [1408, 1450])
        self.assertEqual(fault["checksum_before"], CHECKSUM_BEFORE)
        self.assertEqual(fault["tohost_before"], "FAIL 0xdead")
        self.assertIsNone(fault["request_id"])

    def test_diagnosis_carries_request_id_and_prompt_hash(self):
        trail = provenance.build_trail(LIVE_BUNDLE)
        diag = trail["steps"]["diagnosis"]
        self.assertEqual(diag["request_id"], TEXT_REQUEST_ID)
        self.assertEqual(diag["request_id"],
                         _read_json("api/text_response.json")["request_id"])
        self.assertEqual(diag["prompt_hash"], TEXT_PROMPT_HASH)
        self.assertEqual(diag["prompt_hash"],
                         _read_json("api/text_request.json")["prompt_sha256"])
        self.assertTrue(diag["diagnosis"].strip())
        self.assertEqual(diag["image_request_id"], IMAGE_REQUEST_ID)
        self.assertEqual(diag["image_request_id"],
                         _read_json("api/image_response.json")["request_id"])

    def test_patch_carries_sha_and_guard_verdict(self):
        trail = provenance.build_trail(LIVE_BUNDLE)
        patch = trail["steps"]["patch"]
        self.assertEqual(patch["patch_sha256"],
                         hashlib.sha256(_read("patch.diff").encode("utf-8")).hexdigest())
        self.assertEqual(patch["request_id"], TEXT_REQUEST_ID)
        self.assertTrue(patch["guard_ok"])
        self.assertEqual(patch["guard_reasons"], [])
        self.assertGreaterEqual(patch["patch_lines"], 1)
        self.assertLessEqual(patch["patch_lines"], 40)

    def test_green_carries_resim_checksum_and_verdict(self):
        trail = provenance.build_trail(LIVE_BUNDLE)
        green = trail["steps"]["green"]
        after = _read_json("artifacts/verdict_after.json")
        self.assertEqual(green["checksum_after"], CHECKSUM_AFTER)
        self.assertEqual(green["checksum_after"], after["checksum"])
        self.assertTrue(green["pass"])
        self.assertEqual(green["tohost_after"], "PASS")
        self.assertNotEqual(green["checksum_after"], CHECKSUM_BEFORE)
        self.assertIsNone(green["request_id"])

    def test_request_id_on_every_step_where_one_exists(self):
        trail = provenance.build_trail(LIVE_BUNDLE)
        steps = trail["steps"]
        self.assertIsNone(steps["fault"]["request_id"])
        self.assertTrue(steps["diagnosis"]["request_id"])
        self.assertTrue(steps["patch"]["request_id"])
        self.assertIsNone(steps["green"]["request_id"])

    def test_verify_accepts_live_trail(self):
        trail = provenance.build_trail(LIVE_BUNDLE)
        ok, reasons = provenance.verify_trail(trail)
        self.assertTrue(ok, reasons)

    def test_regeneration_is_deterministic(self):
        first = provenance.build_trail(LIVE_BUNDLE)
        second = provenance.build_trail(LIVE_BUNDLE)
        self.assertEqual(first, second)
        ok, reasons = provenance.trail_matches_bundle(first, LIVE_BUNDLE)
        self.assertTrue(ok, reasons)


class TestVerifyRejects(unittest.TestCase):
    def _live_trail(self):
        return provenance.build_trail(LIVE_BUNDLE)

    def test_rejects_tampered_checksum(self):
        trail = self._live_trail()
        trail["steps"]["green"]["checksum_after"] = "0" * 64
        ok, reasons = provenance.verify_trail(trail)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    def test_rejects_missing_step(self):
        trail = self._live_trail()
        del trail["steps"]["patch"]
        ok, reasons = provenance.verify_trail(trail)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    def test_rejects_red_green_step(self):
        trail = self._live_trail()
        trail["steps"]["green"]["pass"] = False
        trail["steps"]["green"]["tohost_after"] = "FAIL 0xdead"
        ok, reasons = provenance.verify_trail(trail)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    def test_rejects_missing_request_id(self):
        trail = self._live_trail()
        trail["steps"]["diagnosis"]["request_id"] = None
        ok, _ = provenance.verify_trail(trail)
        self.assertFalse(ok)

    def test_rejects_reordered_steps(self):
        # "4 ordered steps": a swapped-order trail must not verify,
        # and must not match the bundle either (dict == is order-blind).
        trail = self._live_trail()
        keys = list(trail["steps"].keys())
        reordered = {k: trail["steps"][k] for k in reversed(keys)}
        trail["steps"] = reordered
        ok, reasons = provenance.verify_trail(trail)
        self.assertFalse(ok, reasons)
        self.assertTrue(any("order" in r for r in reasons))
        ok2, _ = provenance.trail_matches_bundle(trail, LIVE_BUNDLE)
        self.assertFalse(ok2)

    def test_rejects_trail_from_red_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_bundle(tmp, green_pass=False)
            trail = provenance.build_trail(tmp)
            ok, reasons = provenance.verify_trail(trail)
            self.assertFalse(ok, "RED bundle must not verify as a green trail")
            self.assertTrue(reasons)

    def test_missing_bundle_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                provenance.build_trail(tmp)

    def _write_bundle(self, root, green_pass):
        os.makedirs(os.path.join(root, "api"))
        os.makedirs(os.path.join(root, "artifacts"))
        patch = ("diff --git a/picorv32.v b/picorv32.v\n"
                 "--- a/picorv32.v\n"
                 "+++ b/picorv32.v\n"
                 "@@ -1450,1 +1450,1 @@\n"
                 "-do_waitirq <= 1;\n"
                 "+do_waitirq <= 0;\n")
        with open(os.path.join(root, "patch.diff"), "w",
                  encoding="utf-8") as fh:
            fh.write(patch)
        meta = {"run_id": "synth-red", "case": CASE,
                "mutant_record": {"file": "picorv32.v", "line": 1450,
                                  "before": "do_waitirq <= 0;",
                                  "after": "do_waitirq <= 1;",
                                  "faults": [{"file": "picorv32.v",
                                              "line": 1450,
                                              "before": "do_waitirq <= 0;",
                                              "after": "do_waitirq <= 1;"}]},
                "seed": 42}
        with open(os.path.join(root, "meta.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(meta, fh)
        req = {"prompt_sha256": "a" * 64, "model": "muse-spark-1.3"}
        with open(os.path.join(root, "api", "text_request.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(req, fh)
        resp = {"request_id": "chatcmpl-synth",
                "diagnosis": "synthetic red diagnosis",
                "latency_ms": 1, "model": "muse-spark-1.3"}
        with open(os.path.join(root, "api", "text_response.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(resp, fh)
        before = {"case": CASE, "pass": False, "tohost": "FAIL 0xdead",
                  "checksum": CHECKSUM_BEFORE, "cycles": 2009, "seed": 42}
        with open(os.path.join(root, "artifacts", "verdict_before.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(before, fh)
        after = {"case": CASE, "pass": green_pass,
                 "tohost": "PASS" if green_pass else "FAIL 0xdead",
                 "checksum": CHECKSUM_AFTER if green_pass else CHECKSUM_BEFORE,
                 "cycles": 2009, "seed": 42}
        with open(os.path.join(root, "artifacts", "verdict_after.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(after, fh)


if __name__ == "__main__":
    unittest.main()
