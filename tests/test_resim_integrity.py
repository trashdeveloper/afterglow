"""Resim-integrity + no-op-patch detector tests (TDD, fixtures + mocks only).

Covers the mutbigN-v3-21 anomaly: attempt-1 patch applied cleanly
(guard_ok=True applied=True) yet RESIM returned an IDENTICAL checksum
(c2a7a4d6...) and tohost before/after -- behaviorally impossible for
that edit, pointing at a stale build / wrong tree / cached objects,
which the old loop recorded as an ordinary RED with no hash evidence
and no retry signal.

- TestDetectNoBehavioralEffect: pure predicate on (before, after) verdicts.
- TestResimWithIntegrity: wrapper snapshots pre/post hashes; a tree-file
  hash mismatch forces exactly one clean rebuild (obj dir removed,
  resim_fn called twice); an identical-checksum resim sets
  no_behavioral_effect.
- TestLoopFeedbackWiring: full mocked main loop with an
  identical-checksum/tohost resim -> attempt + meta carry
  no_behavioral_effect=true, and the logged api/text_request.json
  contains the "PATCH HAD NO BEHAVIORAL EFFECT ..." sentence
  verbatim; the retry prompt carries it as a LOOP FEEDBACK block while
  the prompt files stay untouched (no v3->v4 bump).

Run: python3 tests/test_resim_integrity.py -v   (from repo root)
"""
import hashlib
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
    "TRAP: test trap\n"
    "tohost: FAIL 0x1\n"
    "CYCLES=1234\n"
)

MODEL_OK = {
    "content": json.dumps({
        "diagnosis": "receiver clock tree glitch delays bit capture",
        "expected": "tohost: PASS",
        "patch_diff": (
            "diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v\n"
            "--- a/picosoc/simpleuart.v\n"
            "+++ b/picosoc/simpleuart.v\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    }),
    "raw": {"id": "mock-id"},
    "latency_ms": 7,
    "request_id": "mock-req",
    "model": loop.TEXT_MODEL,
    "usage": {},
}

IMG_OK = {"latency_ms": 5, "request_id": "mock-img", "bytes": 100,
          "sha256": "abc123", "transport": "mock",
          "model": "muse-image-1.0"}


def _sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _fake_red_sim(case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write(FAKE_BOOT_RED)
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case": case, "pass": False,
                             "tohost": "FAIL 0x1",
                             "checksum": "deadbeef", "cycles": 1234,
                             "seed": seed}) + "\n")
    return 1, "mock RED"


class TestDetectNoBehavioralEffect(unittest.TestCase):
    def test_identical_checksum_and_tohost_is_no_effect(self):
        before = {"checksum": "c2a7", "tohost": "FAIL 0xdead"}
        after = {"checksum": "c2a7", "tohost": "FAIL 0xdead"}
        self.assertIs(loop.detect_no_behavioral_effect(before, after),
                      True)

    def test_different_checksum_has_effect(self):
        before = {"checksum": "aaaa", "tohost": "FAIL 0xdead"}
        after = {"checksum": "bbbb", "tohost": "FAIL 0xdead"}
        self.assertIs(loop.detect_no_behavioral_effect(before, after),
                      False)

    def test_different_tohost_has_effect(self):
        before = {"checksum": "c2a7", "tohost": "FAIL 0xdead"}
        after = {"checksum": "c2a7", "tohost": "PASS"}
        self.assertIs(loop.detect_no_behavioral_effect(before, after),
                      False)

    def test_missing_checksum_never_flags(self):
        self.assertIs(loop.detect_no_behavioral_effect(
            {"checksum": None, "tohost": "FAIL"},
            {"checksum": None, "tohost": "FAIL"}), False)
        self.assertIs(loop.detect_no_behavioral_effect({}, {}), False)


class TestResimWithIntegrity(unittest.TestCase):
    def _tree(self, tmp, body="old line\n"):
        os.makedirs(os.path.join(tmp, "tree"), exist_ok=True)
        with open(os.path.join(tmp, "tree", "f.v"), "w") as fh:
            fh.write(body)
        return os.path.join(tmp, "tree")

    def _case(self):
        return {"name": "tiny", "files": ["f.v"]}

    def _verdict(self, checksum, tohost="FAIL 0x1"):
        return {"case": "tiny", "pass": False, "tohost": tohost,
                "checksum": checksum, "cycles": 10, "seed": 42}

    def test_stable_tree_single_resim_no_rebuild(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp)
            mdir = os.path.join(tree, "obj_afterglow_tiny")
            os.makedirs(mdir)
            marker = os.path.join(mdir, "cached.o")
            with open(marker, "w") as fh:
                fh.write("cached")
            calls = []

            def _fake_resim(tree_, case_, seed_, boot_, verdict_):
                calls.append(1)
                return self._verdict("c" * 64)

            verdict, integrity = loop.resim_with_integrity(
                tree, self._case(), 42,
                os.path.join(tmp, "boot.log"),
                os.path.join(tmp, "v.json"),
                run_dir=None,
                verdict_before=self._verdict("d" * 64),
                resim_fn=_fake_resim)
            self.assertEqual(len(calls), 1)
            self.assertIs(integrity["stale-tree-suspect"], False)
            self.assertEqual(integrity["clean_rebuilds"], 0)
            self.assertIs(integrity["no_behavioral_effect"], False)
            # incremental objects untouched: no gratuitous clean rebuild
            self.assertTrue(os.path.isfile(marker))
            self.assertEqual(verdict["checksum"], "c" * 64)

    def test_hash_mismatch_forces_one_clean_rebuild(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp)
            mdir = os.path.join(tree, "obj_afterglow_tiny")
            os.makedirs(mdir)
            with open(os.path.join(mdir, "cached.o"), "w") as fh:
                fh.write("cached")
            calls = []

            def _fake_resim(tree_, case_, seed_, boot_, verdict_):
                calls.append(1)
                if len(calls) == 1:
                    # simulate tree drift during the build (concurrent
                    # write / wrong-tree patch landing late)
                    with open(os.path.join(tree_, "f.v"), "a") as fh:
                        fh.write("drifted\n")
                return self._verdict("e" * 64)

            verdict, integrity = loop.resim_with_integrity(
                tree, self._case(), 42,
                os.path.join(tmp, "boot.log"),
                os.path.join(tmp, "v.json"),
                run_dir=None,
                verdict_before=self._verdict("d" * 64),
                resim_fn=_fake_resim)
            self.assertEqual(len(calls), 2)
            self.assertIs(integrity["stale-tree-suspect"], True)
            self.assertEqual(integrity["clean_rebuilds"], 1)
            # clean rebuild really dropped the incremental objects
            self.assertFalse(os.path.exists(mdir))
            self.assertEqual(verdict["checksum"], "e" * 64)
            self.assertIn("f.v", integrity["pre_hashes"])
            self.assertNotEqual(integrity["pre_hashes"],
                                integrity["post_hashes"])

    def test_identical_checksum_resim_sets_flag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp)
            before = self._verdict("c2a7", tohost="FAIL 0xdead")

            def _fake_resim(tree_, case_, seed_, boot_, verdict_):
                return self._verdict("c2a7", tohost="FAIL 0xdead")

            _verdict, integrity = loop.resim_with_integrity(
                tree, self._case(), 42,
                os.path.join(tmp, "boot.log"),
                os.path.join(tmp, "v.json"),
                run_dir=None, verdict_before=before,
                resim_fn=_fake_resim)
            self.assertIs(integrity["no_behavioral_effect"], True)
            self.assertIs(integrity["stale-tree-suspect"], False)

    def test_stash_copies_patched_source_before_resim(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._tree(tmp, body="patched line\n")
            run_dir = os.path.join(tmp, "run")
            os.makedirs(run_dir)
            hashes = loop.stash_patched_sources(
                tree, ["f.v"], run_dir)
            self.assertIn("f.v", hashes)
            self.assertEqual(hashes["f.v"],
                             _sha_text("patched line\n"))
            stashed = os.path.join(run_dir, "patched_source.v")
            self.assertTrue(os.path.isfile(stashed),
                            "patched_source.<ext> must land BEFORE resim")
            with open(stashed) as fh:
                self.assertEqual(fh.read(), "patched line\n")


class TestLoopFeedbackWiring(unittest.TestCase):
    def test_feedback_sentence_in_logged_request(self):
        run_id = "test-resim-feedback"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)

        def _fake_resim_identical(tree, case, seed, boot_path,
                                  verdict_path):
            # v3-21 replay: patch applied, yet checksum/tohost identical
            with open(boot_path, "w", encoding="utf-8") as fh:
                fh.write(FAKE_BOOT_RED)
            verdict = {"case": case["name"], "pass": False,
                       "tohost": "FAIL 0x1", "checksum": "deadbeef",
                       "cycles": 1234, "seed": seed}
            with open(verdict_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(verdict) + "\n")
            return verdict

        patches = [
            mock.patch.object(loop, "run_sim_before",
                              side_effect=_fake_red_sim),
            mock.patch.object(loop, "rtl_excerpt",
                              return_value="line 1: fake excerpt"),
            mock.patch.object(
                loop, "call_text_with_backoff",
                return_value=(dict(MODEL_OK), [])),
            mock.patch.object(loop.visual, "generate",
                              return_value=dict(IMG_OK)),
            mock.patch.object(
                loop, "try_apply_diff",
                return_value={"guard_ok": True, "reasons": [],
                              "applied": True, "repaired": False,
                              "patch_lines": 2,
                              "files": ["picosoc/simpleuart.v"],
                              "applied_diff": "diff text\n"}),
            mock.patch.object(loop, "resim_after",
                              side_effect=_fake_resim_identical),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id])
        self.assertEqual(rc, 1)  # still RED, never false green
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertIs(meta.get("no_behavioral_effect"), True)
        self.assertTrue(
            any(a.get("no_behavioral_effect")
                for a in meta.get("attempts", [])),
            "at least one attempt must carry no_behavioral_effect=true")
        req = _read_json(os.path.join(run_dir, "api",
                                      "text_request.json"))
        sentence = ("PATCH HAD NO BEHAVIORAL EFFECT — suspect no-op "
                    "edit or stale build")
        self.assertIn(sentence, req.get("loop_feedback", ""),
                      "feedback must be logged verbatim in text_request.json")
        self.assertIn(sentence, req.get("prompt", ""),
                      "feedback must reach the next attempt's prompt")
        self.assertIn("LOOP FEEDBACK", req.get("prompt", ""))
        # prompt-hash versioning untouched: no prompt file changed, so
        # the logged hash must still be the on-disk template's hash
        # (no phantom v3->v4 bump).
        with open(os.path.join(ROOT, "agent", "prompts", "v2.txt"),
                  encoding="utf-8") as fh:
            template = fh.read()
        self.assertEqual(meta.get("prompt_version"), "v2")
        self.assertEqual(req.get("prompt_sha256"), _sha_text(template))
        self.assertFalse(
            os.path.exists(os.path.join(ROOT, "agent", "prompts",
                                        "v4.txt")),
            "no prompt bump: feedback rides as a context block, "
            "not a new template")


if __name__ == "__main__":
    unittest.main()
