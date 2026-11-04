"""Keyless re-verification tests (TDD, red-first) for `afterglow verify`.

`afterglow verify <run-id>` rebuilds the RED tree from the pinned spec,
applies runs/<id>/patch.diff with the loop's own applier, resims, and
compares the checksum against meta.json's AFTER checksum -- with no model
API calls, no keys, no network.

Covers:
- TestVerifyMatchRealBundle: a REAL committed GREEN bundle
  (runs/wave-off-picosoc-uart-div-42, picosoc-uart-div, seed 42) re-sims
  to its recorded AFTER checksum. Env keys are scrubbed to prove the
  path is keyless. (Real verilator build+sim, offline tree copy.)
- TestVerifyMismatchTamperedPatch: the same bundle with an applicable
  but behaviorally-different patch re-sims to a DIFFERENT checksum
  (MISMATCH, not a crash).
- TestVerifyMissingBundle: unknown run-id fails loud (VerifyError /
  CLI nonzero + FATAL), never a fake MATCH.
- TestVerifyCli: `afterglow verify --help` exists, `afterglow --help`
  lists verify, CLI MATCH exits 0, CLI MISMATCH exits 1, CLI missing
  bundle exits nonzero with a loud error.

Run: python3 -m unittest tests.test_verify -v   (from repo root)
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")
GREEN_BUNDLE = os.path.join(ROOT, "runs", "wave-off-picosoc-uart-div-42")
EXPECTED_AFTER = "5c8f58d0f0818c97d014d2f851a6140882bd1efc96a2537047806252c0c2caf9"


def _scrubbed_env():
    """Env without any API-key material: verify must not need keys."""
    env = {k: v for k, v in os.environ.items()
           if "KEY" not in k.upper() and "TOKEN" not in k.upper()}
    env["AFTERGLOW_NO_TUI"] = "1"
    return env


def _copy_bundle_to_tmp():
    tmp = tempfile.mkdtemp(prefix="verify-test-")
    dest = os.path.join(tmp, "wave-off-picosoc-uart-div-42")
    shutil.copytree(GREEN_BUNDLE, dest)
    return tmp, dest


class TestVerifyMatchRealBundle(unittest.TestCase):
    def test_match_real_green_bundle_keyless(self):
        from agent import verify as verify_mod
        with mock.patch.dict(os.environ, _scrubbed_env(), clear=True):
            result = verify_mod.verify_bundle(GREEN_BUNDLE)
        self.assertTrue(result["match"], "expected MATCH: %r" % (result,))
        self.assertEqual(result["got"], EXPECTED_AFTER)
        self.assertEqual(result["expected"], EXPECTED_AFTER)
        self.assertEqual(result["case"], "picosoc-uart-div")

    def test_verify_never_touches_shared_tree(self):
        from agent import verify as verify_mod
        import vbackend
        pins = vbackend.load_pins()
        _url, sha = pins["picorv32"]
        tree = str(vbackend.tree_dir("picorv32", sha))
        before = subprocess.run(
            ["git", "-C", tree, "status", "--porcelain"],
            stdout=subprocess.PIPE, text=True, timeout=60).stdout
        with mock.patch.dict(os.environ, _scrubbed_env(), clear=True):
            verify_mod.verify_bundle(GREEN_BUNDLE)
        after = subprocess.run(
            ["git", "-C", tree, "status", "--porcelain"],
            stdout=subprocess.PIPE, text=True, timeout=60).stdout
        self.assertEqual(before, after,
                         "verify must leave the shared cached tree untouched")


class TestVerifyMismatchTamperedPatch(unittest.TestCase):
    def test_tampered_patch_is_mismatch(self):
        from agent import verify as verify_mod
        tmp, dest = _copy_bundle_to_tmp()
        self.addCleanup(shutil.rmtree, tmp, True)
        patch_path = os.path.join(dest, "patch.diff")
        with open(patch_path, encoding="utf-8") as fh:
            diff = fh.read()
        # Applicable tamper: keep hunk structure, change the fixed lines'
        # divisor so the resim checksum moves (still applies cleanly).
        tampered = diff.replace(
            "+					if (recv_divcnt > cfg_divider) begin",
            "+					if (recv_divcnt > (cfg_divider >> 1)) begin")
        self.assertNotEqual(tampered, diff, "tamper must change patch.diff")
        self.assertIn("cfg_divider >> 1", tampered)
        with open(patch_path, "w", encoding="utf-8") as fh:
            fh.write(tampered)
        with mock.patch.dict(os.environ, _scrubbed_env(), clear=True):
            result = verify_mod.verify_bundle(dest)
        self.assertFalse(result["match"], "tampered patch must MISMATCH")
        self.assertEqual(result["expected"], EXPECTED_AFTER)
        self.assertNotEqual(result["got"], EXPECTED_AFTER)

    def test_unapplicable_patch_is_mismatch_not_crash(self):
        from agent import verify as verify_mod
        tmp, dest = _copy_bundle_to_tmp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(dest, "patch.diff"), "w",
                   encoding="utf-8") as fh:
            fh.write("this is not a diff at all\n")
        with mock.patch.dict(os.environ, _scrubbed_env(), clear=True):
            result = verify_mod.verify_bundle(dest)
        self.assertFalse(result["match"])


class TestVerifyMissingBundle(unittest.TestCase):
    def test_missing_bundle_raises_loud(self):
        from agent import verify as verify_mod
        missing = os.path.join(ROOT, "runs", "does-not-exist-xyz")
        with self.assertRaises(verify_mod.VerifyError) as ctx:
            verify_mod.verify_bundle(missing)
        self.assertIn("not found", str(ctx.exception).lower())

    def test_missing_meta_raises_loud(self):
        from agent import verify as verify_mod
        tmp = tempfile.mkdtemp(prefix="verify-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        run = os.path.join(tmp, "empty-run")
        os.makedirs(run)
        with self.assertRaises(verify_mod.VerifyError):
            verify_mod.verify_bundle(run)


class TestVerifyCli(unittest.TestCase):
    def test_verify_help_exists(self):
        proc = subprocess.run(
            [AFTERGLOW, "verify", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60, env=_scrubbed_env())
        self.assertEqual(proc.returncode, 0)
        self.assertIn("runs/<id>", proc.stdout)

    def test_help_lists_verify(self):
        proc = subprocess.run(
            [AFTERGLOW, "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60, env=_scrubbed_env())
        self.assertEqual(proc.returncode, 0)
        self.assertIn("verify", proc.stdout)

    def test_cli_match_exit_0(self):
        proc = subprocess.run(
            [AFTERGLOW, "verify", GREEN_BUNDLE],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=1200, env=_scrubbed_env())
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:])
        self.assertIn("MATCH", proc.stdout)
        self.assertIn(EXPECTED_AFTER[:12], proc.stdout)

    def test_cli_mismatch_exit_1(self):
        tmp, dest = _copy_bundle_to_tmp()
        self.addCleanup(shutil.rmtree, tmp, True)
        patch_path = os.path.join(dest, "patch.diff")
        with open(patch_path, encoding="utf-8") as fh:
            diff = fh.read()
        tampered = diff.replace(
            "+					if (recv_divcnt > cfg_divider) begin",
            "+					if (recv_divcnt > (cfg_divider >> 1)) begin")
        self.assertNotEqual(tampered, diff)
        with open(patch_path, "w", encoding="utf-8") as fh:
            fh.write(tampered)
        proc = subprocess.run(
            [AFTERGLOW, "verify", dest],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=1200, env=_scrubbed_env())
        self.assertEqual(proc.returncode, 1, proc.stdout[-2000:])
        self.assertIn("MISMATCH", proc.stdout)

    def test_cli_missing_bundle_fails_loud(self):
        proc = subprocess.run(
            [AFTERGLOW, "verify",
             os.path.join(ROOT, "runs", "does-not-exist-xyz")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60, env=_scrubbed_env())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("FATAL", proc.stdout)


if __name__ == "__main__":
    unittest.main()
