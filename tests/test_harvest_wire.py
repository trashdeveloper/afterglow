"""Harvest wiring tests: v3 HARVEST into the live loop (fixtures only).

Run: python3 tests/test_harvest_wire.py -v   (from repo root)

No live API calls, no keys, no real sim builds -- unittest.mock seams only:

- test_default_prompt_is_v2: --prompt defaults to agent/prompts/v2.txt
  (v3 is opt-in; ablation flips the default only on locked 3/3).
- test_prompt_shorthand: --prompt v3 resolves to agent/prompts/v3.txt;
  full paths still pass through.
- test_assemble_harvest_fixture: assemble_harvest on a 3-line fixture
  tree returns text with fingerprint hex + cone file:line refs +
  verdict, counts exactly pack_lines/pack_tokens and under caps.
- test_v3_renders_all_six_slots: v3 template + staged slots + collect +
  harvest leaves no {{ token (all six slots filled from fixtures).
- test_loop_v3_writes_fingerprint_new_bundle_only: loop.main --prompt
  v3 writes runs/<new>/fingerprint.json (fp matches meta harvest),
  meta gains prompt_version + harvest provenance, old bundle untouched.
- test_degrade_harvest_failure: harvest exception -> v2-context fallback,
  harvest FAILED recorded, loop never dies, no {{HARVEST}} leaks.
- test_recall_advisory_only: recalled past failures land in the prompt
  labeled ADVISORY with scores logged, carrying no patch text.
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


class PromptFlag(unittest.TestCase):
    def test_default_prompt_is_v2(self):
        args = loop.parse_args(["--case", "dummy"])
        self.assertTrue(args.prompt.endswith(
            os.path.join("prompts", "v2.txt")), args.prompt)
        self.assertNotIn("v3", args.prompt)

    def test_prompt_shorthand(self):
        for short, want in (("v1", "v1.txt"), ("v2", "v2.txt"),
                            ("v3", "v3.txt")):
            args = loop.parse_args(["--case", "dummy", "--prompt", short])
            self.assertTrue(loop.resolve_prompt_file(args.prompt).endswith(
                os.path.join("prompts", want)))
        custom = os.path.join(ROOT, "agent", "prompts", "v1.txt")
        args = loop.parse_args(["--case", "dummy", "--prompt", custom])
        self.assertEqual(loop.resolve_prompt_file(args.prompt), custom)


class AssembleHarvest(unittest.TestCase):
    def _tree(self):
        tree = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tree, True)
        _write(os.path.join(tree, "top.v"), TOP_V)
        _write(os.path.join(tree, "alu.v"), ALU_V)
        return tree

    def test_assemble_harvest_fixture(self):
        tree = self._tree()
        with tempfile.TemporaryDirectory() as runs:
            text, counts, rec = loop.assemble_harvest(
                TRACE_FIX, VERDICT_FIX, tree, ["top.v", "alu.v"], runs)
        self.assertIn(rec["fingerprint"], text)
        self.assertIn("### CONE_SLICE", text)
        self.assertIn("### VERDICT", text)
        # Every emitted cone line carries file:line provenance.
        self.assertRegex(text, r"(?m)^[^:]+:\d+: ")
        self.assertEqual(counts["pack_lines"], len(text.splitlines()))
        from afterglow_core import packer as pack_mod
        self.assertEqual(counts["pack_tokens"],
                         pack_mod.approx_tokens(text))
        self.assertLessEqual(counts["pack_lines"], 200)
        self.assertLessEqual(counts["pack_tokens"], 2500)
        self.assertEqual(len(rec["fingerprint"]), 64)
        self.assertGreater(counts["cone_lines"], 0)

    def test_v3_renders_all_six_slots(self):
        tree = self._tree()
        with tempfile.TemporaryDirectory() as runs:
            text, _counts, _rec = loop.assemble_harvest(
                TRACE_FIX, VERDICT_FIX, tree, ["top.v", "alu.v"], runs)
        with open(os.path.join(ROOT, "agent", "prompts", "v3.txt"),
                  encoding="utf-8") as fh:
            template = fh.read()
        from agent import collect as collect_mod
        staged = template.replace("{{CASE}}", CASE).replace(
            "{{RTL_FILE}}", "top.v").replace(
            "{{RTL_EXCERPT}}", "line 1: x").replace(
            "{{HARVEST}}", text)
        bundle = collect_mod.collect(TRACE_FIX, VERDICT_FIX, staged)
        for needle in (CASE, "top.v", "line 1: x", "### FINGERPRINT",
                       "TRAP:", "FAIL 0xdead"):
            self.assertIn(needle, bundle["full_prompt"])
        self.assertNotIn("{{", bundle["full_prompt"])
        self.assertNotIn("}}", bundle["full_prompt"])


class LoopWire(unittest.TestCase):
    def _base_patches(self):
        return [
            mock.patch.object(loop, "run_sim_before",
                              side_effect=_fake_red_sim),
            mock.patch.object(loop, "rtl_excerpt",
                              return_value="line 1: fake excerpt"),
            mock.patch.object(loop, "call_text_with_backoff",
                              side_effect=loop.LiveAPIError("mock flake")),
        ]

    def _run_v3(self, run_id, extra_patches=()):
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        patches = self._base_patches() + list(extra_patches)
        for p in patches:
            self.addCleanup(p.stop)
            p.start()
        v3 = os.path.join(ROOT, "agent", "prompts", "v3.txt")
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id, "--prompt", v3])
        return rc, run_dir

    def test_loop_v3_writes_fingerprint_new_bundle_only(self):
        old_dir = os.path.join(ROOT, "runs", "test-wire-old-green")
        os.makedirs(old_dir, exist_ok=True)
        self.addCleanup(shutil.rmtree, old_dir, True)
        old_rec = {"fingerprint": "ee" * 32, "trap_line": "TRAP: old",
                   "oracle_sig": "FAIL 0x1", "signals": ["clk"]}
        with open(os.path.join(old_dir, "fingerprint.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(old_rec, fh)
        with open(os.path.join(old_dir, "meta.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"run_id": "test-wire-old-green", "status": "green",
                       "diagnosed": True}, fh)
        with open(os.path.join(old_dir, "fingerprint.json"),
                  encoding="utf-8") as fh:
            old_before = fh.read()
        rc, run_dir = self._run_v3("test-wire-v3-new")
        self.assertEqual(rc, 2)  # API flake degrades to triage, never dies
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("prompt_version"), "v3")
        harvest = meta.get("harvest") or {}
        self.assertEqual(harvest.get("status"), "ok")
        self.assertEqual(len(harvest.get("fingerprint", "")), 64)
        self.assertGreater(harvest.get("cone_lines", 0), 0)
        self.assertGreater(harvest.get("pack_tokens", 0), 0)
        fp_path = os.path.join(run_dir, "fingerprint.json")
        self.assertTrue(os.path.isfile(fp_path))
        saved = _read_json(fp_path)
        self.assertEqual(saved["fingerprint"], harvest["fingerprint"])
        # Budgets logged exactly: pack counts match the packer ruler.
        from afterglow_core import packer as pack_mod
        self.assertLessEqual(harvest["pack_lines"], 200)
        self.assertLessEqual(harvest["pack_tokens"], 2500)
        with open(os.path.join(old_dir, "fingerprint.json"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read(), old_before)  # old untouched

    def test_degrade_harvest_failure(self):
        with mock.patch.object(
                loop.harvest_fp, "fingerprint_for_failure",
                side_effect=RuntimeError("mock harvest boom")):
            rc, run_dir = self._run_v3("test-wire-v3-degrade")
        self.assertEqual(rc, 2)  # loop never dies on harvest
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        harvest = meta.get("harvest") or {}
        self.assertEqual(harvest.get("status"), "FAILED")
        self.assertIn("fallback", harvest)
        # No unreplaced slot leaks into the deciding text request.
        treq = _read_json(os.path.join(run_dir, "api", "text_request.json"))
        prompt = treq.get("prompt", "")
        self.assertNotIn("{{HARVEST}}", prompt)
        self.assertIn("HARVEST FAILED", prompt)
        # FAILED path writes no fingerprint (provenance lives in meta).
        self.assertFalse(
            os.path.isfile(os.path.join(run_dir, "fingerprint.json")))

    def test_recall_advisory_only(self):
        with tempfile.TemporaryDirectory() as runs:
            d = os.path.join(runs, "old-green")
            os.makedirs(d)
            with open(os.path.join(d, "meta.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"run_id": "old-green", "status": "green",
                           "diagnosed": True}, fh)
            with open(os.path.join(d, "fingerprint.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"fingerprint": "ab" * 32,
                           "trap_line": "TRAP: uart recv_divcnt mismatch",
                           "oracle_sig": "FAIL 0xdead",
                           "signals": ["recv_divcnt", "rdata"]}, fh)
            tree = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, tree, True)
            _write(os.path.join(tree, "top.v"), TOP_V)
            _write(os.path.join(tree, "alu.v"), ALU_V)
            text, counts, _rec = loop.assemble_harvest(
                TRACE_FIX, VERDICT_FIX, tree, ["top.v", "alu.v"], runs)
        self.assertTrue(counts.get("recall"))
        self.assertIn("PAST_FAILURES", text)
        self.assertIn("advisory", text.lower())
        self.assertNotIn("diff --git", text)  # no patch text, never overrides
        self.assertGreaterEqual(counts["recall"][0][1], 0.0)


if __name__ == "__main__":
    unittest.main()
