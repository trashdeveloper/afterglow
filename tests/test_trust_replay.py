"""Proof-bundle v2 replay tests (Task D1, TDD).

`agent/trust/replay.py` regenerates a bundle's AFTER verdict from the
bundle contents alone (patch.diff + meta.json + artifacts/verdict_after.json
+ the repo sim backend) and `write_replay_sh()` drops an executable
`REPLAY.sh` into the bundle so a fresh copy replays to an identical
checksum.

- Unit tests use an injected fake sim_fn: fast, offline, deterministic.
- TestLiveReplayPicosocUartDiv uses the REAL Verilator backend against the
  locked live bundle runs/live-20261026T174854Z-picosoc-uart-div and
  asserts the replayed checksum is identical (sim-only proof, no API key).

Run: python3 -m unittest tests.test_trust_replay -v   (from repo root)
"""
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import replay  # noqa: E402  (fails until replay.py exists)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_BUNDLE = os.path.join(ROOT, "runs", "live-20261026T174854Z-picosoc-uart-div")
LIVE_CHECKSUM = "5c8f58d0f0818c97d014d2f851a6140882bd1efc96a2537047806252c0c2caf9"


def _det_checksum(boot_text):
    det = [ln for ln in boot_text.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


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


def _make_bundle(path, boot_text=FAKE_BOOT_PASS, seed=42):
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
    return path


def _fake_sim_factory(boot_text):
    def _sim(case, seed, patch_text, work_dir):
        assert patch_text.strip(), "sim must receive the bundle patch"
        verdict = {"case": case, "pass": "tohost: PASS" in boot_text,
                   "tohost": "PASS" if "tohost: PASS" in boot_text else "FAIL 0xdead",
                   "checksum": _det_checksum(boot_text),
                   "cycles": 2652 if "PASS" in boot_text else 2112,
                   "seed": seed}
        return boot_text, verdict
    return _sim


class TestChecksumAlgorithm(unittest.TestCase):
    def test_dash_lines_excluded_like_vbackend(self):
        got = replay.checksum_of_boot(FAKE_BOOT_PASS)
        self.assertEqual(got, _det_checksum(FAKE_BOOT_PASS))
        self.assertNotIn("- tb noise", hashlib.sha256(b"- tb noise line (excluded from checksum)\n").hexdigest())

    def test_empty_boot_is_stable(self):
        self.assertEqual(replay.checksum_of_boot(""), _det_checksum(""))


class TestReplayMatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trust-replay-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bundle = _make_bundle(os.path.join(self.tmp, "bundle"))

    def test_matching_verdict_replays_ok(self):
        res = replay.replay_bundle(self.bundle,
                                   sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["replayed_checksum"], res["expected_checksum"])
        self.assertEqual(res["reason"], "checksum MATCH")

    def test_mismatched_checksum_is_not_ok(self):
        res = replay.replay_bundle(self.bundle,
                                   sim_fn=_fake_sim_factory(FAKE_BOOT_OTHER))
        self.assertFalse(res["ok"], res)
        self.assertIn("MISMATCH", res["reason"])
        self.assertNotEqual(res["replayed_checksum"], res["expected_checksum"])

    def test_missing_patch_is_error_not_ok(self):
        os.remove(os.path.join(self.bundle, "patch.diff"))
        res = replay.replay_bundle(self.bundle,
                                   sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertFalse(res["ok"], res)
        self.assertIn("patch.diff", res["reason"])

    def test_missing_verdict_is_error_not_ok(self):
        os.remove(os.path.join(self.bundle, "artifacts", "verdict_after.json"))
        res = replay.replay_bundle(self.bundle,
                                   sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertFalse(res["ok"], res)
        self.assertIn("verdict_after.json", res["reason"])


class TestFreshCopyIdentical(unittest.TestCase):
    def test_fresh_copy_replays_to_identical_checksum(self):
        tmp = tempfile.mkdtemp(prefix="trust-replay-copy-")
        self.addCleanup(shutil.rmtree, tmp, True)
        src = _make_bundle(os.path.join(tmp, "orig"))
        dst = os.path.join(tmp, "fresh-copy", "orig")
        shutil.copytree(src, dst)
        sim = _fake_sim_factory(FAKE_BOOT_PASS)
        r1 = replay.replay_bundle(src, sim_fn=sim)
        r2 = replay.replay_bundle(dst, sim_fn=sim)
        self.assertTrue(r1["ok"] and r2["ok"], (r1, r2))
        self.assertEqual(r1["replayed_checksum"], r2["replayed_checksum"])


class TestWriteReplaySh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trust-replay-sh-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bundle = _make_bundle(os.path.join(self.tmp, "bundle"))

    def test_writes_executable_replay_sh(self):
        path = replay.write_replay_sh(self.bundle)
        self.assertEqual(path, os.path.join(self.bundle, "REPLAY.sh"))
        self.assertTrue(os.path.isfile(path))
        mode = os.stat(path).st_mode
        self.assertTrue(mode & stat.S_IXUSR, "REPLAY.sh must be executable")

    def test_replay_sh_is_portable_to_fresh_copy(self):
        path = replay.write_replay_sh(self.bundle)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("agent/trust/replay.py", content)
        self.assertIn("AF_REPO", content,
                      "must allow repo override so a copy anywhere can replay")
        self.assertNotIn(self.tmp, content,
                         "must not hardcode the bundle's absolute path")

    def test_replay_sh_regenerates_from_bundle_alone(self):
        replay.write_replay_sh(self.bundle)
        dst = os.path.join(self.tmp, "copy", "bundle")
        shutil.copytree(self.bundle, dst)
        # The copy carries everything the replay needs: no reference to src.
        for name in ("patch.diff", "meta.json", "artifacts/verdict_after.json",
                     "REPLAY.sh"):
            self.assertTrue(os.path.isfile(os.path.join(dst, name)), name)
        res = replay.replay_bundle(dst, sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["ok"], res)


class TestLiveReplayPicosocUartDiv(unittest.TestCase):
    """Sim-only proof: real Verilator replay of the locked live bundle."""

    def test_live_bundle_replays_to_identical_checksum(self):
        self.assertTrue(os.path.isdir(LIVE_BUNDLE),
                        "live bundle missing: %s" % LIVE_BUNDLE)
        res = replay.replay_bundle(LIVE_BUNDLE)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["replayed_checksum"], LIVE_CHECKSUM)
        self.assertEqual(res["expected_checksum"], LIVE_CHECKSUM)

    def test_tampered_expected_checksum_is_mismatch(self):
        # Negative control: the comparison is real, not vacuous. A fresh
        # copy with one hex char flipped in the EXPECTED checksum must
        # replay to the ORIGINAL checksum and report MISMATCH (the copy
        # lives in tmp; the recorded bundle is never touched).
        tmp = tempfile.mkdtemp(prefix="trust-replay-tamper-")
        self.addCleanup(shutil.rmtree, tmp, True)
        dst = os.path.join(tmp, "tampered")
        shutil.copytree(LIVE_BUNDLE, dst)
        vpath = os.path.join(dst, "artifacts", "verdict_after.json")
        with open(vpath, encoding="utf-8") as fh:
            v = json.load(fh)
        self.assertEqual(v["checksum"], LIVE_CHECKSUM)
        bad = ("0" if v["checksum"][0] != "0" else "1") + v["checksum"][1:]
        v["checksum"] = bad
        with open(vpath, "w", encoding="utf-8") as fh:
            json.dump(v, fh)
        res = replay.replay_bundle(dst)
        self.assertFalse(res["ok"], res)
        self.assertIn("MISMATCH", res["reason"])
        self.assertEqual(res["replayed_checksum"], LIVE_CHECKSUM)


if __name__ == "__main__":
    unittest.main()
