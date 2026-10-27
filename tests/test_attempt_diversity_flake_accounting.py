"""Attempt-diversity + flake-vs-signal accounting tests (fixtures only).

Run: python3 tests/test_attempt_diversity_flake_accounting.py -v  (repo root)

Evidence-first (no live API, no keys, no real sim builds):

Direction keys (normalized file + sorted changed-line numbers + op-word):
- test_same_direction_same_key: mutbigN-v3-21 lesson -- attempts 1 AND 3
  proposed the same wrong-direction patch (same file/lines, reworded
  content) -> identical direction keys.
- test_different_lines_different_key: same file, different hunk lines ->
  different keys.
- test_op_word_distinguishes_add_remove_replace: same file+lines, add-only
  vs remove-only vs mixed -> three distinct keys.
- test_one_liner_keys: fabricated prior one-liners with the same hunk ->
  same key; different hunks -> different keys.
- test_empty_diff_key: empty diff -> "(empty)".

Duplicate detection:
- test_duplicate_against_stored_direction: a new diff matching a stored
  prior direction -> True; non-matching -> False.
- test_duplicate_legacy_oneliner_priors: priors with only one-liner
  "patch" text (no stored direction) still match identical one-liners.

Loop diversity (mocked main run):
- test_duplicate_reask_without_burning_resim: attempt 2 repeats attempt 1's
  direction -> exactly ONE extra model ask (prompt carries an explicit
  avoid/different-direction note + prior one-liners, logged verbatim in
  text_request.json), resim still runs exactly 3 times (no resim burned
  for the duplicate), and the attempt entry records duplicate_direction.
- test_max_one_duplicate_per_run: a model that always repeats the same
  direction gets exactly one re-ask per run (4 text calls for 3 consumed
  attempts), then the repeat is tolerated.

Flake accounting (mocked transport):
- test_flake_storm_preserves_diagnostic_budget: one exhausted transport
  sequence (the wave-1 re-fire's 3 EMPTYs inside attempt 2) does NOT
  consume a diagnostic attempt -> all 3 diagnostic attempts still run,
  attempts_consumed == 3, transport_failures == 1.
- test_persistent_flakes_stay_bounded_and_degrade: transport always down ->
  exit 2 triage (never infinite), attempts_consumed == 0,
  transport_failures capped.
- test_transport_waits_accumulate_across_attempts: waits from every
  successful call accumulate in meta["transport_retries"] (never
  overwritten by the latest attempt).
- test_failed_sequence_keeps_its_waits: an exhausted
  call_text_with_backoff exposes its waits on the raised LiveAPIError.
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

# Same direction, reworded content (the v3-21 attempts-1-and-3 lesson).
DIFF_A1 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1446,4 +1446,4 @@
-wire decoder_trigger <= mem_do_rinst || mem_done;
+wire decoder_trigger <= mem_do_rinst || mem_done || 1'b0;
"""

DIFF_A2 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1446,4 +1446,4 @@
-wire decoder_trigger <= mem_do_rinst || mem_done;
+wire decoder_trigger <= (mem_do_rinst || mem_done);
"""

DIFF_B = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -377,1 +377,1 @@
-reg mem_la_read = 0;
+reg mem_la_read = 1;
"""

DIFF_ADD_ONLY = """diff --git a/top.v b/top.v
--- a/top.v
+++ b/top.v
@@ -10,0 +11,2 @@
+wire [7:0] probe;
+assign probe = recv_divcnt;
"""

DIFF_DEL_ONLY = """diff --git a/top.v b/top.v
--- a/top.v
+++ b/top.v
@@ -10,2 +10,0 @@
-wire [7:0] probe;
-assign probe = recv_divcnt;
"""

DIFF_MIXED = """diff --git a/top.v b/top.v
--- a/top.v
+++ b/top.v
@@ -10,1 +10,1 @@
-wire [7:0] probe;
+wire [7:0] probe2;
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _fake_red_sim(case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write(FAKE_BOOT_RED)
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(FAKE_VERDICT, case=case, seed=seed)) + "\n")
    return 1, "mock RED"


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _resp(diff, n):
    return ({"content": json.dumps(
        {"diagnosis": "mock diagnosis %d" % n,
         "patch_diff": diff, "expected": "tohost: PASS"}),
        "raw": {}, "latency_ms": 5,
        "request_id": "mock-r%d" % n,
        "model": loop.TEXT_MODEL, "usage": {}}, [])


class DirectionKey(unittest.TestCase):
    def test_same_direction_same_key(self):
        self.assertEqual(loop.direction_key(DIFF_A1),
                         loop.direction_key(DIFF_A2))

    def test_different_lines_different_key(self):
        self.assertNotEqual(loop.direction_key(DIFF_A1),
                            loop.direction_key(DIFF_B))

    def test_op_word_distinguishes_add_remove_replace(self):
        k_add = loop.direction_key(DIFF_ADD_ONLY)
        k_del = loop.direction_key(DIFF_DEL_ONLY)
        k_mix = loop.direction_key(DIFF_MIXED)
        self.assertNotEqual(k_add, k_del)
        self.assertNotEqual(k_add, k_mix)
        self.assertNotEqual(k_del, k_mix)
        self.assertIn("add", k_add)
        self.assertIn("remove", k_del)
        self.assertIn("replace", k_mix)
        self.assertIn("top.v", k_add)

    def test_one_liner_keys(self):
        one1 = "@@ -1446,4 +1446,4 @@ / - decoder_trigger <= mem_do_rinst;"
        one2 = "@@ -1446,4 +1446,4 @@ / - decoder_trigger <= mem_do_rinst;"
        one3 = "@@ -377,1 +377,1 @@ / -reg mem_la_read = 0;"
        self.assertEqual(loop.direction_key(one1),
                         loop.direction_key(one2))
        self.assertNotEqual(loop.direction_key(one1),
                            loop.direction_key(one3))

    def test_empty_diff_key(self):
        self.assertEqual(loop.direction_key(""), "(empty)")
        self.assertEqual(loop.direction_key("   \n"), "(empty)")


class DuplicateDetection(unittest.TestCase):
    def test_duplicate_against_stored_direction(self):
        priors = [{"attempt": 1, "patch": loop.patch_one_liner(DIFF_A1),
                   "direction": loop.direction_key(DIFF_A1),
                   "resim": "RED", "checksum_changed": "no"}]
        self.assertTrue(loop.is_duplicate_direction(DIFF_A2, priors))
        self.assertFalse(loop.is_duplicate_direction(DIFF_B, priors))

    def test_duplicate_legacy_oneliner_priors(self):
        one = "@@ -1446,4 +1446,4 @@ / - decoder_trigger <= mem_do_rinst;"
        priors = [{"attempt": 1, "patch": one,
                   "resim": "RED", "checksum_changed": "no"}]
        self.assertTrue(loop.is_duplicate_direction(one, priors))
        other = "@@ -377,1 +377,1 @@ / -reg mem_la_read = 0;"
        self.assertFalse(loop.is_duplicate_direction(other, priors))

    def test_empty_priors_never_duplicate(self):
        self.assertFalse(loop.is_duplicate_direction(DIFF_A1, []))


def _run_with_calls(run_id, call_side_effect, resim_counts=None):
    """Run loop.main with mocked seams; returns (rc, run_dir, prompts)."""
    run_dir = os.path.join(ROOT, "runs", run_id)
    prompts = []

    def fake_call(prompt):
        prompts.append(prompt)
        eff = call_side_effect[len(prompts) - 1]
        if isinstance(eff, Exception):
            raise eff
        return eff

    def fake_apply(diff_text, tree, tmp_path, spec=None):
        return {"guard_ok": True, "reasons": [], "applied": True,
                "repaired": False, "patch_lines": 2,
                "files": ["picorv32.v"], "applied_diff": diff_text}

    def fake_resim(tree, case, seed, boot_path, verdict_path):
        if resim_counts is not None:
            resim_counts.append(1)
        with open(boot_path, "w", encoding="utf-8") as fh:
            fh.write("mock boot after\ntohost: FAIL 0xdead\n")
        verdict = {"case": case["name"], "pass": False,
                   "tohost": "FAIL 0xdead",
                   "checksum": "deadbeef",
                   "cycles": 2009, "seed": seed,
                   "sim_time_utc": loop.utc_now()}
        with open(verdict_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(verdict) + "\n")
        return verdict

    patches = [
        mock.patch.object(loop, "run_sim_before",
                          side_effect=_fake_red_sim),
        mock.patch.object(loop, "rtl_excerpt",
                          return_value="line 1: fake excerpt"),
        mock.patch.object(loop, "call_text_with_backoff",
                          side_effect=fake_call),
        mock.patch.object(loop, "try_apply_diff",
                          side_effect=fake_apply),
        mock.patch.object(loop, "resim_after", side_effect=fake_resim),
        mock.patch.object(loop.visual, "generate", return_value={
            "latency_ms": 1, "request_id": "img-mock", "bytes": 10,
            "sha256": "ab" * 32, "transport": "mock",
            "model": "mock-image"}),
    ]
    return patches, prompts, run_dir, fake_resim


class LoopDiversity(unittest.TestCase):
    def _start(self, test_case, run_id, effects):
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        resim_counts = []
        patches, prompts, run_dir, _ = _run_with_calls(
            run_id, effects, resim_counts)
        for p in patches:
            self.addCleanup(p.stop)
            p.start()
        v3 = os.path.join(ROOT, "agent", "prompts", "v3.txt")
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id, "--prompt", v3])
        return rc, run_dir, prompts, resim_counts

    def test_duplicate_reask_without_burning_resim(self):
        # call1 -> A1 (attempt 1), call2 -> A2 same direction (attempt 2
        # duplicate -> ONE re-ask), call3 -> B (attempt 2 proceeds),
        # call4 -> B-ish repeat tolerated (attempt 3, no second re-ask).
        effects = [_resp(DIFF_A1, 1), _resp(DIFF_A2, 2),
                   _resp(DIFF_B, 3), _resp(DIFF_B, 4)]
        rc, run_dir, prompts, resims = self._start(
            self, "test-diversity-reask", effects)
        self.assertEqual(rc, 1)  # still RED after 3 attempts
        self.assertEqual(len(prompts), 4)  # 3 consumed + 1 re-ask
        self.assertEqual(len(resims), 3)  # no resim burned for the dup
        # The re-ask prompt names the avoid explicitly.
        self.assertIn("DIFFERENT", prompts[2])
        self.assertIn("mem_do_rinst", prompts[2])
        # Deciding request logged verbatim incl. the re-ask prompt.
        treq = _read_json(os.path.join(run_dir, "api", "text_request.json"))
        self.assertEqual(treq.get("prompt"), prompts[-1])
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        dups = [a for a in meta["attempts"]
                if a.get("duplicate_direction")]
        self.assertEqual(len(dups), 1)
        self.assertEqual(meta.get("attempts_consumed"), 3)

    def test_max_one_duplicate_per_run(self):
        effects = [_resp(DIFF_A1, 1), _resp(DIFF_A2, 2),
                   _resp(DIFF_A1, 3), _resp(DIFF_A1, 4),
                   _resp(DIFF_A1, 5)]
        rc, run_dir, prompts, resims = self._start(
            self, "test-diversity-maxone", effects)
        self.assertEqual(rc, 1)
        # attempt1 + attempt2(dup -> 1 re-ask) + attempt3 tolerated = 4
        # text calls; a 5th call would mean a second re-ask happened.
        self.assertEqual(len(prompts), 4)
        self.assertEqual(len(resims), 3)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        dups = [a for a in meta["attempts"]
                if a.get("duplicate_direction")]
        self.assertEqual(len(dups), 1)


class FlakeAccounting(unittest.TestCase):
    def _start(self, run_id, effects):
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        resim_counts = []
        patches, prompts, run_dir, _ = _run_with_calls(
            run_id, effects, resim_counts)
        for p in patches:
            self.addCleanup(p.stop)
            p.start()
        v3 = os.path.join(ROOT, "agent", "prompts", "v3.txt")
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id, "--prompt", v3])
        return rc, run_dir, prompts, resim_counts

    def test_flake_storm_preserves_diagnostic_budget(self):
        # Wave-1 re-fire shape: attempt 1 completes, attempt 2's transport
        # dies exhausted (3 EMPTYs inside one call_text_with_backoff), then
        # transport recovers: all 3 diagnostic attempts must still run.
        # Three mutually-distinct directions (no duplicate re-ask
        # interferes with this accounting test).
        effects = [_resp(DIFF_A1, 1),
                   loop.LiveAPIError("live text call returned EMPTY "
                                     "content (latency 1ms, request x)"),
                   _resp(DIFF_B, 3), _resp(DIFF_ADD_ONLY, 4)]
        rc, run_dir, prompts, resims = self._start(
            "test-flake-storm", effects)
        self.assertEqual(rc, 1)  # RED after 3 diagnostics, not triage
        self.assertEqual(len(resims), 3)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 3)
        self.assertEqual(meta.get("transport_failures"), 1)
        self.assertEqual(len(meta.get("attempts", [])), 3)
        self.assertEqual(meta.get("status"), "red")

    def test_persistent_flakes_stay_bounded_and_degrade(self):
        effects = [loop.LiveAPIError("mock flake: down")] * 10
        rc, run_dir, prompts, resims = self._start(
            "test-flake-persistent", effects)
        self.assertEqual(rc, 2)  # bounded degrade to triage, never hang
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("status"), "triage")
        self.assertEqual(meta.get("attempts_consumed"), 0)
        self.assertEqual(meta.get("transport_failures"),
                         loop.MAX_TRANSPORT_FAILURES)
        self.assertLessEqual(len(prompts), loop.MAX_TRANSPORT_FAILURES)
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "api", "text_request.json")))

    def test_transport_waits_accumulate_across_attempts(self):
        r1, w1 = _resp(DIFF_A1, 1)
        r3, _w3 = _resp(DIFF_B, 2)
        # Third direction is distinct (no duplicate re-ask fires here).
        effects = [(r1, [5.0]), (r3, [15.0]),
                   _resp(DIFF_ADD_ONLY, 3)]
        rc, run_dir, prompts, _resims = self._start(
            "test-flake-waits", effects)
        self.assertEqual(rc, 1)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("transport_retries"), [5.0, 15.0])

    def test_failed_sequence_keeps_its_waits(self):
        def always_flake(prompt):
            raise loop.LiveAPIError("live text call returned EMPTY content")
        with self.assertRaises(loop.LiveAPIError) as ctx:
            loop.call_text_with_backoff(
                "p", call_fn=always_flake,
                sleep_fn=lambda s: None, rand_fn=lambda a, b: 0.0)
        self.assertEqual(list(getattr(ctx.exception, "transport_waits",
                                       [])),
                         [5.0, 15.0])


if __name__ == "__main__":
    unittest.main()
