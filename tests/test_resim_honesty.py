"""Resim honesty tests (TDD, fixtures + mocks only) for the mutpair-v8b-05
incident: attempt-1's recorded diff fixed BOTH true faults (verified
against sim/targets/mut-pair-05.yaml anchors) yet resim reported an
identical-checksum FAIL; an independent rebuild of the same content
PASSES (proven in /tmp/v9check). The loop discarded a correct fix.

Covers:
- TestBuildLogPath: vbackend.build accepts log_path, writes full
  verilator/make stdout there, returns the binary as before.
- TestPerAttemptStash: stash_patched_sources(..., attempt=N) keeps
  every attempt's pre-build content (patched_source.attempt<N>.<ext>)
  alongside the legacy patched_source.<ext> name.
- TestStaleBuildSuspect: real edit (pre != RED-baseline content) +
  identical checksum + unchanged tohost => drop the obj dir, clean
  rebuild, resim EXACTLY once; the second verdict stands; reason +
  clean_rebuilds recorded; never more than 1 extra resim per attempt.
- TestResetTruncation: reset-to-baseline truncates the compositional
  stack record to [] and logs the truncation; an empty stack leaves
  the reset note identical (singles path unchanged).

Run: python3 -m unittest tests.test_resim_honesty -v   (from repo root)
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop  # noqa: E402  (adds sim/ to sys.path)
import vbackend  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tiny_tree(tmp, files=("d.v",)):
    os.makedirs(tmp, exist_ok=True)
    for name in files:
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
            fh.write("// %s\nwire x;\n" % name)
    return tmp


def _hon_case(name="hon"):
    return {"name": name, "top": "T", "tb": "fake_tb.cpp",
            "files": ["d.v"], "oracle_pass": "tohost: PASS"}


class TestBuildLogPath(unittest.TestCase):
    def _fake_env(self, tmp):
        tree = _tiny_tree(os.path.join(tmp, "tree"))
        simdir = os.path.join(tmp, "sim")
        os.makedirs(os.path.join(simdir, "tb"))
        with open(os.path.join(simdir, "tb", "fake_tb.cpp"), "w") as fh:
            fh.write("// fake tb\n")
        return tree, simdir

    def test_build_writes_stdout_to_log_path(self):
        tmp = os.path.join(ROOT, "runs", "scratch", "test-hon-build-log")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree, simdir = self._fake_env(tmp)
        case = _hon_case()
        binary = os.path.join(tree, "obj_afterglow_hon", "VT")

        def _fake_sh(cmd, cwd=None, timeout=600):
            os.makedirs(os.path.dirname(binary), exist_ok=True)
            with open(binary, "w") as fh:
                fh.write("elf\n")
            return 0, "fake-make: built VT ok\n"

        log_path = os.path.join(tmp, "build_attempt1.log")
        with mock.patch.object(vbackend, "sh", side_effect=_fake_sh), \
                mock.patch.object(vbackend, "SIMDIR", simdir):
            got = vbackend.build(tree, case, log_path=log_path)
        self.assertEqual(str(got), binary)
        with open(log_path, encoding="utf-8") as fh:
            self.assertIn("fake-make: built VT ok", fh.read())

    def test_build_failure_raises_and_still_logs(self):
        tmp = os.path.join(ROOT, "runs", "scratch", "test-hon-build-fail")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree, simdir = self._fake_env(tmp)
        case = _hon_case()
        log_path = os.path.join(tmp, "build_attempt1.log")
        with mock.patch.object(
                vbackend, "sh",
                return_value=(1, "fake-make: ERROR boom\n")), \
                mock.patch.object(vbackend, "SIMDIR", simdir):
            with self.assertRaises(RuntimeError):
                vbackend.build(tree, case, log_path=log_path)
        with open(log_path, encoding="utf-8") as fh:
            self.assertIn("ERROR boom", fh.read())

    def test_build_default_writes_no_log(self):
        tmp = os.path.join(ROOT, "runs", "scratch", "test-hon-build-def")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree, simdir = self._fake_env(tmp)
        case = _hon_case()
        binary = os.path.join(tree, "obj_afterglow_hon", "VT")

        def _fake_sh(cmd, cwd=None, timeout=600):
            os.makedirs(os.path.dirname(binary), exist_ok=True)
            with open(binary, "w") as fh:
                fh.write("elf\n")
            return 0, "out\n"

        with mock.patch.object(vbackend, "sh", side_effect=_fake_sh), \
                mock.patch.object(vbackend, "SIMDIR", simdir):
            got = vbackend.build(tree, case)
        self.assertEqual(str(got), binary)
        self.assertEqual(os.listdir(tmp), ["sim", "tree"])


class TestPerAttemptStash(unittest.TestCase):
    def test_every_attempt_survives(self):
        tmp = os.path.join(ROOT, "runs", "scratch", "test-hon-stash")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = _tiny_tree(os.path.join(tmp, "tree"))
        run_dir = os.path.join(tmp, "run")
        os.makedirs(run_dir)
        with open(os.path.join(tree, "d.v"), "w") as fh:
            fh.write("attempt2 content\n")
        loop.stash_patched_sources(tree, ["d.v"], run_dir, attempt=2)
        with open(os.path.join(tree, "d.v"), "w") as fh:
            fh.write("attempt3 content\n")
        loop.stash_patched_sources(tree, ["d.v"], run_dir, attempt=3)
        with open(os.path.join(run_dir, "patched_source.attempt2.v")) as fh:
            self.assertEqual(fh.read(), "attempt2 content\n")
        with open(os.path.join(run_dir, "patched_source.attempt3.v")) as fh:
            self.assertEqual(fh.read(), "attempt3 content\n")
        # Legacy name keeps working (latest content).
        with open(os.path.join(run_dir, "patched_source.v")) as fh:
            self.assertEqual(fh.read(), "attempt3 content\n")

    def test_no_attempt_writes_legacy_only(self):
        tmp = os.path.join(ROOT, "runs", "scratch", "test-hon-stash-leg")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = _tiny_tree(os.path.join(tmp, "tree"))
        run_dir = os.path.join(tmp, "run")
        os.makedirs(run_dir)
        loop.stash_patched_sources(tree, ["d.v"], run_dir)
        names = sorted(os.listdir(run_dir))
        self.assertEqual(names, ["patched_source.v"])

    def test_same_extension_dedup_with_attempt(self):
        tmp = os.path.join(ROOT, "runs", "scratch", "test-hon-stash-dd")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = _tiny_tree(os.path.join(tmp, "tree"), ("x.v", "y.v"))
        run_dir = os.path.join(tmp, "run")
        os.makedirs(run_dir)
        loop.stash_patched_sources(tree, ["x.v", "y.v"], run_dir, attempt=1)
        names = sorted(os.listdir(run_dir))
        self.assertIn("patched_source.attempt1.v", names)
        self.assertIn("patched_source.attempt1.2.v", names)


class TestStaleBuildSuspect(unittest.TestCase):
    def _setup(self, tag):
        tmp = os.path.join(ROOT, "runs", "scratch", "test-hon-%s" % tag)
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = _tiny_tree(os.path.join(tmp, "tree"))
        run_dir = os.path.join(tmp, "run")
        os.makedirs(run_dir)
        case = _hon_case()
        return tmp, tree, run_dir, case

    def _verdict(self, checksum, tohost="FAIL 0x1", passed=False):
        return {"case": "hon", "pass": passed, "tohost": tohost,
                "checksum": checksum, "cycles": 1234, "seed": 42}

    def test_real_edit_identical_checksum_rebuilds_once(self):
        _tmp, tree, run_dir, case = self._setup("stale")
        red_baseline = dict(loop.snapshot_file_hashes(tree, ["d.v"]))
        with open(os.path.join(tree, "d.v"), "w") as fh:
            fh.write("// REAL edit: both faults fixed\n")
        calls = []

        def _fake_resim(t, c, seed, boot_path, verdict_path):
            calls.append(True)
            return self._verdict("aaa")

        verdict_before = self._verdict("aaa")
        verdict, integrity = loop.resim_with_integrity(
            tree, case, 42, os.path.join(run_dir, "boot.log"),
            os.path.join(run_dir, "v.json"), run_dir=run_dir,
            verdict_before=verdict_before, resim_fn=_fake_resim,
            attempt=1, red_baseline_hashes=red_baseline)
        self.assertEqual(len(calls), 2, "exactly one extra resim")
        self.assertEqual(integrity["clean_rebuilds"], 1)
        self.assertTrue(integrity.get("stale-build-suspect"))
        self.assertTrue(integrity.get("stale_build_reason"),
                        "a reason must be recorded")
        self.assertIn("stale", integrity.get("integrity_detail", "").lower())
        self.assertEqual(verdict["checksum"], "aaa")

    def test_stale_build_drops_obj_dir(self):
        _tmp, tree, run_dir, case = self._setup("stale-obj")
        mdir = os.path.join(tree, "obj_afterglow_hon")
        os.makedirs(mdir)
        with open(os.path.join(mdir, "stale.o"), "w") as fh:
            fh.write("stale\n")
        red_baseline = dict(loop.snapshot_file_hashes(tree, ["d.v"]))
        with open(os.path.join(tree, "d.v"), "w") as fh:
            fh.write("// REAL edit\n")

        def _fake_resim(t, c, seed, boot_path, verdict_path):
            return self._verdict("aaa")

        loop.resim_with_integrity(
            tree, case, 42, os.path.join(run_dir, "boot.log"),
            os.path.join(run_dir, "v.json"), run_dir=run_dir,
            verdict_before=self._verdict("aaa"), resim_fn=_fake_resim,
            attempt=1, red_baseline_hashes=red_baseline)
        self.assertFalse(os.path.exists(mdir),
                         "stale obj dir must be dropped before rebuild")

    def test_second_verdict_stands(self):
        _tmp, tree, run_dir, case = self._setup("stale-stands")
        red_baseline = dict(loop.snapshot_file_hashes(tree, ["d.v"]))
        with open(os.path.join(tree, "d.v"), "w") as fh:
            fh.write("// REAL edit\n")
        seq = [self._verdict("aaa"), self._verdict("bbb", "PASS", True)]

        def _fake_resim(t, c, seed, boot_path, verdict_path):
            return seq.pop(0)

        verdict, integrity = loop.resim_with_integrity(
            tree, case, 42, os.path.join(run_dir, "boot.log"),
            os.path.join(run_dir, "v.json"), run_dir=run_dir,
            verdict_before=self._verdict("aaa"), resim_fn=_fake_resim,
            attempt=1, red_baseline_hashes=red_baseline)
        self.assertEqual(verdict["checksum"], "bbb")
        self.assertTrue(verdict["pass"])
        self.assertFalse(integrity["no_behavioral_effect"],
                         "second verdict stands: behavior DID change")

    def test_no_real_edit_no_extra_resim(self):
        _tmp, tree, run_dir, case = self._setup("stale-noop")
        # No edit: pre-build content == RED-baseline content.
        red_baseline = dict(loop.snapshot_file_hashes(tree, ["d.v"]))
        calls = []

        def _fake_resim(t, c, seed, boot_path, verdict_path):
            calls.append(True)
            return self._verdict("aaa")

        _v, integrity = loop.resim_with_integrity(
            tree, case, 42, os.path.join(run_dir, "boot.log"),
            os.path.join(run_dir, "v.json"), run_dir=run_dir,
            verdict_before=self._verdict("aaa"), resim_fn=_fake_resim,
            attempt=1, red_baseline_hashes=red_baseline)
        self.assertEqual(len(calls), 1)
        self.assertFalse(integrity.get("stale-build-suspect", False))
        self.assertEqual(integrity["clean_rebuilds"], 0)

    def test_behavioral_change_no_extra_resim(self):
        _tmp, tree, run_dir, case = self._setup("stale-changed")
        red_baseline = dict(loop.snapshot_file_hashes(tree, ["d.v"]))
        with open(os.path.join(tree, "d.v"), "w") as fh:
            fh.write("// REAL edit\n")
        calls = []

        def _fake_resim(t, c, seed, boot_path, verdict_path):
            calls.append(True)
            return self._verdict("DIFFERENT")

        _v, integrity = loop.resim_with_integrity(
            tree, case, 42, os.path.join(run_dir, "boot.log"),
            os.path.join(run_dir, "v.json"), run_dir=run_dir,
            verdict_before=self._verdict("aaa"), resim_fn=_fake_resim,
            attempt=1, red_baseline_hashes=red_baseline)
        self.assertEqual(len(calls), 1)
        self.assertFalse(integrity.get("stale-build-suspect", False))

    def test_never_more_than_one_extra_resim(self):
        _tmp, tree, run_dir, case = self._setup("stale-cap")
        red_baseline = dict(loop.snapshot_file_hashes(tree, ["d.v"]))
        with open(os.path.join(tree, "d.v"), "w") as fh:
            fh.write("// REAL edit\n")
        calls = []

        def _fake_resim(t, c, seed, boot_path, verdict_path):
            calls.append(True)
            # Drift mid-build too: both signals present at once.
            with open(os.path.join(t, "d.v"), "a") as fh:
                fh.write("// drift\n")
            return self._verdict("aaa")

        _v, integrity = loop.resim_with_integrity(
            tree, case, 42, os.path.join(run_dir, "boot.log"),
            os.path.join(run_dir, "v.json"), run_dir=run_dir,
            verdict_before=self._verdict("aaa"), resim_fn=_fake_resim,
            attempt=1, red_baseline_hashes=red_baseline)
        self.assertEqual(len(calls), 2,
                         "drift + stale-build together still resim at most x2")
        self.assertEqual(integrity["clean_rebuilds"], 1)

    def test_retry_build_log_path(self):
        _tmp, tree, run_dir, case = self._setup("stale-log")
        red_baseline = dict(loop.snapshot_file_hashes(tree, ["d.v"]))
        with open(os.path.join(tree, "d.v"), "w") as fh:
            fh.write("// REAL edit\n")
        seen = []

        def _fake_real(t, c, seed, boot_path, verdict_path,
                       build_log_path=None):
            seen.append(build_log_path)
            return self._verdict("aaa")

        log_path = os.path.join(run_dir, "build_attempt1.log")
        with mock.patch.object(loop, "resim_after", _fake_real):
            loop.resim_with_integrity(
                tree, case, 42, os.path.join(run_dir, "boot.log"),
                os.path.join(run_dir, "v.json"), run_dir=run_dir,
                verdict_before=self._verdict("aaa"), resim_fn=_fake_real,
                attempt=1, red_baseline_hashes=red_baseline,
                build_log_path=log_path)
        self.assertEqual(seen[0], log_path)
        self.assertEqual(len(seen), 2, "retry resim must also log its build")
        self.assertNotEqual(seen[1], seen[0],
                            "retry build log must not overwrite the first")


class TestResimAfterBuildLog(unittest.TestCase):
    def test_resim_after_forwards_log_path_to_build(self):
        tmp = os.path.join(ROOT, "runs", "scratch", "test-hon-resim-log")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = _tiny_tree(os.path.join(tmp, "tree"))
        case = _hon_case()
        log_path = os.path.join(tmp, "build_attempt1.log")
        binary = os.path.join(tree, "Vbin")

        def _fake_build(t, c, log_path=None):
            with open(log_path, "w") as fh:
                fh.write("make output\n")
            return binary

        fake_log = ("tohost: PASS\nCYCLES=42\n")
        with mock.patch.object(loop.vbackend, "build",
                               side_effect=_fake_build), \
                mock.patch.object(loop.vbackend, "sh",
                                   return_value=(0, fake_log)):
            verdict = loop.resim_after(
                tree, case, 42, os.path.join(tmp, "boot.log"),
                os.path.join(tmp, "v.json"), build_log_path=log_path)
        self.assertTrue(verdict["pass"])
        with open(log_path, encoding="utf-8") as fh:
            self.assertIn("make output", fh.read())


class TestResetTruncation(unittest.TestCase):
    def test_nonempty_stack_truncated_and_logged(self):
        stack = [{"attempt": 1, "patch": "p1"},
                 {"attempt": 3, "patch": "p3"}]
        reset_note = {"tree_reset": True, "tree_reset_verified": True}
        loop.truncate_stack_on_reset(stack, reset_note)
        self.assertEqual(stack, [])
        self.assertTrue(reset_note.get("stack_truncated"))
        self.assertEqual(reset_note.get("stack_truncated_from"), 2)

    def test_empty_stack_reset_note_identical(self):
        stack = []
        reset_note = {"tree_reset": True, "tree_reset_verified": True}
        before = dict(reset_note)
        loop.truncate_stack_on_reset(stack, reset_note)
        self.assertEqual(stack, [])
        self.assertEqual(reset_note, before,
                         "singles reset path must stay byte-identical")


if __name__ == "__main__":
    unittest.main()
