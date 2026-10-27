"""OPTIMIZE-mode pilot tests: `--goals fix` (default) vs `fix+opt`.

Run: python3 tests/test_optimize.py -v   (from repo root)

Static fixtures + unittest.mock seams only -- no live API calls, no keys,
no real sim builds:

- test_goals_default_fix: --goals defaults to "fix"; "fix+opt" parses;
  anything else is rejected by argparse.
- test_resolve_prompt_v3opt: "v3opt" resolves to agent/prompts/v3opt.txt;
  v1/v2/v3 mappings are untouched (PROMPT_VERSIONS unchanged).
- test_opt_prompt_capped_and_honest: build_opt_prompt assembles under the
  2500-token cap and pins the SIMULATED-cycles baseline; v1/v2/v3 carry no
  opt section.
- test_honesty_no_hardware_claims: the new opt prompt + opt strings make
  no faster-chip/Hz claim (SIMULATED cycles only, by wording).
- test_evaluate_opt_result: pure predicate table (accept only on PASS +
  identical tohost/checksum + strictly lower known SIMULATED cycles).
- test_opt_pass_accepts: full loop fix+opt with fixtures: GREEN fix at
  2000 cycles, opt resim PASS identical checksum/tohost at 1500 cycles ->
  exit 0, opt accepted, cycles_saved 500, opt_patch.diff kept.
- test_opt_behavior_change_rejected: opt resim with a DIFFERENT checksum
  -> exit 0 GREEN preserved, rejected_opt.diff kept, cycles_after stays
  at the fix value, cycles_saved 0.
- test_opt_tohost_change_rejected: opt resim with a different tohost ->
  same GREEN-preserved outcome.
- test_opt_no_saving_rejected: identical checksum/tohost but equal cycles
  -> rejected (no measurable simulated saving), GREEN preserved.
- test_opt_failure_never_ungreens: opt text call flakes -> status failed,
  exit 0, GREEN fix stands.
- test_opt_guard_reject_keeps_green: opt guard refusal -> rejected diff
  kept, GREEN preserved.
- test_default_fix_unchanged: no --goals flag -> goals "fix", opt skipped,
  no opt files/text call, fix GREEN flow byte-identical in behavior.
- test_opt_live02_truncated_cleanly_rejected: fixture replay of the
  opt-live-02 truncated mid-JSON output -> clean FAILED with a recorded
  reason, GREEN preserved.
- test_opt_live04_empty_cleanly_failed: fixture replay of the opt-live-04
  EMPTY output -> clean FAILED, GREEN preserved.
- test_opt_fenced_prose_diff_accepted: prose + fenced diff (not bare
  JSON) parses tolerantly and flows through the SAME guard + SAME
  equivalence gate -> accepted on PASS/identical/lower-cycles resim.
- test_opt_fenced_prose_behavior_change_rejected: same tolerant candidate
  with a changed-checksum resim -> REJECTED, GREEN preserved (tolerance
  is parsing-only, never acceptance).
- test_opt_prose_without_diff_rejected: prose with no diff block -> clean
  FAILED, no invented patch, GREEN preserved.
- test_opt_prompt_v4_bump: prompt bumped v3opt -> v4opt with old file
  kept, both resolvable, explicit output schema + miniature example,
  honest wording, under the token cap.
"""
import json
import os
import re
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import collect
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

FIX_CALL = {
    "content": json.dumps({
        "diagnosis": "receiver divisor wrong, early sample",
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
    "raw": {"id": "mock-fix"},
    "latency_ms": 7,
    "request_id": "mock-fix-req",
    "model": loop.TEXT_MODEL,
    "usage": {},
}

OPT_CALL = {
    "content": json.dumps({
        "diagnosis": "redundant wait wastes simulated cycles, same behavior",
        "expected": "tohost: PASS 0xA5, same checksum, lower CYCLES=",
        "patch_diff": (
            "diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v\n"
            "--- a/picosoc/simpleuart.v\n"
            "+++ b/picosoc/simpleuart.v\n"
            "@@ -1 +1 @@\n"
            "-wait\n"
            "+nowait\n"
        ),
    }),
    "raw": {"id": "mock-opt"},
    "latency_ms": 5,
    "request_id": "mock-opt-req",
    "model": loop.TEXT_MODEL,
    "usage": {},
}

IMG_OK = {"latency_ms": 5, "request_id": "mock-img", "bytes": 100,
          "sha256": "abc123", "transport": "mock",
          "model": "muse-image-1.0"}

# Fixture replay: live opt failures (real strings from the two bundles).
#
# opt-live-02 (runs/opt-live-02/api/opt_text_response.json recorded only
# the error, with the first 200 chars of the raw output):
#   model output is not a JSON object: '{"diagnosis": "The'
# The model output was truncated mid-JSON: no closing brace, no diff
# block. Reconstructed below with that EXACT recorded prefix; the
# truncation property (unterminated, diff-less) is what the parser must
# cleanly reject -- the lost tail is unrecoverable by construction.
OPT_LIVE_02_TRUNCATED = (
    '{"diagnosis": "The receiver wastes SIMULATED cycles in the half-bit '
    'wait because recv_state starts in 1 instead of 0, so the first start '
    'bit is missed and the byte is re-framed after extra SIMULATED cycles'
)
# opt-live-04 (runs/opt-live-04/api/opt_text_response.json): the live
# text call returned EMPTY content (latency 64027ms, request
# chatcmpl-01a17b44-6677-700c-98e8-9e218a945770); refusing to invent a
# diagnosis. Empty output must fail cleanly, never ungreen.
OPT_LIVE_04_EMPTY = ""

# Prose-wrapped diff: valid diagnosis + fenced unified diff, but NOT bare
# JSON. The tolerant parser must extract this candidate; the SAME guard +
# SAME equivalence gate still decide acceptance (tolerance in parsing,
# never in acceptance).
OPT_FENCED_PROSE = (
    "The receiver wastes SIMULATED cycles in the half-bit wait; sampling "
    "one step earlier preserves behavior with identical tohost and "
    "checksum.\n"
    "```diff\n"
    "diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v\n"
    "--- a/picosoc/simpleuart.v\n"
    "+++ b/picosoc/simpleuart.v\n"
    "@@ -1 +1 @@\n"
    "-wait\n"
    "+nowait\n"
    "```\n"
)

CHECKSUM_FIX = "a" * 64
TOHOST_PASS = "PASS 0xA5"


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


def _resim_pair(opt_verdict):
    """resim_after seam: fix GREEN at 2000 cycles, then opt_verdict."""
    fix_verdict = {"case": CASE, "pass": True, "tohost": TOHOST_PASS,
                   "checksum": CHECKSUM_FIX, "cycles": 2000, "seed": 42}

    def _fake(tree, case, seed, boot_path, verdict_path):
        verdict = fix_verdict if _fake.calls == 0 else opt_verdict
        _fake.calls += 1
        with open(boot_path, "w", encoding="utf-8") as fh:
            fh.write("tohost: %s\nCYCLES=%s\n"
                     % (verdict["tohost"], verdict["cycles"]))
        with open(verdict_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(verdict) + "\n")
        return verdict

    _fake.calls = 0
    return _fake


def _applied_outcome(patch_lines=2):
    return {"guard_ok": True, "reasons": [], "applied": True,
            "repaired": False, "patch_lines": patch_lines,
            "files": ["picosoc/simpleuart.v"],
            "applied_diff": "diff text\n"}


def _base_verdict(cycles=2000, checksum=CHECKSUM_FIX, tohost=TOHOST_PASS,
                  passed=True):
    return {"case": CASE, "pass": passed, "tohost": tohost,
            "checksum": checksum, "cycles": cycles, "seed": 42}


class TestOptimize(unittest.TestCase):
    def test_goals_default_fix(self):
        args = loop.parse_args(["--case", "dummy"])
        self.assertEqual(args.goals, "fix")
        args_opt = loop.parse_args(["--case", "dummy", "--goals", "fix+opt"])
        self.assertEqual(args_opt.goals, "fix+opt")
        with self.assertRaises(SystemExit):
            loop.parse_args(["--case", "dummy", "--goals", "fix+turbo"])

    def test_resolve_prompt_v3opt(self):
        self.assertEqual(loop.PROMPT_VERSIONS, ("v1", "v2", "v3"))
        self.assertTrue(loop.resolve_prompt_file("v3opt").endswith(
            os.path.join("prompts", "v3opt.txt")))
        self.assertTrue(os.path.isfile(loop.opt_prompt_path()))
        # v1/v2/v3 mappings untouched by the opt variant.
        self.assertTrue(loop.resolve_prompt_file("v2").endswith(
            os.path.join("prompts", "v2.txt")))
        self.assertTrue(loop.resolve_prompt_file("v3").endswith(
            os.path.join("prompts", "v3.txt")))

    def test_opt_prompt_capped_and_honest(self):
        trace = "".join("cycle %d: uart recv bit ok\n" % i
                        for i in range(200))
        verdict_text = json.dumps(_base_verdict())
        prompt, counts = loop.build_opt_prompt(
            CASE, "picosoc/simpleuart.v", "line 1: fake excerpt",
            trace, verdict_text, _base_verdict())
        self.assertLessEqual(counts["opt_prompt_tokens"], 2500)
        self.assertLessEqual(collect.approx_tokens(prompt), 2500)
        self.assertEqual(len(counts["opt_prompt_sha256"]), 64)
        self.assertIn("SIMULATED", prompt)
        self.assertIn(CHECKSUM_FIX, prompt)
        # v1/v2/v3 carry no opt section (variant lives in v3opt.txt only).
        for ver in ("v1", "v2", "v3"):
            with open(os.path.join(ROOT, "agent", "prompts",
                                   "%s.txt" % ver),
                      encoding="utf-8") as fh:
                self.assertNotIn("{{OPT_BASELINE}}", fh.read())

    def test_honesty_no_hardware_claims(self):
        # The pilot measures SIMULATED cycles only: no faster-chip or
        # frequency claim may appear in the new opt wording.
        banned = re.compile(r"faster chip|GHz|MHz|\bHz\b", re.IGNORECASE)
        with open(loop.opt_prompt_path(), encoding="utf-8") as fh:
            self.assertIsNone(banned.search(fh.read()))
        self.assertIsNone(banned.search(loop.OPT_DISCLAIMER))
        _, reason = loop.evaluate_opt_result(
            _base_verdict(cycles=1500), _base_verdict(cycles=2000))
        self.assertIsNone(banned.search(reason))

    def test_evaluate_opt_result(self):
        base = _base_verdict(cycles=2000)
        ok, reason = loop.evaluate_opt_result(_base_verdict(cycles=1500),
                                              base)
        self.assertTrue(ok)
        self.assertIn("500", reason)
        # Behavior change: checksum differs.
        ok, _ = loop.evaluate_opt_result(_base_verdict(cycles=1500,
                                                       checksum="b" * 64),
                                         base)
        self.assertFalse(ok)
        # Behavior change: tohost differs.
        ok, _ = loop.evaluate_opt_result(_base_verdict(cycles=1500,
                                                       tohost="FAIL 0x1"),
                                         base)
        self.assertFalse(ok)
        # Opt resim RED.
        ok, _ = loop.evaluate_opt_result(_base_verdict(cycles=1500,
                                                       passed=False,
                                                       tohost="FAIL 0x1"),
                                         base)
        self.assertFalse(ok)
        # No measurable simulated saving: equal cycles.
        ok, _ = loop.evaluate_opt_result(_base_verdict(cycles=2000), base)
        self.assertFalse(ok)
        # Regression: more cycles.
        ok, _ = loop.evaluate_opt_result(_base_verdict(cycles=2100), base)
        self.assertFalse(ok)
        # Unknown cycles: cannot prove a saving.
        no_cycles = _base_verdict(cycles=1500)
        del no_cycles["cycles"]
        ok, _ = loop.evaluate_opt_result(no_cycles, base)
        self.assertFalse(ok)
        base_unknown = dict(base)
        del base_unknown["cycles"]
        ok, _ = loop.evaluate_opt_result(_base_verdict(cycles=1500),
                                         base_unknown)
        self.assertFalse(ok)

    def _run_fix_opt(self, run_id, opt_verdict=None, opt_side=None,
                     guard_side=None, extra=("--goals", "fix+opt")):
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        text_side = [(dict(FIX_CALL), [])]
        if opt_side is not None:
            text_side = opt_side
        else:
            text_side.append((dict(OPT_CALL), []))
        patches = [
            mock.patch.object(loop, "run_sim_before",
                              side_effect=_fake_red_sim),
            mock.patch.object(loop, "rtl_excerpt",
                              return_value="line 1: fake excerpt"),
            mock.patch.object(loop, "call_text_with_backoff",
                              side_effect=text_side),
            mock.patch.object(loop.visual, "generate",
                              return_value=dict(IMG_OK)),
            mock.patch.object(
                loop, "try_apply_diff",
                side_effect=(guard_side or [_applied_outcome(),
                                            _applied_outcome()])),
            mock.patch.object(loop, "resim_after",
                              side_effect=_resim_pair(
                                  opt_verdict or _base_verdict(1500))),
        ]
        for p in patches:
            self.addCleanup(p.stop)
            p.start()
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id] + list(extra))
        return rc, run_dir

    def test_opt_pass_accepts(self):
        rc, run_dir = self._run_fix_opt("test-opt-accept")
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["goals"], "fix+opt")
        self.assertEqual(meta["opt_status"], "accepted")
        self.assertEqual(meta["opt_cycles_before"], 2000)
        self.assertEqual(meta["opt_cycles_after"], 1500)
        self.assertEqual(meta["opt_cycles_saved"], 500)
        # Final effective cycles ledger.
        self.assertEqual(meta["cycles_before"], 1234)
        self.assertEqual(meta["cycles_after"], 1500)
        self.assertEqual(meta["cycles_saved"], 500)
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "opt_patch.diff")))
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "rejected_opt.diff")))
        self.assertTrue(os.path.isfile(os.path.join(
            run_dir, "artifacts", "verdict_opt.json")))
        cost = _read_json(os.path.join(run_dir, "cost.json"))
        self.assertEqual(cost["cycles_before"], 2000)
        self.assertEqual(cost["cycles_after"], 1500)
        self.assertEqual(cost["cycles_saved"], 500)

    def test_opt_behavior_change_rejected(self):
        rc, run_dir = self._run_fix_opt(
            "test-opt-reject-checksum",
            opt_verdict=_base_verdict(cycles=1500, checksum="b" * 64))
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "rejected")
        self.assertIn("checksum", meta["opt_reason"])
        # GREEN fix stands: final cycles untouched, nothing saved.
        self.assertEqual(meta["cycles_after"], 2000)
        self.assertEqual(meta["cycles_saved"], 0)
        self.assertEqual(meta["opt_cycles_after"], 1500)
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "rejected_opt.diff")))
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "opt_patch.diff")))
        cost = _read_json(os.path.join(run_dir, "cost.json"))
        self.assertEqual(cost["cycles_before"], 2000)
        self.assertEqual(cost["cycles_after"], 2000)
        self.assertEqual(cost["cycles_saved"], 0)

    def test_opt_tohost_change_rejected(self):
        rc, run_dir = self._run_fix_opt(
            "test-opt-reject-tohost",
            opt_verdict=_base_verdict(cycles=1500, tohost="FAIL 0x1",
                                      passed=False))
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "rejected")
        self.assertEqual(meta["cycles_after"], 2000)
        self.assertEqual(meta["cycles_saved"], 0)
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "rejected_opt.diff")))

    def test_opt_no_saving_rejected(self):
        rc, run_dir = self._run_fix_opt(
            "test-opt-reject-nosave",
            opt_verdict=_base_verdict(cycles=2000))
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "rejected")
        self.assertIn("no SIMULATED-cycle saving", meta["opt_reason"])
        self.assertEqual(meta["cycles_saved"], 0)
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "rejected_opt.diff")))

    def test_opt_failure_never_ungreens(self):
        rc, run_dir = self._run_fix_opt(
            "test-opt-fail",
            opt_side=[(dict(FIX_CALL), []),
                      loop.LiveAPIError("mock opt flake: down")])
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "failed")
        self.assertEqual(meta["cycles_after"], 2000)
        self.assertEqual(meta["cycles_saved"], 0)
        self.assertIsNone(meta["opt_cycles_after"])
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "opt_patch.diff")))
        cost = _read_json(os.path.join(run_dir, "cost.json"))
        self.assertEqual(cost["cycles_saved"], 0)

    def test_opt_guard_reject_keeps_green(self):
        rc, run_dir = self._run_fix_opt(
            "test-opt-guard",
            guard_side=[_applied_outcome(),
                        {"guard_ok": False, "reasons": ["mock opt reject"],
                         "applied": False, "repaired": False,
                         "patch_lines": 0, "files": []}])
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "rejected")
        self.assertIn("guard", meta["opt_reason"])
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "rejected_opt.diff")))
        self.assertEqual(meta["cycles_saved"], 0)

    def _opt_envelope(self, content):
        call = dict(OPT_CALL)
        call["content"] = content
        return (call, [])

    def test_opt_live02_truncated_cleanly_rejected(self):
        # Fixture replay: opt-live-02 truncated mid-JSON output. No diff
        # block is recoverable -> clean FAILED with a recorded reason;
        # GREEN fix stands, never ungreened.
        with self.assertRaises(loop.LoopError):
            loop.parse_opt_output(OPT_LIVE_02_TRUNCATED)
        rc, run_dir = self._run_fix_opt(
            "test-opt-live02-replay",
            opt_side=[(dict(FIX_CALL), []),
                      self._opt_envelope(OPT_LIVE_02_TRUNCATED)])
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "failed")
        self.assertTrue(meta["opt_reason"])
        self.assertEqual(meta["cycles_after"], 2000)
        self.assertEqual(meta["cycles_saved"], 0)
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "opt_patch.diff")))

    def test_opt_live04_empty_cleanly_failed(self):
        # Fixture replay: opt-live-04 EMPTY model output. Nothing to
        # parse -> clean FAILED with a recorded reason; GREEN stands.
        with self.assertRaises(loop.LoopError):
            loop.parse_opt_output(OPT_LIVE_04_EMPTY)
        with self.assertRaises(loop.LoopError):
            loop.parse_opt_output("   \n  ")
        rc, run_dir = self._run_fix_opt(
            "test-opt-live04-replay",
            opt_side=[(dict(FIX_CALL), []),
                      self._opt_envelope(OPT_LIVE_04_EMPTY)])
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "failed")
        self.assertTrue(meta["opt_reason"])
        self.assertEqual(meta["cycles_saved"], 0)
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "opt_patch.diff")))

    def test_opt_fenced_prose_diff_accepted(self):
        # Tolerant parse: prose + fenced diff (not bare JSON) still flows
        # through the SAME guard + SAME equivalence gate -> accepted here
        # because resim is PASS with identical checksum/tohost, lower
        # SIMULATED cycles.
        obj, note = loop.parse_opt_output(OPT_FENCED_PROSE)
        self.assertIn("diff --git", obj["patch_diff"])
        self.assertTrue(obj["diagnosis"].strip())
        self.assertIn("tolerant", note)
        rc, run_dir = self._run_fix_opt(
            "test-opt-fenced-accept",
            opt_side=[(dict(FIX_CALL), []),
                      self._opt_envelope(OPT_FENCED_PROSE)])
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "accepted")
        self.assertEqual(meta["opt_cycles_saved"], 500)
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "opt_patch.diff")))

    def test_opt_fenced_prose_behavior_change_rejected(self):
        # Tolerance is parsing-only: the SAME fenced candidate with a
        # changed-checksum resim is REJECTED by the untouched
        # equivalence gate; GREEN fix stands.
        rc, run_dir = self._run_fix_opt(
            "test-opt-fenced-reject",
            opt_verdict=_base_verdict(cycles=1500, checksum="b" * 64),
            opt_side=[(dict(FIX_CALL), []),
                      self._opt_envelope(OPT_FENCED_PROSE)])
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "rejected")
        self.assertIn("checksum", meta["opt_reason"])
        self.assertEqual(meta["cycles_after"], 2000)
        self.assertEqual(meta["cycles_saved"], 0)
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "rejected_opt.diff")))
        self.assertFalse(os.path.exists(
            os.path.join(run_dir, "opt_patch.diff")))

    def test_opt_prose_without_diff_rejected(self):
        # Prose with no diff block is unrecoverable -> clean FAILED,
        # never an invented patch, GREEN stands.
        with self.assertRaises(loop.LoopError):
            loop.parse_opt_output(
                "The receiver wastes cycles but I propose no patch.")
        rc, run_dir = self._run_fix_opt(
            "test-opt-nodiff-replay",
            opt_side=[(dict(FIX_CALL), []),
                      self._opt_envelope("No patch proposed.")])
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["opt_status"], "failed")
        self.assertTrue(meta["opt_reason"])
        self.assertEqual(meta["cycles_saved"], 0)

    def test_opt_prompt_v4_bump(self):
        # Prompt bumped v3opt -> v4opt: old file kept, both resolve,
        # fix-prompt version tuple untouched, new prompt carries an
        # explicit output schema + miniature example and stays honest
        # (SIMULATED cycles only) and under the 2500-token cap.
        self.assertEqual(loop.OPT_PROMPT_NAME, "v4opt")
        self.assertTrue(os.path.isfile(os.path.join(
            ROOT, "agent", "prompts", "v3opt.txt")))
        self.assertTrue(os.path.isfile(loop.opt_prompt_path()))
        self.assertTrue(loop.resolve_prompt_file("v3opt").endswith(
            os.path.join("prompts", "v3opt.txt")))
        self.assertTrue(loop.resolve_prompt_file("v4opt").endswith(
            os.path.join("prompts", "v4opt.txt")))
        self.assertEqual(loop.PROMPT_VERSIONS, ("v1", "v2", "v3"))
        with open(loop.opt_prompt_path(), encoding="utf-8") as fh:
            v4 = fh.read()
        for marker in ("{{CASE}}", "{{RTL_FILE}}", "{{RTL_EXCERPT}}",
                       "{{OPT_BASELINE}}", "{{TRACE}}", "{{VERDICT}}",
                       "OUTPUT SCHEMA", "```diff"):
            self.assertIn(marker, v4)
        banned = re.compile(r"faster chip|GHz|MHz|\bHz\b", re.IGNORECASE)
        self.assertIsNone(banned.search(v4))
        trace = "".join("cycle %d: uart recv bit ok\n" % i
                        for i in range(200))
        verdict_text = json.dumps(_base_verdict())
        prompt, counts = loop.build_opt_prompt(
            CASE, "picosoc/simpleuart.v", "line 1: fake excerpt",
            trace, verdict_text, _base_verdict())
        self.assertLessEqual(counts["opt_prompt_tokens"], 2500)
        self.assertLessEqual(collect.approx_tokens(prompt), 2500)
        self.assertIn("SIMULATED", prompt)

    def test_default_fix_unchanged(self):
        run_id = "test-opt-default-fix"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        patches = [
            mock.patch.object(loop, "run_sim_before",
                              side_effect=_fake_red_sim),
            mock.patch.object(loop, "rtl_excerpt",
                              return_value="line 1: fake excerpt"),
            mock.patch.object(
                loop, "call_text_with_backoff",
                return_value=(dict(FIX_CALL), [])),
            mock.patch.object(loop.visual, "generate",
                              return_value=dict(IMG_OK)),
            mock.patch.object(loop, "try_apply_diff",
                              return_value=_applied_outcome()),
            mock.patch.object(loop, "resim_after",
                              side_effect=_resim_pair(
                                  _base_verdict(1500))),
        ]
        started = []
        for p in patches:
            self.addCleanup(p.stop)
            started.append(p.start())
        text_mock = started[2]
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id])
        self.assertEqual(rc, 0)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "green")
        self.assertEqual(meta["goals"], "fix")
        self.assertEqual(meta["opt_status"], "skipped")
        # No opt pass ran: single text call, no opt artifacts, no
        # cycles_saved ledger, fix cycles_after untouched.
        self.assertEqual(text_mock.call_count, 1)
        for name in ("opt_patch.diff", "rejected_opt.diff",
                     "artifacts/boot_opt.log", "artifacts/verdict_opt.json",
                     "api/opt_text_request.json",
                     "api/opt_text_response.json"):
            self.assertFalse(os.path.exists(os.path.join(run_dir, name)),
                             msg=name)
        self.assertNotIn("cycles_saved", meta)
        self.assertEqual(meta["cycles_after"], 2000)
        cost = _read_json(os.path.join(run_dir, "cost.json"))
        self.assertNotIn("cycles_saved", cost)


if __name__ == "__main__":
    unittest.main()
