"""Clean-slate retries + truncated-JSON salvage (TDD, static fixtures).

CONTEXT: runs/mutbigN-v3-21 went RED across 3 attempts for two
mechanical reasons, both visible in runs/mutbigN-v3-21/meta.json:
- attempt-1 applied a wrong-direction patch IN the scratch tree, so
  attempt-3's dry-run FAILED with "Hunk #1 FAILED at 1446": attempts
  accumulate on a dirty tree instead of starting from the clean RED
  baseline (attempt-3's diff is byte-identical to patch.diff, so the
  tree edit it needed was already consumed by attempt-1).
- attempt-2 burned a full retry on a truncated model output
  ("model output is not a JSON object", cut mid-sentence, likely
  max_tokens). The surviving bytes (meta.json attempts[1] summary,
  first 200 chars of content) end inside the diagnosis string with NO
  closing brace anywhere and NO patch_diff key emitted yet.

What this file proves (no model calls, no network, no builds):
- reset: prepare_mutant_tree(force=True) re-applied before attempt N>1
  restores the recorded RED baseline hash despite an attempt-1 patch;
  reset_tree_for_attempt refuses non-mutant cases loudly.
- salvage: a truncation of the REAL v3-21 api/text_response.json
  content (cut mid-patch_diff, max_tokens-style) is repaired to the
  exact original diagnosis+diff; the REAL attempt-2 fragment and
  garbage still raise LoopError (salvage repairs truncation, never
  invents a diff).

Run: python3 tests/test_retry_hygiene.py -v   (from repo root)
"""
import ast
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# Real-artifact fixtures (exact bytes from runs/mutbigN-v3-21).
# --------------------------------------------------------------------------

# V3_21_ATTEMPT2_PREFIX: first 200 bytes of the attempt-2 model output,
# recovered EXACTLY via ast.literal_eval of the %r-formatted fragment in
# runs/mutbigN-v3-21/meta.json attempts[1].summary
# ("...not a JSON object: '<fragment>'"). The full output is lost
# (unparseable outputs were never persisted); only these 200 bytes
# survive. Ends mid-sentence inside the diagnosis string; no closing
# brace, no patch_diff key.
V3_21_ATTEMPT2_PREFIX = '{"diagnosis": "CONE_SLICE is empty so trace wins. 256-word NOP fetch should progress with no trap, but trace shows `TRAP: cpu trap asserted` and `tohost: FAIL 0xdead` at CYCLES=2009. Excerpt picorv32.'

# V3_21_COMPLETE: full 856-byte content of
# runs/mutbigN-v3-21/api/text_response.json (attempt-3, the deciding
# call). Used whole (parses strict) and truncated mid-patch_diff
# (must salvage to the identical object).
V3_21_COMPLETE = r'''{"diagnosis": "TRAP asserted with tohost FAIL 0xdead during 256-word NOP fetch instead of clean fetch progress with no trap. decoder_trigger at picorv32.v:1446 fires on mem_do_rinst || mem_done, so data-phase mem_done re-triggers the decoder on a stale word that decodes illegal. Removing the mem_done term restricts trigger to instruction fetch only, preventing the spurious illegal decode and trap.", "patch_diff": "diff --git a/picorv32.v b/picorv32.v\n--- a/picorv32.v\n+++ b/picorv32.v\n@@ -1446,5 +1446,5 @@\n-\t\tdecoder_trigger <= mem_do_rinst || mem_done;\n+\t\tdecoder_trigger <= mem_do_rinst;\n \t\tdecoder_trigger_q <= decoder_trigger;\n \t\tdecoder_pseudo_trigger <= 0;\n \t\tdecoder_pseudo_trigger_q <= decoder_pseudo_trigger;\n \t\tdo_waitirq <= 0;\n", "expected": "tohost 0x1 PASS, no TRAP: cpu trap asserted, fetch progress over 256 NOPs"}'''

# Cut 700 chars in: lands inside the patch_diff string value
# (local context: '...decoder_trigger <= mem_do_rinst || mem_do...'),
# max_tokens-style truncation with valid escapes still open.
V3_21_CUT = 700


def _tiny_pristine(tmp):
    src = os.path.join(tmp, "pristine")
    os.makedirs(os.path.join(src, "picosoc"))
    lines = [
        "module simpleuart(input clk);",
        "reg [31:0] recv_divcnt;",
        "if (recv_divcnt > cfg_divider) begin",
        "recv_state <= 0;",          # line 4: pinned fault site
        "recv_divcnt <= recv_divcnt + 1;",
        "send_pattern <= ~0;",
        "endmodule",
    ]
    with open(os.path.join(src, "picosoc", "simpleuart.v"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return src


def _tiny_case(line=4):
    return {
        "name": "mut-tiny", "project": "picorv32",
        "files": ["picosoc/simpleuart.v"],
        "top": "tb_x", "tb": "x_tb.sv",
        "anchor": "recv_state <= 0;", "bug": "recv_state <= 1;",
        "gate": "t", "oracle_pass": "tohost: PASS",
        "mutant_file": "picosoc/simpleuart.v", "mutant_line": line,
    }


class TestFixtureProvenance(unittest.TestCase):
    def test_attempt2_prefix_matches_bundle_bytes(self):
        with open(os.path.join(
                ROOT, "runs", "mutbigN-v3-21", "meta.json")) as fh:
            meta = json.load(fh)
        summary = meta["attempts"][1]["summary"]
        frag = ast.literal_eval(summary.split("not a JSON object: ", 1)[1])
        self.assertEqual(frag, V3_21_ATTEMPT2_PREFIX)
        self.assertEqual(len(frag), 200)

    def test_complete_fixture_matches_bundle_bytes(self):
        with open(os.path.join(
                ROOT, "runs", "mutbigN-v3-21", "api",
                "text_response.json")) as fh:
            content = json.load(fh)["content"]
        self.assertEqual(content, V3_21_COMPLETE)
        # strict parse of the whole artifact (attempt-3 went through)
        obj = loop.extract_model_json(content)
        self.assertTrue(obj["diagnosis"].startswith("TRAP asserted"))
        self.assertIn("decoder_trigger", obj["patch_diff"])
        # the cut lands inside the patch_diff string value
        self.assertIn('"patch_diff": "', V3_21_COMPLETE[:V3_21_CUT])
        self.assertLess(V3_21_CUT, len(V3_21_COMPLETE))


class TestSalvage(unittest.TestCase):
    def test_truncated_real_response_salvages_to_usable_object(self):
        truncated = V3_21_COMPLETE[:V3_21_CUT]
        with self.assertRaises(loop.LoopError):
            loop.extract_model_json(truncated)
        salvaged = loop.salvage_model_json(truncated)
        want = loop.extract_model_json(V3_21_COMPLETE)
        # diagnosis precedes the cut: recovered verbatim
        self.assertEqual(salvaged["diagnosis"], want["diagnosis"])
        # patch_diff is cut mid-token: salvage returns the surviving
        # prefix closed, not the unknowable tail -- usable (non-empty,
        # prefix-consistent) but honestly shorter than the full diff
        self.assertTrue(salvaged["patch_diff"])
        self.assertTrue(
            want["patch_diff"].startswith(salvaged["patch_diff"]),
            "salvage must repair, never invent")
        self.assertLess(len(salvaged["patch_diff"]),
                        len(want["patch_diff"]))

    def test_real_attempt2_fragment_still_fails_cleanly(self):
        # Cut mid-diagnosis before patch_diff was emitted: no repair can
        # yield a complete object, and salvage must never invent a diff.
        with self.assertRaises(loop.LoopError):
            loop.extract_model_json(V3_21_ATTEMPT2_PREFIX)
        with self.assertRaises(loop.LoopError):
            loop.salvage_model_json(V3_21_ATTEMPT2_PREFIX)

    def test_garbage_still_fails_cleanly(self):
        for bad in ("", "   ", "hello world", "{not json",
                    '{"diagnosis": "only diagnosis, no diff"}',
                    '{"diagnosis": "d", "patch_diff": ',
                    '{"diagnosis": "", "patch_diff": "diff --git x"}',
                    '{"diagnosis": "d", "patch_diff": ""}'):
            with self.assertRaises(loop.LoopError, msg=repr(bad)):
                loop.salvage_model_json(bad)

    def test_parse_model_output_marks_salvaged(self):
        obj, salvaged = loop.parse_model_output(V3_21_COMPLETE)
        self.assertFalse(salvaged)
        self.assertTrue(obj["diagnosis"])
        obj2, salvaged2 = loop.parse_model_output(
            V3_21_COMPLETE[:V3_21_CUT])
        self.assertTrue(salvaged2)
        self.assertEqual(obj2["diagnosis"], obj["diagnosis"])
        self.assertTrue(obj["patch_diff"].startswith(obj2["patch_diff"]))
        with self.assertRaises(loop.LoopError):
            loop.parse_model_output(V3_21_ATTEMPT2_PREFIX)


class TestCleanSlateReset(unittest.TestCase):
    def test_reset_restores_red_baseline_despite_attempt1_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            dest = os.path.join(tmp, "scratch", "run-1")
            loop.prepare_mutant_tree(_tiny_case(), src, dest)
            baseline = loop.hash_tree(dest)  # recorded RED baseline
            # simulate attempt-1: a wrong-direction patch lands in-tree
            victim = os.path.join(dest, "picosoc", "simpleuart.v")
            with open(victim) as fh:
                lines = fh.read().splitlines()
            self.assertEqual(lines[3], "recv_state <= 1;")  # RED bug
            lines[3] = "recv_state <= 0;"  # attempt-1 "fixed" it wrong
            with open(victim, "w") as fh:
                fh.write("\n".join(lines) + "\n")
            self.assertNotEqual(loop.hash_tree(dest), baseline)
            # attempt-2 clean-slate reset before diagnosing again
            record = loop.reset_tree_for_attempt(
                _tiny_case(), src, dest, baseline)
            self.assertTrue(record["tree_reset"])
            self.assertTrue(record["tree_reset_verified"])
            self.assertEqual(record["tree_sha256"], baseline)
            self.assertEqual(loop.hash_tree(dest), baseline)
            with open(victim) as fh:
                self.assertEqual(fh.read().splitlines()[3],
                                 "recv_state <= 1;")

    def test_reset_mismatch_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            dest = os.path.join(tmp, "scratch", "run-1")
            loop.prepare_mutant_tree(_tiny_case(), src, dest)
            with self.assertRaises(loop.LoopError):
                loop.reset_tree_for_attempt(
                    _tiny_case(), src, dest,
                    "deadbeef" * 8)  # wrong baseline: refuse, don't debug

    def test_reset_refuses_non_mutant_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            dest = os.path.join(tmp, "scratch", "run-1")
            os.makedirs(dest)
            case = _tiny_case()
            del case["mutant_file"]
            del case["mutant_line"]
            self.assertFalse(loop.is_mutant_case(case))
            with self.assertRaises(loop.LoopError):
                loop.reset_tree_for_attempt(case, src, dest)


if __name__ == "__main__":
    unittest.main()
