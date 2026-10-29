"""Audit-mode hash chain tests (Task D8-TRUE, plan item 38, Wave D).

`agent/trust/auditmode.py` dumps a hash-chained audit log from a
finished `runs/` bundle alone (bundle reads only, no API, no re-sim):

  api/text_request.json (prompt hash)
    -> api/text_response.json (request id + latency)
    -> patch.diff sha
    -> resim checksum

`dump_audit()` records every link hash-verified from bundle files;
`verify_audit()` checks the dump is internally consistent (shapes +
chain linkage); `audit_matches_bundle()` rebuilds from the bundle and
compares field-by-field, so any post-dump tampering breaks the chain.

Ground truth: the real GREEN bundle runs/mutpair-v9-05. Hardcoded
ids/hashes below are read verbatim from that bundle, so a passing test
proves the chain regenerates from real proof, not stubs.

RED-first TDD: this file was written BEFORE agent/trust/auditmode.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_auditmode -v   (from repo root)
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import auditmode  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_BUNDLE = os.path.join(ROOT, "runs", "mutpair-v9-05")

# Ground truth read verbatim from runs/mutpair-v9-05 (immutable bundle).
TEXT_PROMPT_HASH = "ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233"
TEXT_REQUEST_ID = "chatcmpl-01a182dd-0d17-71d2-9656-37b9189b8581"
TEXT_LATENCY_MS = 60350
PATCH_SHA = "03f8fa71592cb7a5d825e8ad71ee5750af93abcb669feb4a8308d4ba737b8140"
CHECKSUM_AFTER = "5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a"
CASE = "mut-pair-05"


def _read(bundle_rel):
    with open(os.path.join(LIVE_BUNDLE, bundle_rel),
              encoding="utf-8") as fh:
        return fh.read()


def _read_json(bundle_rel):
    return json.loads(_read(bundle_rel))


class TestLiveGreenChain(unittest.TestCase):
    def test_four_links_present_and_ordered(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        self.assertEqual(list(audit["links"].keys()),
                         ["prompt", "response", "patch", "resim"])

    def test_prompt_link_carries_hash_from_request_file(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        prompt = audit["links"]["prompt"]
        self.assertEqual(prompt["prompt_sha256"], TEXT_PROMPT_HASH)
        self.assertEqual(prompt["prompt_sha256"],
                         _read_json("api/text_request.json")
                         ["prompt_sha256"])
        # Hash-verified: file bytes hash recorded from the bundle file.
        raw = _read("api/text_request.json")
        self.assertEqual(prompt["file_sha256"],
                         hashlib.sha256(raw.encode("utf-8")).hexdigest())

    def test_response_link_carries_request_id_and_latency(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        resp = audit["links"]["response"]
        self.assertEqual(resp["request_id"], TEXT_REQUEST_ID)
        self.assertEqual(resp["request_id"],
                         _read_json("api/text_response.json")["request_id"])
        self.assertEqual(resp["latency_ms"], TEXT_LATENCY_MS)
        self.assertEqual(resp["latency_ms"],
                         _read_json("api/text_response.json")["latency_ms"])
        raw = _read("api/text_response.json")
        self.assertEqual(resp["file_sha256"],
                         hashlib.sha256(raw.encode("utf-8")).hexdigest())
        # Content hash recomputed from the bundle file itself.
        content = _read_json("api/text_response.json")["content"]
        self.assertEqual(resp["content_sha256"],
                         hashlib.sha256(content.encode("utf-8")).hexdigest())

    def test_patch_link_carries_diff_sha(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        patch = audit["links"]["patch"]
        self.assertEqual(patch["patch_sha256"], PATCH_SHA)
        self.assertEqual(patch["patch_sha256"],
                         hashlib.sha256(_read("patch.diff")
                                        .encode("utf-8")).hexdigest())

    def test_resim_link_carries_checksum(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        resim = audit["links"]["resim"]
        after = _read_json("artifacts/verdict_after.json")
        self.assertEqual(resim["checksum_after"], CHECKSUM_AFTER)
        self.assertEqual(resim["checksum_after"], after["checksum"])
        self.assertTrue(resim["pass"])
        self.assertEqual(resim["tohost_after"], "PASS")
        # Resim hash-verified: boot_after.log re-checksummed from bytes.
        self.assertTrue(resim["boot_checksum_match"])

    def test_chain_links_every_step(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        self.assertEqual(len(audit["chain"]), 4)
        self.assertEqual(audit["chain_head"], audit["chain"][-1])
        ok, reasons = auditmode.verify_audit(audit)
        self.assertTrue(ok, reasons)

    def test_regeneration_is_deterministic(self):
        first = auditmode.dump_audit(LIVE_BUNDLE)
        second = auditmode.dump_audit(LIVE_BUNDLE)
        self.assertEqual(first, second)
        ok, reasons = auditmode.audit_matches_bundle(first, LIVE_BUNDLE)
        self.assertTrue(ok, reasons)


class TestTamperDetection(unittest.TestCase):
    def _copy_live_bundle(self, tmp):
        dst = os.path.join(tmp, "tampered")
        shutil.copytree(LIVE_BUNDLE, dst, symlinks=False)
        return dst

    def test_modified_response_breaks_chain(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_live_bundle(tmp)
            resp_path = os.path.join(dst, "api", "text_response.json")
            with open(resp_path, encoding="utf-8") as fh:
                resp = json.load(fh)
            # Tamper: inflate the latency; content untouched.
            resp["latency_ms"] = int(resp["latency_ms"]) + 1
            with open(resp_path, "w", encoding="utf-8") as fh:
                json.dump(resp, fh)
            ok, reasons = auditmode.audit_matches_bundle(audit, dst)
            self.assertFalse(ok, "tampered response must break the chain")
            self.assertTrue(any("response" in r for r in reasons),
                            reasons)

    def test_modified_patch_breaks_chain(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        with tempfile.TemporaryDirectory() as tmp:
            dst = self._copy_live_bundle(tmp)
            patch_path = os.path.join(dst, "patch.diff")
            with open(patch_path, "a", encoding="utf-8") as fh:
                fh.write("\n# tampered\n")
            ok, reasons = auditmode.audit_matches_bundle(audit, dst)
            self.assertFalse(ok, "tampered patch must break the chain")
            self.assertTrue(any("patch" in r for r in reasons), reasons)

    def test_hand_edited_chain_head_rejected(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        audit["chain_head"] = "0" * 64
        ok, reasons = auditmode.verify_audit(audit)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    def test_hand_edited_link_rejected(self):
        audit = auditmode.dump_audit(LIVE_BUNDLE)
        audit["links"]["resim"]["checksum_after"] = "0" * 64
        ok, reasons = auditmode.verify_audit(audit)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    def test_missing_bundle_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                auditmode.dump_audit(tmp)


if __name__ == "__main__":
    unittest.main()
