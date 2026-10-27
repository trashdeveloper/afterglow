"""Consensus diagnosis pilot (TDD, fixtures only -- no API/keys/builds).

CONTEXT: loop diagnoses once per attempt (single-model opinion); flake
storms force retries; per-fix cost ~$0.07; budget $100.

What this file proves (mocked model-call layer only, never verdict/resim):
- predicate: agreement = same target file AND overlapping line window
  (+/-10 lines). Same file + lines within 10 -> agree; same file + lines
  >10 apart -> disagree; different files -> disagree; empty diffs never
  agree.
- 3-0 unanimous proceeds with the majority (3 mock calls, 1 resim GREEN).
- 2-1 proceeds with the majority pair (3 mock calls, 1 resim GREEN, the
  winner is one of the agreeing pair).
- 1-1-1 split consumes the attempt with NO patch and NO resim burned
  (3 mock calls, 0 resims), logs `consensus: SPLIT ...` in meta + the
  text_request proof, and carries all three opinions into the next
  attempt's prior-attempts block.
- flakes retried then excluded: 2 real agreeing votes + 1 exhausted
  transport sequence -> proceeds (3 mock calls, 1 resim GREEN).
- cost-cap: accumulated est >= $0.50 degrades to single-diagnosis with
  `consensus: OFF-cost-cap` logged (1 mock call).
- default-off byte-identical: without --consensus, three distinct
  diagnoses run single-path (3 mock calls for 3 attempts, 3 resims, no
  consensus keys anywhere) -- existing diagnose tests pass unmodified.

Run: PYTHONPATH=. python3 tests/test_consensus.py -v  (repo root)
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

# C1 and C2 target the SAME file within +/-10 lines -> agreement.
DIFF_C1 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1446,4 +1446,4 @@
-wire decoder_trigger <= mem_do_rinst || mem_done;
+wire decoder_trigger <= mem_do_rinst;
"""

DIFF_C2 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1450,1 +1450,1 @@
-reg mem_la_read = 0;
+reg mem_la_read = 1;
"""

# C3 targets the same file but FAR away (>10 lines) -> no agreement.
DIFF_C3_FAR = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -377,1 +377,1 @@
-reg mem_la_read = 0;
+reg mem_la_read = 1;
"""

# C4/C5 target different files -> no agreement with C1.
DIFF_C4 = """diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v
--- a/picosoc/simpleuart.v
+++ b/picosoc/simpleuart.v
@@ -10,1 +10,1 @@
-recv_divcnt <= recv_divcnt + 1;
+recv_divcnt <= recv_divcnt + 2;
"""

DIFF_C5 = """diff --git a/ibex_cs_registers.sv b/ibex_cs_registers.sv
--- a/ibex_cs_registers.sv
+++ b/ibex_cs_registers.sv
@@ -20,1 +20,1 @@
-csr_rdata <= 32'h0;
+csr_rdata <= 32'h1;
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


def _run_loop(run_id, effects, resim_fn, extra_args=None,
              seed_cost_json=None):
    """Run loop.main with mocked seams; returns (rc, run_dir, prompts)."""
    run_dir = os.path.join(ROOT, "runs", run_id)
    prompts = []

    def fake_call(prompt):
        prompts.append(prompt)
        eff = effects[len(prompts) - 1]
        if isinstance(eff, Exception):
            raise eff
        return eff

    def fake_apply(diff_text, tree, tmp_path, spec=None):
        return {"guard_ok": True, "reasons": [], "applied": True,
                "repaired": False, "patch_lines": 2,
                "files": ["picorv32.v"], "applied_diff": diff_text}

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
        mock.patch.object(loop.visual, "generate", return_value={
            "latency_ms": 1, "request_id": "img-mock", "bytes": 10,
            "sha256": "ab" * 32, "transport": "mock",
            "model": "mock-image"}),
    ]
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
    return rc, run_dir, prompts


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


def _red_resim(tree, case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write("mock boot after\ntohost: FAIL 0xdead\n")
    verdict = {"case": case["name"], "pass": False,
               "tohost": "FAIL 0xdead", "checksum": "deadbeef",
               "cycles": 2009, "seed": seed,
               "sim_time_utc": loop.utc_now()}
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(verdict) + "\n")
    return verdict


class ConsensusPredicate(unittest.TestCase):
    def test_same_file_overlapping_window_agrees(self):
        self.assertTrue(loop.consensus_agree(DIFF_C1, DIFF_C2))

    def test_same_file_far_lines_disagree(self):
        self.assertFalse(loop.consensus_agree(DIFF_C1, DIFF_C3_FAR))

    def test_different_files_disagree(self):
        self.assertFalse(loop.consensus_agree(DIFF_C1, DIFF_C4))
        self.assertFalse(loop.consensus_agree(DIFF_C4, DIFF_C5))

    def test_empty_never_agrees(self):
        self.assertFalse(loop.consensus_agree("", DIFF_C1))
        self.assertFalse(loop.consensus_agree(DIFF_C1, ""))
        self.assertFalse(loop.consensus_agree("", ""))

    def test_boundary_ten_lines_agrees_eleven_disagrees(self):
        base = ("diff --git a/f.v b/f.v\n--- a/f.v\n+++ b/f.v\n"
                "@@ -%d,1 +%d,1 @@\n-a;\n+b;\n")
        self.assertTrue(loop.consensus_agree(base % (100, 100),
                                            base % (110, 110)))
        self.assertFalse(loop.consensus_agree(base % (100, 100),
                                             base % (111, 111)))


class ConsensusLoop(unittest.TestCase):
    def tearDown(self):
        for rid in ("test-cons-30", "test-cons-21", "test-cons-split",
                    "test-cons-flake", "test-cons-costcap",
                    "test-cons-defaultoff"):
            shutil.rmtree(os.path.join(ROOT, "runs", rid),
                          ignore_errors=True)

    def _start(self, run_id, effects, resim_fn, extra_args=None,
               seed_cost_json=None):
        run_dir = os.path.join(ROOT, "runs", run_id)
        shutil.rmtree(run_dir, ignore_errors=True)
        return _run_loop(run_id, effects, resim_fn, extra_args,
                         seed_cost_json)

    def test_unanimous_30_proceeds(self):
        effects = [_resp(DIFF_C1, 1), _resp(DIFF_C1, 2),
                   _resp(DIFF_C1, 3)]
        rc, run_dir, prompts = self._start(
            "test-cons-30", effects, _green_resim, ["--consensus"])
        self.assertEqual(rc, 0)  # GREEN on the majority patch
        self.assertEqual(len(prompts), 3)  # 3 mock calls, 1 attempt
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 1)
        treq = _read_json(os.path.join(run_dir, "api",
                                       "text_request.json"))
        self.assertIn("consensus", json.dumps(treq))

    def test_majority_21_proceeds_with_pair(self):
        effects = [_resp(DIFF_C1, 1), _resp(DIFF_C2, 2),
                   _resp(DIFF_C4, 3)]
        rc, run_dir, prompts = self._start(
            "test-cons-21", effects, _green_resim, ["--consensus"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(prompts), 3)
        # Winner is one of the agreeing pair (C1/C2), never the odd one.
        tresp = _read_json(os.path.join(run_dir, "api",
                                        "text_response.json"))
        winner = tresp.get("diagnosis", "")
        self.assertIn(winner, ("mock diagnosis 1", "mock diagnosis 2"))
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 1)

    def test_split_111_consumes_attempt_without_patch_or_resim(self):
        resims = []

        def counting_red(tree, case, seed, boot_path, verdict_path):
            resims.append(1)
            return _red_resim(tree, case, seed, boot_path, verdict_path)

        effects = [_resp(DIFF_C1, 1), _resp(DIFF_C4, 2),
                   _resp(DIFF_C5, 3)] * 3  # every attempt splits
        rc, run_dir, prompts = self._start(
            "test-cons-split", effects, counting_red, ["--consensus"])
        self.assertEqual(len(prompts), 9)  # 3 attempts x 3 mock calls
        self.assertEqual(len(resims), 0)  # no resim ever burned
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 3)
        splits = [a for a in meta.get("attempts", [])
                  if "SPLIT" in str(a.get("consensus", ""))]
        self.assertEqual(len(splits), 3)
        treq = _read_json(os.path.join(run_dir, "api",
                                       "text_request.json"))
        self.assertIn("consensus: SPLIT", json.dumps(treq))
        # All three opinions carried into the next attempt's block.
        priors = prompts[3]
        self.assertIn("PRIOR_ATTEMPTS", priors)
        self.assertIn("picorv32.v", priors)
        self.assertIn("simpleuart.v", priors)
        self.assertIn("ibex_cs_registers.sv", priors)

    def test_flake_excluded_from_votes(self):
        effects = [_resp(DIFF_C1, 1),
                   loop.LiveAPIError("mock flake: EMPTY content"),
                   _resp(DIFF_C2, 3)]
        rc, run_dir, prompts = self._start(
            "test-cons-flake", effects, _green_resim, ["--consensus"])
        self.assertEqual(rc, 0)  # 2 agreeing reals proceed
        self.assertEqual(len(prompts), 3)  # flake retried, excluded
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("transport_failures"), 1)
        self.assertEqual(meta.get("attempts_consumed"), 1)

    def test_cost_cap_degrades_to_single(self):
        effects = [_resp(DIFF_C1, 1)]
        rc, run_dir, prompts = self._start(
            "test-cons-costcap", effects, _green_resim, ["--consensus"],
            seed_cost_json={"est_usd": 0.99, "text_tokens": 99999,
                            "image_calls": 3})
        self.assertEqual(rc, 0)
        self.assertEqual(len(prompts), 1)  # single diagnosis, not 3
        treq = _read_json(os.path.join(run_dir, "api",
                                       "text_request.json"))
        self.assertIn("consensus: OFF-cost-cap", json.dumps(treq))
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertIn("OFF-cost-cap", json.dumps(meta))

    def test_default_off_is_single_path(self):
        effects = [_resp(DIFF_C1, 1), _resp(DIFF_C4, 2),
                   _resp(DIFF_C5, 3)]
        rc, run_dir, prompts = self._start(
            "test-cons-defaultoff", effects, _red_resim)  # no flag
        self.assertEqual(rc, 1)
        self.assertEqual(len(prompts), 3)  # 1 mock call per attempt
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 3)
        self.assertNotIn("consensus", json.dumps(meta).lower())


if __name__ == "__main__":
    unittest.main()
