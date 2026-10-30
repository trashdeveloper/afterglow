"""Third-party replay certification tests (Task H7, plan item 87).

`agent/trust/replaycert.py` certifies a finished proof bundle from a
scratch copy: it stages `bundle_dir` into `clean_checkout`, replays the
verdict via `agent/trust/replay.py`, verifies the hash chain via
`agent/trust/auditmode.py`, and writes a stamp file.

- A real GREEN bundle certifies from a scratch copy.
- A tampered copy fails, with reasons naming the broken link.

Unit tests use an injected fake sim_fn: fast, offline, deterministic.
A live test replays the locked GREEN bundle
runs/live-20261026T174854Z-picosoc-uart-div with the REAL Verilator
backend (sim-only proof, no API key).

RED-first TDD: this file was written BEFORE agent/trust/replaycert.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_replaycert -v   (from repo root)
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import replaycert  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_BUNDLE = os.path.join(ROOT, "runs", "live-20261026T174854Z-picosoc-uart-div")

FAKE_BOOT_PASS = (
    "- tb noise line (excluded from checksum)\n"
    "cycle 0: reset assert\n"
    "loopback send 0xa5 got 0xa5\n"
    "tohost: PASS\n"
    "CYCLES=2652\n"
)

FAKE_BOOT_OTHER = (
    "cycle 0: reset assert\n"
    "loopback send 0xa5 got 0xe0\n"
    "tohost: FAIL 0xdead\n"
    "CYCLES=2112\n"
)

FAKE_PATCH = (
    "diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v\n"
    "--- a/picosoc/simpleuart.v\n"
    "+++ b/picosoc/simpleuart.v\n"
    "@@ -89,1 +89,1 @@\n"
    "-old\n"
    "+new\n"
)

FAKE_PROMPT = "diagnose the uart divisor bug from this trace"
FAKE_CONTENT = "the divisor is off by one; patch line 89"


def _det_checksum(boot_text):
    det = [ln for ln in boot_text.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def _make_green_bundle(path, boot_text=FAKE_BOOT_PASS, seed=42):
    """A synthetic GREEN bundle satisfying replay + auditmode readers."""
    os.makedirs(os.path.join(path, "artifacts"), exist_ok=True)
    os.makedirs(os.path.join(path, "api"), exist_ok=True)
    with open(os.path.join(path, "patch.diff"), "w", encoding="utf-8") as fh:
        fh.write(FAKE_PATCH)
    with open(os.path.join(path, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"run_id": os.path.basename(path), "case": "picosoc-uart-div",
                   "seed": seed, "mode": "live", "status": "green"}, fh)
    with open(os.path.join(path, "artifacts", "verdict_after.json"),
              "w", encoding="utf-8") as fh:
        json.dump({"case": "picosoc-uart-div", "pass": True,
                   "tohost": "PASS", "checksum": _det_checksum(boot_text),
                   "cycles": 2652, "seed": seed}, fh)
    with open(os.path.join(path, "artifacts", "boot_after.log"),
              "w", encoding="utf-8") as fh:
        fh.write(boot_text)
    with open(os.path.join(path, "api", "text_request.json"),
              "w", encoding="utf-8") as fh:
        json.dump({"prompt_sha256": hashlib.sha256(
            FAKE_PROMPT.encode("utf-8")).hexdigest(),
            "prompt": FAKE_PROMPT, "model": "test-model-v1",
            "tokens": 10}, fh)
    with open(os.path.join(path, "api", "text_response.json"),
              "w", encoding="utf-8") as fh:
        json.dump({"request_id": "chatcmpl-test-0001", "latency_ms": 123,
                   "content": FAKE_CONTENT, "model": "test-model-v1"}, fh)
    return path


def _fake_sim_factory(boot_text):
    def _sim(case, seed, patch_text, work_dir):
        assert patch_text.strip(), "sim must receive the bundle patch"
        verdict = {"case": case, "pass": "tohost: PASS" in boot_text,
                   "tohost": "PASS" if "tohost: PASS" in boot_text
                   else "FAIL 0xdead",
                   "checksum": _det_checksum(boot_text),
                   "cycles": 2652 if "PASS" in boot_text else 2112,
                   "seed": seed}
        return boot_text, verdict
    return _sim


class TestCertifyGreenFromScratchCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trust-replaycert-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bundle = _make_green_bundle(os.path.join(self.tmp, "bundle"))

    def test_green_bundle_certifies_from_scratch_copy(self):
        scratch = os.path.join(self.tmp, "clean-checkout")
        res = replaycert.certify(self.bundle, scratch,
                                 sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["certified"], res)
        self.assertEqual(res["reasons"], ["OK: replay MATCH + audit chain verified"])
        # The scratch copy carries everything; the original path is unreferenced.
        self.assertTrue(os.path.isdir(scratch), "scratch copy must exist")
        for name in ("patch.diff", "meta.json",
                     "artifacts/verdict_after.json"):
            self.assertTrue(os.path.isfile(os.path.join(scratch, name)), name)

    def test_stamp_file_written(self):
        scratch = os.path.join(self.tmp, "clean-checkout")
        res = replaycert.certify(self.bundle, scratch,
                                 sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["certified"], res)
        stamp = res.get("stamp", "")
        self.assertTrue(stamp, "certify must return a stamp path")
        self.assertTrue(os.path.isfile(stamp), stamp)
        with open(stamp, encoding="utf-8") as fh:
            stamped = json.load(fh)
        self.assertTrue(stamped["certified"])
        self.assertIn("chain_head", stamped)

    def test_certify_accepts_prestaged_scratch_copy(self):
        # Third-party flow: the copy already exists; certify from it as-is.
        scratch = os.path.join(self.tmp, "prestaged")
        shutil.copytree(self.bundle, scratch)
        res = replaycert.certify(self.bundle, scratch,
                                 sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["certified"], res)


class TestTamperedCopyFailsNamingBrokenLink(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trust-replaycert-tamper-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bundle = _make_green_bundle(os.path.join(self.tmp, "bundle"))

    def test_tampered_patch_fails_naming_patch_link(self):
        scratch = os.path.join(self.tmp, "clean-checkout")
        shutil.copytree(self.bundle, scratch)
        with open(os.path.join(scratch, "patch.diff"), "a",
                  encoding="utf-8") as fh:
            fh.write("# tampered\n")
        res = replaycert.certify(self.bundle, scratch,
                                 sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertFalse(res["certified"], res)
        self.assertTrue(any("patch" in r for r in res["reasons"]), res)

    def test_tampered_verdict_fails_naming_resim_link(self):
        scratch = os.path.join(self.tmp, "clean-checkout")
        shutil.copytree(self.bundle, scratch)
        vpath = os.path.join(scratch, "artifacts", "verdict_after.json")
        with open(vpath, encoding="utf-8") as fh:
            verdict = json.load(fh)
        bad = ("0" if verdict["checksum"][0] != "0" else "1") \
            + verdict["checksum"][1:]
        verdict["checksum"] = bad
        with open(vpath, "w", encoding="utf-8") as fh:
            json.dump(verdict, fh)
        res = replaycert.certify(self.bundle, scratch,
                                 sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertFalse(res["certified"], res)
        self.assertTrue(any("resim" in r for r in res["reasons"]), res)

    def test_replay_mismatch_fails(self):
        scratch = os.path.join(self.tmp, "clean-checkout")
        res = replaycert.certify(self.bundle, scratch,
                                 sim_fn=_fake_sim_factory(FAKE_BOOT_OTHER))
        self.assertFalse(res["certified"], res)
        self.assertTrue(any("replay" in r.lower() or "mismatch" in r.lower()
                            for r in res["reasons"]), res)


class TestLiveGreenCertifies(unittest.TestCase):
    """Sim-only proof: real Verilator replay of the locked live bundle."""

    def test_live_green_bundle_certifies(self):
        self.assertTrue(os.path.isdir(LIVE_BUNDLE),
                        "live bundle missing: %s" % LIVE_BUNDLE)
        tmp = tempfile.mkdtemp(prefix="trust-replaycert-live-")
        self.addCleanup(shutil.rmtree, tmp, True)
        scratch = os.path.join(tmp, "clean-checkout")
        res = replaycert.certify(LIVE_BUNDLE, scratch)
        self.assertTrue(res["certified"], res)
        self.assertTrue(os.path.isfile(res["stamp"]), res)


if __name__ == "__main__":
    unittest.main()
