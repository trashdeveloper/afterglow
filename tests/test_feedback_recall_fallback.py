"""Failure-feedback + recall-hygiene + cone-fallback tests (fixtures only).

Run: python3 tests/test_feedback_recall_fallback.py -v   (from repo root)

Evidence-first (no live API, no keys, no real sim builds):

- test_attempt2_prompt_carries_prior_block: two applied-but-RED attempts
  feed attempt 3 a compact PRIOR_ATTEMPTS block (patch one-liner +
  resim outcome + checksum-delta); attempt 1 prompt has no block.
- test_feedback_block_logged_verbatim_in_text_request: the deciding
  text_request.json prompt contains the PRIOR_ATTEMPTS block verbatim.
- test_self_recall_excluded: a fixture bundle with the CURRENT run-id
  never appears in recall results; without the filter it does (baseline).
- test_same_case_recall_excluded: prior bundles of the SAME case are
  dropped from recall and counted in recall_excluded.
- test_fallback_triggers_on_empty_static_slice: static slice 0 lines ->
  fallback window around the top localization suspect, capped, with
  cone_source fallback-window (never silently empty: always logged).
- test_fallback_stays_capped: oversized fallback window is capped.
- test_static_cone_keeps_source_static: non-empty static slice logs
  cone_source static.

Covers the mutbigN-v3-21 lesson (attempt-1 `|| mem_done` applied but
changed nothing, yet attempt-3 got no mention of it), the
mutbigN-v3-02-rerun self-match recall, and the picorv32 empty-cone runs.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop
from afterglow_core import memory as fix_memory

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

TRACE_FIX = (
    "- tb seed 7\n"
    "cycle 10 fetch ok\n"
    "TRAP: uart recv_divcnt mismatch got 0xE0 want 0xA5\n"
    "tohost: FAIL 0xdead\n"
)
VERDICT_FIX = '{"case": "uart-start-half", "pass": false, "tohost": "FAIL 0xdead"}'

TOP_V = """module top(input clk, input [7:0] a, b, output [7:0] recv_divcnt);
wire [7:0] adder_out;
alu u_alu(.a(a), .b(b), .y(adder_out));
assign recv_divcnt = adder_out;
endmodule
"""
ALU_V = """module alu(input [7:0] a, b, output [7:0] y);
assign y = a + b;
endmodule
"""

MODEL_DIFF_1 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -376,1 +376,1 @@
-wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) ||
+wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) ||
"""

MODEL_DIFF_2 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -377,1 +377,1 @@
-reg mem_la_read = 0;
+reg mem_la_read = 1;
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


def _tree_with(top_text=TOP_V, alu_text=ALU_V):
    tree = tempfile.mkdtemp()
    _write(os.path.join(tree, "top.v"), top_text)
    _write(os.path.join(tree, "alu.v"), alu_text)
    return tree


def _bundle(runs, name, record=None, case="some-case"):
    d = os.path.join(runs, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "meta.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"run_id": name, "status": "green", "diagnosed": True,
                   "case": case, "case_id": case}, fh)
    if record is not None:
        with open(os.path.join(d, "fingerprint.json"), "w",
                   encoding="utf-8") as fh:
            json.dump(record, fh)
    return d


def _rec(fphex, signals):
    return {"fingerprint": fphex, "trap_line": "TRAP: x",
            "oracle_sig": "FAIL 0xdead", "signals": list(signals)}


class PriorAttemptsHelpers(unittest.TestCase):
    def test_patch_one_liner(self):
        one = loop.patch_one_liner(MODEL_DIFF_1)
        self.assertIn("@@", one)
        self.assertIn("mem_done", one)
        self.assertLessEqual(len(one), 160)
        self.assertNotIn("\n", one)

    def test_format_prior_block_rows(self):
        priors = [
            {"attempt": 1, "patch": "@@ -376 +wire mem_done",
             "resim": "RED (tohost=FAIL 0xdead)",
             "checksum_changed": "no"},
            {"attempt": 2, "patch": "@@ -377 +reg mem_la_read",
             "resim": "RED (tohost=FAIL 0xdead)",
             "checksum_changed": "yes"},
        ]
        block = loop.format_prior_attempts_block(priors)
        self.assertIn("PRIOR_ATTEMPTS", block)
        self.assertIn("mem_done", block)
        self.assertIn("checksum changed: no", block)
        self.assertIn("checksum changed: yes", block)
        self.assertIn("RED", block)

    def test_format_prior_block_empty(self):
        self.assertEqual(loop.format_prior_attempts_block([]), "")


class LoopFeedback(unittest.TestCase):
    def _run_three_red(self, run_id):
        """Three applied-but-RED attempts; returns (rc, run_dir, prompts)."""
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        prompts = []

        def fake_call(prompt):
            prompts.append(prompt)
            n = len(prompts)
            diff = MODEL_DIFF_1 if n < 2 else MODEL_DIFF_2
            return ({"content": json.dumps(
                {"diagnosis": "mock diagnosis %d" % n,
                 "patch_diff": diff, "expected": "tohost: PASS"}),
                "raw": {}, "latency_ms": 5,
                "request_id": "mock-r%d" % n,
                "model": loop.TEXT_MODEL, "usage": {}}, [])

        def fake_apply(diff_text, tree, tmp_path, spec=None):
            return {"guard_ok": True, "reasons": [], "applied": True,
                    "repaired": False, "patch_lines": 2,
                    "files": ["picorv32.v"], "applied_diff": diff_text}

        checksums = ["deadbeef", "beefdead", "deadbeef"]

        def fake_resim(tree, case, seed, boot_path, verdict_path):
            n = len(prompts)
            with open(boot_path, "w", encoding="utf-8") as fh:
                fh.write("mock boot after\ntohost: FAIL 0xdead\n")
            verdict = {"case": case["name"], "pass": False,
                       "tohost": "FAIL 0xdead",
                       "checksum": checksums[min(n - 1, 2)],
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
        for p in patches:
            self.addCleanup(p.stop)
            p.start()
        v3 = os.path.join(ROOT, "agent", "prompts", "v3.txt")
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id, "--prompt", v3])
        return rc, run_dir, prompts

    def test_attempt2_prompt_carries_prior_block(self):
        rc, run_dir, prompts = self._run_three_red("test-feedback-3red")
        self.assertEqual(rc, 1)  # still RED after 3 attempts
        self.assertEqual(len(prompts), 3)
        # Attempt 1: no history yet.
        self.assertNotIn("PRIOR_ATTEMPTS", prompts[0])
        # Attempt 2: attempt-1's applied-but-ineffective patch is named,
        # with resim outcome and checksum delta.
        self.assertIn("PRIOR_ATTEMPTS", prompts[1])
        self.assertIn("mem_done", prompts[1])
        self.assertIn("RED", prompts[1])
        self.assertIn("checksum changed: no", prompts[1])
        # Attempt 3: both priors present, incl. the checksum flip.
        self.assertIn("mem_done", prompts[2])
        self.assertIn("mem_la_read", prompts[2])
        self.assertIn("checksum changed: yes", prompts[2])

    def test_feedback_block_logged_verbatim_in_text_request(self):
        _rc, run_dir, prompts = self._run_three_red("test-feedback-logged")
        treq = _read_json(os.path.join(run_dir, "api", "text_request.json"))
        logged = treq.get("prompt", "")
        # The deciding (attempt-3) request carries the block verbatim:
        # same marker + same prior rows as the live attempt prompts.
        self.assertIn("PRIOR_ATTEMPTS", logged)
        self.assertIn("mem_done", logged)
        self.assertIn("checksum changed: no", logged)
        self.assertEqual(logged, prompts[-1])


class RecallHygiene(unittest.TestCase):
    def test_self_recall_excluded(self):
        with tempfile.TemporaryDirectory() as runs:
            _bundle(runs, "run-self",
                    _rec("aa" * 32, ["recv_divcnt", "rdata"]))
            _bundle(runs, "run-other",
                    _rec("bb" * 32, ["clk"]))
            tree = _tree_with()
            self.addCleanup(shutil.rmtree, tree, True)
            # Baseline: without the filter the self bundle is recalled.
            _text0, counts0, _rec0 = loop.assemble_harvest(
                TRACE_FIX, VERDICT_FIX, tree, ["top.v", "alu.v"], runs)
            self.assertIn("run-self", [r[0] for r in counts0["recall"]])
            # With current_run_id set, self is gone and counted.
            _text1, counts1, _rec1 = loop.assemble_harvest(
                TRACE_FIX, VERDICT_FIX, tree, ["top.v", "alu.v"], runs,
                current_run_id="run-self")
            self.assertNotIn("run-self", [r[0] for r in counts1["recall"]])
            self.assertGreaterEqual(counts1.get("recall_excluded", 0), 1)

    def test_same_case_recall_excluded(self):
        with tempfile.TemporaryDirectory() as runs:
            _bundle(runs, "old-same-case",
                    _rec("cc" * 32, ["recv_divcnt", "rdata"]),
                    case="mut-bigN-02")
            _bundle(runs, "old-other-case",
                    _rec("cc" * 32, ["recv_divcnt", "rdata"]),
                    case="mut-bigN-09")
            tree = _tree_with()
            self.addCleanup(shutil.rmtree, tree, True)
            _text, counts, _fp = loop.assemble_harvest(
                TRACE_FIX, VERDICT_FIX, tree, ["top.v", "alu.v"], runs,
                current_run_id="mutbigN-v3-02-rerun",
                current_case="mut-bigN-02")
            ids = [r[0] for r in counts["recall"]]
            self.assertNotIn("old-same-case", ids)
            self.assertIn("old-other-case", ids)
            self.assertGreaterEqual(counts.get("recall_excluded", 0), 1)

    def test_memory_exclude_param(self):
        with tempfile.TemporaryDirectory() as runs:
            _bundle(runs, "run-01", _rec("aa" * 32, ["pc"]))
            _bundle(runs, "run-02", _rec("bb" * 32, ["recv_divcnt"]))
            ranked = fix_memory.recall_similar(["recv_divcnt"], runs, k=5)
            self.assertEqual(ranked[0][0], "run-02")
            ranked2 = fix_memory.recall_similar(
                ["recv_divcnt"], runs, k=5, exclude="run-02")
            self.assertNotIn("run-02", [r[0] for r in ranked2])


class ConeFallback(unittest.TestCase):
    # Seed 'zzz_absent' never occurs in the tree -> static slice is 0
    # lines; 'recv_divcnt' IS present -> fallback window must fire.
    TRACE_ABSENT_SEED = (
        "- tb seed 7\n"
        "TRAP: zzz_absent zzz_absent cpu trap asserted\n"
        "TRAP: uart recv_divcnt mismatch\n"
        "tohost: FAIL 0xdead\n"
    )

    def test_fallback_triggers_on_empty_static_slice(self):
        tree = _tree_with()
        self.addCleanup(shutil.rmtree, tree, True)
        with tempfile.TemporaryDirectory() as runs:
            text, counts, _rec = loop.assemble_harvest(
                self.TRACE_ABSENT_SEED, VERDICT_FIX, tree,
                ["top.v", "alu.v"], runs)
        self.assertEqual(counts.get("cone_source"), "fallback-window")
        self.assertGreater(counts.get("cone_lines", 0), 0)
        self.assertIn("### CONE_SLICE", text)
        self.assertNotIn("(empty cone)", text)
        self.assertIn("top.v:", text)

    def test_fallback_stays_capped(self):
        with tempfile.TemporaryDirectory() as tree:
            big = ["module big(input clk, output [7:0] y);"]
            for i in range(400):
                big.append("wire [7:0] w%d;" % i)
                big.append("assign w%d = recv_divcnt + %d;" % (i, i))
            big.append("assign y = w399;")
            big.append("endmodule")
            _write(os.path.join(tree, "big.v"), "\n".join(big) + "\n")
            with tempfile.TemporaryDirectory() as runs:
                _text, counts, _rec = loop.assemble_harvest(
                    self.TRACE_ABSENT_SEED, VERDICT_FIX, tree,
                    ["big.v"], runs)
        self.assertEqual(counts.get("cone_source"), "fallback-window")
        self.assertLessEqual(counts.get("cone_lines", 0), 200)
        self.assertLessEqual(counts.get("pack_lines", 0), 200)
        self.assertLessEqual(counts.get("pack_tokens", 0), 2500)

    def test_static_cone_keeps_source_static(self):
        tree = _tree_with()
        self.addCleanup(shutil.rmtree, tree, True)
        with tempfile.TemporaryDirectory() as runs:
            _text, counts, _rec = loop.assemble_harvest(
                TRACE_FIX, VERDICT_FIX, tree, ["top.v", "alu.v"], runs)
        self.assertEqual(counts.get("cone_source"), "static")
        self.assertGreater(counts.get("cone_lines", 0), 0)

    def test_cone_source_always_logged(self):
        tree = _tree_with()
        self.addCleanup(shutil.rmtree, tree, True)
        with tempfile.TemporaryDirectory() as runs:
            # No signals at all -> even fallback is empty, but logged.
            _text, counts, _rec = loop.assemble_harvest(
                "cycle 0 ok\ncycle 1 ok\n", VERDICT_FIX, tree,
                ["top.v", "alu.v"], runs)
        self.assertIn("cone_source", counts)


if __name__ == "__main__":
    unittest.main()
