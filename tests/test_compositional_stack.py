"""Compositional repair (stacked partial fixes) tests (TDD, red-first).

Pair-05 evidence (runs/mutpair-v7-05/, eval/pair_pilot.md): the pair needs
TWO faults fixed (picorv32.v:1408 wdata + :1450 do_waitirq); single
attempts fix at most one fault, and NEITHER single fix changes any
observable (checksum/cycles/log identical, BEFORE==AFTER in
runs/mutpair-v7-05/ no_behavioral_effect=True) -- yet composing BOTH fixes
goes GREEN with a clean fresh-tree replay. So the old checksum-delta gate
could never fire there. The honest trigger is applied-clean: pair spec +
patch applied clean (guard + dry-run pass, resim completed with a
verdict) + still RED => stack and continue from the improved tree,
whether or not the checksum moved. The replay gate + cap-2 + 3-attempt
budget already bound the cost of a wrong stack. RESET only on
guard-rejected / unparseable attempts (junk never stacks).

- TestStackPredicates: is_pair_case / should_stack_diff pure gates.
- TestStackRecord: make_stack_entry shape (incl. trigger marker),
  stack_combined_diff order, check_stack_cap refusal (cap depth 2).
- TestStackWiring: full mocked main loop on a pair spec --
  applied-clean RED skips reset_tree_for_attempt, records meta stack,
  and re-collects; guard-rejected RED still resets (old path preserved).
- TestStackReplay: fresh-tree replay verification logic -- combined
  stack applies clean to a fresh RED tree (real prepare + real apply),
  replay verdict decides GREEN-vs-loud-RED; a lying stack is RED.
- TestStackCap: third stackable RED hits the cap loudly, stack stays 2.
- TestSinglesNeverStack: single-fault run path never writes a stack
  entry (byte-identical behavior otherwise); no API in any test here.

Run: python3 tests/test_compositional_stack.py -v   (from repo root)
"""
import hashlib
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop  # noqa: E402
import vbackend  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_BOOT_RED = (
    "cycle 0: reset assert\n"
    "cycle 1: fetch pc=0x00\n"
    "TRAP: test trap\n"
    "tohost: FAIL 0x1\n"
    "CYCLES=1234\n"
)

MODEL_OK = {
    "content": json.dumps({
        "diagnosis": "first fault fixed, second fault remains in waitirq",
        "expected": "tohost: PASS",
        "patch_diff": (
            "diff --git a/picorv32.v b/picorv32.v\n"
            "--- a/picorv32.v\n"
            "+++ b/picorv32.v\n"
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


def _pair_case():
    return {
        "name": "mut-pair-05", "project": "picorv32",
        "files": ["picorv32.v"], "top": "tb_x", "tb": "x_tb.sv",
        "gate": "dual-fault", "oracle_pass": "tohost: PASS",
        "mutant_seed": 1234, "mutant_op": "pair",
        "faults": [
            {"file": "picorv32.v", "line": 10,
             "anchor": "set_mem_do_wdata = 0;",
             "bug": "set_mem_do_wdata = 1;"},
            {"file": "picorv32.v", "line": 20,
             "anchor": "do_waitirq <= 0;",
             "bug": "do_waitirq <= 1;"},
        ],
    }


def _single_mutant_case():
    return {
        "name": "mut-single", "project": "picorv32",
        "files": ["picorv32.v"], "top": "tb_x", "tb": "x_tb.sv",
        "gate": "g", "oracle_pass": "tohost: PASS",
        "mutant_file": "picorv32.v", "mutant_line": 10,
        "anchor": "set_mem_do_wdata = 0;",
        "bug": "set_mem_do_wdata = 1;",
    }


def _tiny_pristine(tmp, nlines=30):
    """Tiny picorv32.v lookalike carrying both pair fault anchors.

    Valid enough for guard.lint_rtl (balanced module/endmodule); the
    pair anchors sit at lines 10 and 20 (1-based) to match _pair_case().
    """
    lines = ["module tiny(input wire clk);",
             "  reg set_mem_do_wdata;",
             "  reg do_waitirq;"]
    while len(lines) < 9:
        lines.append("  wire pad_%d;" % len(lines))
    lines.append("set_mem_do_wdata = 0;")  # line 10
    while len(lines) < 19:
        lines.append("  wire pad_%d;" % len(lines))
    lines.append("do_waitirq <= 0;")  # line 20
    while len(lines) < nlines:
        lines.append("  wire pad_%d;" % len(lines))
    lines.append("endmodule")
    with open(os.path.join(tmp, "picorv32.v"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return tmp


def _fix1_diff():
    return (
        "diff --git a/picorv32.v b/picorv32.v\n"
        "--- a/picorv32.v\n"
        "+++ b/picorv32.v\n"
        "@@ -10,1 +10,1 @@\n"
        "-set_mem_do_wdata = 1;\n"
        "+set_mem_do_wdata = 0;\n"
    )


def _fix2_diff():
    return (
        "diff --git a/picorv32.v b/picorv32.v\n"
        "--- a/picorv32.v\n"
        "+++ b/picorv32.v\n"
        "@@ -20,1 +20,1 @@\n"
        "-do_waitirq <= 1;\n"
        "+do_waitirq <= 0;\n"
    )


class TestStackPredicates(unittest.TestCase):
    def test_pair_case_needs_two_plus_faults(self):
        self.assertTrue(loop.is_pair_case(_pair_case()))
        single = dict(_pair_case(), faults=_pair_case()["faults"][:1])
        self.assertFalse(loop.is_pair_case(single),
                         "one fault is not a pair: must never stack")
        self.assertFalse(loop.is_pair_case(_single_mutant_case()),
                         "legacy mutant_file form is single: never stack")
        self.assertFalse(loop.is_pair_case({"name": "x"}))
        self.assertFalse(loop.is_pair_case(None))

    def test_should_stack_only_on_pair_applied_clean_red(self):
        ok, why = loop.should_stack_diff(
            case=_pair_case(), applied=True,
            checksum_before="aaa", checksum_after="bbb", still_red=True)
        self.assertTrue(ok, why)
        self.assertIn("applied-clean", why.lower())

    def test_should_stack_on_identical_checksum_applied_clean(self):
        # Pair-05 real behavior: single fixes are log-identical
        # (BEFORE==AFTER in runs/mutpair-v7-05/) yet compose GREEN.
        # Applied-clean + still RED stacks whether or not the
        # checksum moved (replay gate + cap-2 + budget bound the cost).
        ok, why = loop.should_stack_diff(
            case=_pair_case(), applied=True,
            checksum_before="aaa", checksum_after="aaa", still_red=True)
        self.assertTrue(ok,
                        "applied-clean RED must stack even when "
                        "checksum is identical: %r" % (why,))
        self.assertIn("applied-clean", why.lower())

    def test_should_stack_refuses_single_even_when_changed(self):
        ok, why = loop.should_stack_diff(
            case=_single_mutant_case(), applied=True,
            checksum_before="aaa", checksum_after="bbb", still_red=True)
        self.assertFalse(ok)
        self.assertIn("single", why.lower())

    def test_should_stack_refuses_unapplied_junk(self):
        ok, why = loop.should_stack_diff(
            case=_pair_case(), applied=False,
            checksum_before="aaa", checksum_after="aaa", still_red=True)
        self.assertFalse(ok, "guard-rejected/unparseable attempts reset")
        self.assertIn("no applied", why.lower())

    def test_should_stack_refuses_green(self):
        ok, _why = loop.should_stack_diff(
            case=_pair_case(), applied=True,
            checksum_before="aaa", checksum_after="bbb", still_red=False)
        self.assertFalse(ok, "green goes to replay, not to the stack")

    def test_should_stack_refuses_missing_checksum(self):
        for before, after in ((None, "bbb"), ("aaa", None), ("", "bbb")):
            ok, _why = loop.should_stack_diff(
                case=_pair_case(), applied=True,
                checksum_before=before, checksum_after=after,
                still_red=True)
            self.assertFalse(ok, "missing checksum must never stack")


class TestStackRecord(unittest.TestCase):
    def test_entry_shape(self):
        entry = loop.make_stack_entry(
            attempt=1, patch=_fix1_diff(),
            checksum_before="aaa", checksum_after="aaa",
            direction="picorv32.v@10:replace")
        for key in ("attempt", "patch", "checksum_before",
                    "checksum_after", "direction", "trigger"):
            self.assertIn(key, entry, "stack entry needs %r" % key)
        self.assertEqual(entry["attempt"], 1)
        self.assertEqual(entry["patch"], _fix1_diff())
        self.assertEqual(entry["checksum_before"], "aaa")
        self.assertEqual(entry["checksum_after"], "aaa")
        self.assertEqual(entry["trigger"], "applied-clean",
                         "current code only emits applied-clean "
                         "(checksum-delta is a future trigger)")

    def test_combined_diff_concatenates_in_order(self):
        stack = [
            loop.make_stack_entry(attempt=1, patch=_fix1_diff(),
                                  checksum_before="aaa",
                                  checksum_after="bbb"),
            loop.make_stack_entry(attempt=2, patch=_fix2_diff(),
                                  checksum_before="bbb",
                                  checksum_after="ccc"),
        ]
        combined = loop.stack_combined_diff(stack)
        self.assertEqual(combined, _fix1_diff() + _fix2_diff())
        self.assertLess(combined.index("set_mem_do_wdata"),
                        combined.index("do_waitirq"),
                        "order matters: fixes replay in stack order")
        self.assertEqual(loop.stack_combined_diff([]), "")

    def test_cap_refuses_beyond_two(self):
        stack = [
            loop.make_stack_entry(attempt=1, patch=_fix1_diff(),
                                  checksum_before="a", checksum_after="b"),
            loop.make_stack_entry(attempt=2, patch=_fix2_diff(),
                                  checksum_before="b", checksum_after="c"),
        ]
        with self.assertRaises(loop.LoopError):
            loop.check_stack_cap(stack)
        # depth 0 and 1 are fine
        loop.check_stack_cap([])
        loop.check_stack_cap(stack[:1])


class TestStackWiring(unittest.TestCase):
    """Mocked main loop on a pair spec (no API, no Verilator)."""

    def _run_pair(self, run_id, resim_checksums, green_at=None):
        """Drive main with scripted resim checksums.

        resim_checksums: per-ATTEMPT-after checksums; BEFORE is "aaa".
        green_at: attempt index (0-based) whose resim reports pass=True.
        Returns (rc, meta, reset_calls, prompts).
        """
        return _drive_pair_loop(self, run_id, resim_checksums, green_at)

    def test_applied_clean_skips_reset_and_records_stack(self):
        rc, meta, reset_calls, prompts = self._run_pair(
            "test-stack-noreset", ["aaa", "bbb", "ccc"])
        self.assertEqual(rc, 1)  # all RED, never false green
        self.assertEqual(reset_calls, [],
                         "stacked attempts must NOT reset to RED baseline")
        stack = meta.get("stack")
        self.assertIsInstance(stack, list)
        self.assertEqual(len(stack), 2,
                         "attempts 1+2 applied-clean RED: 2 stacked diffs")
        self.assertTrue(meta.get("stacked"))
        self.assertEqual(meta.get("stack_depth"), 2)
        for ent in stack:
            self.assertEqual(ent.get("trigger"), "applied-clean",
                             "current code only emits applied-clean")
        e0 = stack[0]
        self.assertEqual(e0["attempt"], 1)
        self.assertEqual(e0["checksum_before"], "aaa")
        self.assertEqual(e0["checksum_after"], "bbb")
        self.assertIn("set_mem_do_wdata", e0["patch"])
        self.assertEqual(stack[1]["checksum_before"], "bbb")
        self.assertEqual(stack[1]["checksum_after"], "ccc")
        # attempt-2 prompt must note the already-fixed lines so the
        # model does not re-fix them (via the prior-attempts channel,
        # prompt templates untouched).
        self.assertGreaterEqual(len(prompts), 2)
        self.assertIn("PRIOR_ATTEMPTS", prompts[1])
        self.assertIn("STACKED", prompts[1])
        self.assertIn("already-fixed", prompts[1])
        # re-COLLECT: attempt 2 runs against the improved tree, so its
        # prompt must differ from attempt 1 beyond the retry note.
        self.assertNotEqual(prompts[0], prompts[1])
        # no prompt-file changes: v2 template hash stable.
        with open(os.path.join(ROOT, "agent", "prompts", "v2.txt"),
                  encoding="utf-8") as fh:
            template = fh.read()
        self.assertEqual(meta.get("prompt_version"), "v2")
        req = _read_json(os.path.join(
            ROOT, "runs", "test-stack-noreset", "api",
            "text_request.json"))
        self.assertEqual(req.get("prompt_sha256"), _sha_text(template))

    def test_identical_checksum_still_stacks(self):
        # Pair-05 ground truth: applied-clean RED with an IDENTICAL
        # checksum still stacks (checksum-delta gate could never fire).
        rc, meta, reset_calls, _prompts = self._run_pair(
            "test-stack-identical", ["aaa", "aaa", "aaa"])
        self.assertEqual(rc, 1)
        self.assertEqual(reset_calls, [],
                         "stacked attempts must NOT reset even when "
                         "checksum is identical")
        stack = meta.get("stack", [])
        self.assertEqual(len(stack), 2,
                         "attempts 1+2 applied-clean: stacked; "
                         "attempt 3 hits the cap")
        self.assertTrue(meta.get("stacked"))
        for ent in stack:
            self.assertEqual(ent.get("trigger"), "applied-clean")
        self.assertIn("cap", (meta.get("error") or "").lower(),
                      "third identical-checksum RED must hit the cap loudly")

    def test_guard_rejected_still_resets_no_junk_stacked(self):
        # Guard-rejected / unparseable attempts never stack junk: the
        # old reset path is preserved and the stack stays empty.
        run_id = "test-stack-guardreject"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        scratch = os.path.join(ROOT, "runs", "scratch", run_id)
        self.addCleanup(shutil.rmtree, scratch, True)
        prompts = []
        reset_calls = []

        def _fake_before(case, seed, boot_path, verdict_path):
            with open(boot_path, "w", encoding="utf-8") as fh:
                fh.write(FAKE_BOOT_RED)
            verdict = {"case": case, "pass": False,
                       "tohost": "FAIL 0x1", "checksum": "aaa",
                       "cycles": 1234, "seed": seed}
            with open(verdict_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(verdict) + "\n")
            return verdict

        def _fake_text(prompt, **kwargs):
            prompts.append(prompt)
            return dict(MODEL_OK), []

        def _fake_before_red(tree, case, seed, boot_path, verdict_path):
            # Mutant BEFORE also goes through resim_after: report RED.
            with open(boot_path, "w", encoding="utf-8") as fh:
                fh.write(FAKE_BOOT_RED)
            verdict = {"case": case, "pass": False,
                       "tohost": "FAIL 0x1", "checksum": "aaa",
                       "cycles": 1234, "seed": seed}
            with open(verdict_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(verdict) + "\n")
            return verdict

        def _fake_reset(case, pristine_tree, tree, baseline_sha256=None):
            reset_calls.append(dict(tree=tree))
            return {"tree_reset": True, "tree_sha256": "reset-sha",
                    "tree_reset_verified": True}

        def _fake_prepare(case, pristine_tree, dest, force=False):
            import shutil as _shutil
            if os.path.exists(dest):
                _shutil.rmtree(dest)
            os.makedirs(dest)
            with open(os.path.join(dest, "picorv32.v"), "w") as fh:
                fh.write("// wiring scratch\nset_mem_do_wdata = 1;\n"
                         "do_waitirq <= 1;\n")
            return {"file": "picorv32.v", "line": 10,
                    "before": "x", "after": "y",
                    "faults": [{"file": "picorv32.v", "line": 10},
                               {"file": "picorv32.v", "line": 20}]}

        patches = [
            mock.patch.object(loop, "run_sim_before",
                              side_effect=_fake_before),
            mock.patch.object(loop, "rtl_excerpt",
                              return_value="line 1: fake excerpt"),
            mock.patch.object(loop, "call_text_with_backoff",
                              side_effect=_fake_text),
            mock.patch.object(loop.visual, "generate",
                              return_value=dict(IMG_OK)),
            mock.patch.object(loop, "try_apply_diff",
                              return_value={"guard_ok": False,
                                            "reasons": ["junk hunk"],
                                            "applied": False,
                                            "repaired": False,
                                            "patch_lines": 0,
                                            "files": [],
                                            "applied_diff": ""}),
            mock.patch.object(loop, "resim_after",
                              side_effect=_fake_before_red),
            mock.patch.object(loop, "reset_tree_for_attempt",
                              side_effect=_fake_reset),
            mock.patch.object(loop, "prepare_mutant_tree",
                              side_effect=_fake_prepare),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(
                loop.vbackend, "load_case",
                return_value=dict(_pair_case())):
            rc = loop.main(["--case", "mut-pair-05", "--seed", "42",
                            "--run-id", run_id])
        self.assertEqual(rc, 2,
                         "all-guard-rejected: triage (patch withheld), "
                         "never GREEN, never stacked")
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("stack", []), [],
                         "guard-rejected attempts must never stack junk")
        self.assertFalse(meta.get("stacked", False))
        self.assertGreaterEqual(len(reset_calls), 1,
                                "guard-rejected RED keeps the reset path")


def _drive_pair_loop(testcase, run_id, resim_checksums, green_at=None):
    run_dir = os.path.join(ROOT, "runs", run_id)
    testcase.addCleanup(shutil.rmtree, run_dir, True)
    scratch = os.path.join(ROOT, "runs", "scratch", run_id)
    testcase.addCleanup(shutil.rmtree, scratch, True)
    prompts = []
    reset_calls = []

    def _fake_before(case, seed, boot_path, verdict_path):
        with open(boot_path, "w", encoding="utf-8") as fh:
            fh.write(FAKE_BOOT_RED)
        verdict = {"case": case, "pass": False,
                   "tohost": "FAIL 0x1", "checksum": "aaa",
                   "cycles": 1234, "seed": seed}
        with open(verdict_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(verdict) + "\n")
        return verdict

    state = {"n": 0}

    def _fake_resim(tree, case, seed, boot_path, verdict_path):
        idx = state["n"]
        state["n"] += 1
        checksum = resim_checksums[min(idx, len(resim_checksums) - 1)]
        green = (green_at is not None and idx == green_at)
        with open(boot_path, "w", encoding="utf-8") as fh:
            fh.write(FAKE_BOOT_RED.replace(
                "FAIL 0x1", "PASS" if green else "FAIL 0x1"))
        verdict = {"case": case, "pass": green,
                   "tohost": "PASS" if green else "FAIL 0x1",
                   "checksum": checksum, "cycles": 1234, "seed": seed}
        with open(verdict_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(verdict) + "\n")
        return verdict

    def _fake_text(prompt, **kwargs):
        prompts.append(prompt)
        return dict(MODEL_OK), []

    def _fake_reset(case, pristine_tree, tree, baseline_sha256=None):
        reset_calls.append(dict(tree=tree))
        return {"tree_reset": True, "tree_sha256": "reset-sha",
                "tree_reset_verified": True}

    def _fake_prepare(case, pristine_tree, dest, force=False):
        # wiring-only stand-in for prepare_mutant_tree (the real one
        # is covered by TestStackReplay + test_mutant_fire.py): stage
        # a tiny scratch tree so hashing/excerpts work for real.
        import shutil as _shutil
        if os.path.exists(dest):
            _shutil.rmtree(dest)
        os.makedirs(dest)
        with open(os.path.join(dest, "picorv32.v"), "w") as fh:
            fh.write("// wiring scratch\nset_mem_do_wdata = 1;\n"
                     "do_waitirq <= 1;\n")
        return {"file": "picorv32.v", "line": 10,
                "before": "x", "after": "y",
                "faults": [{"file": "picorv32.v", "line": 10},
                           {"file": "picorv32.v", "line": 20}]}

    patches = [
        mock.patch.object(loop, "run_sim_before",
                          side_effect=_fake_before),
        mock.patch.object(loop, "rtl_excerpt",
                          return_value="line 1: fake excerpt"),
        mock.patch.object(loop, "call_text_with_backoff",
                          side_effect=_fake_text),
        mock.patch.object(loop.visual, "generate",
                          return_value=dict(IMG_OK)),
        mock.patch.object(loop, "try_apply_diff",
                          return_value={"guard_ok": True, "reasons": [],
                                        "applied": True,
                                        "repaired": False,
                                        "patch_lines": 2,
                                        "files": ["picorv32.v"],
                                        "applied_diff": _fix1_diff()}),
        mock.patch.object(loop, "resim_after",
                          side_effect=_fake_resim),
        mock.patch.object(loop, "reset_tree_for_attempt",
                          side_effect=_fake_reset),
        mock.patch.object(loop, "prepare_mutant_tree",
                          side_effect=_fake_prepare),
    ]
    for p in patches:
        p.start()
        testcase.addCleanup(p.stop)
    with mock.patch.object(
            loop.vbackend, "load_case",
            return_value=dict(_pair_case())):
        rc = loop.main(["--case", "mut-pair-05", "--seed", "42",
                        "--run-id", run_id])
    meta = _read_json(os.path.join(run_dir, "meta.json"))
    return rc, meta, reset_calls, prompts

class TestStackReplay(unittest.TestCase):
    def test_combined_stack_applies_clean_to_fresh_red_tree(self):
        tmp = os.path.join(ROOT, "runs", "scratch",
                            "test-stack-replay-tmp")
        self.addCleanup(shutil.rmtree, tmp, True)
        pristine = os.path.join(tmp, "pristine")
        os.makedirs(pristine)
        _tiny_pristine(pristine)
        red = os.path.join(tmp, "red")
        loop.prepare_mutant_tree(_pair_case(), pristine, red)
        out1 = loop.try_apply_diff(_fix1_diff(), red,
                                   os.path.join(tmp, "fix1.diff"),
                                   spec=None)
        self.assertTrue(out1["applied"],
                        "fix-1 must pass guard+dry-run+apply: %r"
                        % (out1.get("reasons"),))
        stack = [loop.make_stack_entry(
            attempt=1, patch=out1["applied_diff"],
            checksum_before="aaa", checksum_after="bbb")]
        out2 = loop.try_apply_diff(_fix2_diff(), red,
                                   os.path.join(tmp, "fix2.diff"),
                                   spec=None)
        self.assertTrue(out2["applied"])
        stack.append(loop.make_stack_entry(
            attempt=2, patch=out2["applied_diff"],
            checksum_before="bbb", checksum_after="ccc"))
        # fresh-tree replay: fresh RED + combined stack == fully fixed.
        fresh = os.path.join(tmp, "fresh")
        result = loop.verify_stack_replay(
            stack=stack, case=_pair_case(), pristine_tree=pristine,
            replay_tree=fresh, seed=42,
            tmp_path=os.path.join(tmp, "stack.diff"),
            expected_checksum="ccc",
            resim_fn=lambda *a, **k: {"pass": True, "tohost": "PASS",
                                      "checksum": "ccc", "cycles": 1,
                                      "seed": 42, "case": "mut-pair-05"})
        self.assertTrue(result["ok"], result)
        with open(os.path.join(fresh, "picorv32.v")) as fh:
            fixed = fh.read()
        with open(os.path.join(pristine, "picorv32.v")) as fh:
            want = fh.read()
        self.assertEqual(fixed, want,
                         "both faults fixed: fresh tree == pristine")

    def test_lying_stack_is_loud_red(self):
        tmp = os.path.join(ROOT, "runs", "scratch",
                            "test-stack-replay-lie-tmp")
        self.addCleanup(shutil.rmtree, tmp, True)
        pristine = os.path.join(tmp, "pristine")
        os.makedirs(pristine)
        _tiny_pristine(pristine)
        stack = [loop.make_stack_entry(
            attempt=1, patch=_fix1_diff(),
            checksum_before="aaa", checksum_after="bbb")]
        # replay resim says RED: the stack lied -> ok=False, loud reason.
        result = loop.verify_stack_replay(
            stack=stack, case=_pair_case(), pristine_tree=pristine,
            replay_tree=os.path.join(tmp, "fresh"), seed=42,
            tmp_path=os.path.join(tmp, "stack.diff"),
            expected_checksum="zzz-never",
            resim_fn=lambda *a, **k: {"pass": False,
                                      "tohost": "FAIL 0x1",
                                      "checksum": "qqq", "cycles": 1,
                                      "seed": 42, "case": "mut-pair-05"})
        self.assertFalse(result["ok"])
        self.assertIn("replay", result["reason"].lower())

    def test_green_with_stack_replays_or_goes_red(self):
        # end-to-end through main: stack depth 1, then GREEN. The
        # fresh-tree replay must verify the FULL stacked diff; a lying
        # stack turns the verdict RED (loud, not triage).
        run_id = "test-stack-green-replay"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        replay = {"calls": 0}

        def _fake_replay(**kwargs):
            replay["calls"] += 1
            return {"ok": True, "reason": "replay GREEN, checksum match",
                    "verdict": {"pass": True}}

        with mock.patch.object(loop, "verify_stack_replay",
                               side_effect=_fake_replay):
            rc, meta, reset_calls, _prompts = _drive_pair_loop(
                self, run_id, ["aaa", "bbb", "ccc"], green_at=2)
        self.assertEqual(replay["calls"], 1,
                         "GREEN with a non-empty stack must replay")
        self.assertEqual(reset_calls, [])
        self.assertTrue(meta.get("stacked"))
        self.assertTrue(meta.get("replay_verified"),
                        "honest GREEN records the replay proof")

    def test_green_with_lying_stack_goes_red(self):
        run_id = "test-stack-green-lie"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)

        def _fake_replay(**kwargs):
            return {"ok": False,
                    "reason": "replay RED on fresh tree: the stack lied",
                    "verdict": {"pass": False}}

        with mock.patch.object(loop, "verify_stack_replay",
                               side_effect=_fake_replay):
            rc, meta, _reset_calls, _prompts = _drive_pair_loop(
                self, run_id, ["aaa", "bbb", "ccc"], green_at=2)
        self.assertEqual(rc, 1, "lying stack must not stay GREEN")
        self.assertIn("stack", (meta.get("error") or "").lower())


class TestStackCap(unittest.TestCase):
    def test_third_stackable_red_refuses_loudly(self):
        rc, meta, _reset_calls, _prompts = _drive_pair_loop(
            self, "test-stack-cap", ["bbb", "ccc", "ddd"])
        # attempts 1+2 stack (depth 2); attempt 3 is another
        # applied-clean RED -> cap refuses loudly, run RED.
        self.assertEqual(rc, 1)
        self.assertEqual(len(meta.get("stack", [])), 2,
                         "cap holds: stack never grows beyond 2")
        self.assertIn("cap", (meta.get("error") or "").lower(),
                      "cap refusal must be loud in meta.error, got: %r"
                      % (meta.get("error"),))


class TestSinglesNeverStack(unittest.TestCase):
    def test_single_fault_run_writes_no_stack_entry(self):
        run_id = "test-single-nostack"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)

        def _fake_red_sim(case, seed, boot_path, verdict_path):
            with open(boot_path, "w", encoding="utf-8") as fh:
                fh.write(FAKE_BOOT_RED)
            with open(verdict_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"case": case, "pass": False,
                                     "tohost": "FAIL 0x1",
                                     "checksum": "deadbeef", "cycles": 1234,
                                     "seed": seed}) + "\n")
            return 1, "mock RED"

        def _fake_changed_resim(tree, case, seed, boot_path, verdict_path):
            with open(boot_path, "w", encoding="utf-8") as fh:
                fh.write(FAKE_BOOT_RED)
            verdict = {"case": case, "pass": False,
                       "tohost": "FAIL 0x2",  # behavior CHANGED anyway
                       "checksum": "changed!", "cycles": 1234, "seed": seed}
            with open(verdict_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(verdict) + "\n")
            return verdict

        patches = [
            mock.patch.object(loop, "run_sim_before",
                              side_effect=_fake_red_sim),
            mock.patch.object(loop, "rtl_excerpt",
                              return_value="line 1: fake excerpt"),
            mock.patch.object(loop, "call_text_with_backoff",
                              return_value=(dict(MODEL_OK), [])),
            mock.patch.object(loop.visual, "generate",
                              return_value=dict(IMG_OK)),
            mock.patch.object(loop, "try_apply_diff",
                              return_value={"guard_ok": True, "reasons": [],
                                            "applied": True,
                                            "repaired": False,
                                            "patch_lines": 2,
                                            "files": ["picosoc/simpleuart.v"],
                                            "applied_diff": "diff text\n"}),
            mock.patch.object(loop, "resim_after",
                              side_effect=_fake_changed_resim),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        rc = loop.main(["--case", "uart-start-half", "--seed", "42",
                        "--run-id", run_id])
        self.assertEqual(rc, 1)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("stack", []), [],
                         "single-fault path must never write a stack entry")
        self.assertFalse(meta.get("stacked", False))




class TestRealToolchainJoint(unittest.TestCase):
    """Real-toolchain joint test (CPU-only, no API).

    Pair-05 ground truth on the real Verilator backend: NEITHER single
    fix changes the checksum (both resims are log-identical to the RED
    baseline -- replicated here, matching runs/mutpair-v7-05/ where
    BEFORE == AFTER == 563b.. and no_behavioral_effect=True). Yet the
    joint test proves both-fixes-composed goes GREEN with a clean
    fresh-tree replay. So the honest trigger is applied-clean: the
    first applied-clean RED stacks (keep the improved tree) even
    though the checksum is identical; stacking the second fix then
    goes GREEN, and the FULL stacked diff replays GREEN on a fresh RED
    tree with the REAL resim -- the complete compositional end-state,
    proven without mocks.
    """

    def _fix_hunk(self, tree_lines, lineno, old, new):
        idx = next(i for i, l in enumerate(tree_lines)
                   if l.strip() == old)
        self.assertEqual(idx + 1, lineno)
        n = 4
        body = ([" " + l for l in tree_lines[idx - 1:idx]]
                + ["-" + tree_lines[idx],
                   "+" + tree_lines[idx].replace(old, new)]
                + [" " + l for l in tree_lines[idx + 1:idx + 2]])
        return ("@@ -%d,%d +%d,%d @@\n" % (idx, n, idx, n)
                + "\n".join(body) + "\n")

    def test_real_single_fix_stacks_on_identical_checksum_and_composes_green(self):
        case = vbackend.load_case("mut-pair-05")
        pins = vbackend.load_pins()
        _url, sha = pins[case["project"]]
        pristine = vbackend.tree_dir(case["project"], sha)
        self.assertTrue(os.path.isdir(pristine),
                        "needs the pinned pristine tree")
        scratch = os.path.join(ROOT, "runs", "scratch",
                               "test-stack-joint")
        self.addCleanup(shutil.rmtree, scratch, True)
        spec = loop.case_to_spec(case)
        rec = loop.prepare_mutant_tree(case, pristine, scratch)
        self.assertEqual(len(rec["faults"]), 2)
        base = loop.resim_after(
            scratch, case, 42,
            os.path.join(scratch, "boot_base.log"),
            os.path.join(scratch, "verdict_base.json"))
        self.assertFalse(base["pass"])
        self.assertTrue(base["checksum"])
        with open(os.path.join(scratch, "picorv32.v")) as fh:
            tree_lines = fh.read().splitlines()
        fix1 = self._fix_hunk(tree_lines, 1408,
                              "set_mem_do_wdata = 1;",
                              "set_mem_do_wdata = 0;")
        header = ("diff --git a/picorv32.v b/picorv32.v\n"
                  "--- a/picorv32.v\n"
                  "+++ b/picorv32.v\n")
        out1 = loop.try_apply_diff(
            header + fix1, scratch,
            os.path.join(scratch, "fix1.diff"), spec)
        self.assertTrue(out1["applied"],
                        "fix-1 must pass guard+dry-run+apply: %r"
                        % (out1.get("reasons"),))
        mid = loop.resim_after(
            scratch, case, 42,
            os.path.join(scratch, "boot_mid.log"),
            os.path.join(scratch, "verdict_mid.json"))
        self.assertFalse(mid["pass"], "one fault left: still RED")
        self.assertEqual(mid["checksum"], base["checksum"],
                         "real pair-05: single fix is log-identical "
                         "(matches mutpair-v7-05 BEFORE==AFTER)")
        ok, why = loop.should_stack_diff(
            case=case, applied=True,
            checksum_before=base["checksum"],
            checksum_after=mid["checksum"], still_red=True)
        self.assertTrue(ok,
                        "applied-clean RED must stack even though the "
                        "checksum is identical (05 real behavior): %r"
                        % (why,))
        self.assertIn("applied-clean", why.lower())
        # Stack the first fix and continue from the improved tree: the
        # second fix on top goes GREEN.
        entry1 = loop.make_stack_entry(
            attempt=1, patch=out1["applied_diff"],
            checksum_before=base["checksum"],
            checksum_after=mid["checksum"])
        self.assertEqual(entry1["trigger"], "applied-clean")
        with open(os.path.join(scratch, "picorv32.v")) as fh:
            tree_lines2 = fh.read().splitlines()
        fix2 = self._fix_hunk(tree_lines2, 1450,
                              "do_waitirq <= 1;",
                              "do_waitirq <= 0;")
        out2 = loop.try_apply_diff(
            header + fix2, scratch,
            os.path.join(scratch, "fix2.diff"), spec)
        self.assertTrue(out2["applied"])
        entry2 = loop.make_stack_entry(
            attempt=2, patch=out2["applied_diff"],
            checksum_before=mid["checksum"],
            checksum_after="pending-green")
        self.assertEqual(entry2["trigger"], "applied-clean")
        stack = [entry1, entry2]
        green = loop.resim_after(
            scratch, case, 42,
            os.path.join(scratch, "boot_green.log"),
            os.path.join(scratch, "verdict_green.json"))
        self.assertTrue(green["pass"], "both faults fixed: GREEN")
        entry2["checksum_after"] = green["checksum"]
        # Fresh-tree replay with the REAL resim: FULL stacked diff on a
        # fresh RED tree must go GREEN with the same checksum.
        replay = loop.verify_stack_replay(
            stack=stack, case=case, pristine_tree=pristine,
            replay_tree=os.path.join(ROOT, "runs", "scratch",
                                     "test-stack-joint-replay"),
            seed=42,
            tmp_path=os.path.join(scratch, "stack.diff"),
            expected_checksum=green["checksum"], spec=spec)
        self.addCleanup(shutil.rmtree,
                        os.path.join(ROOT, "runs", "scratch",
                                     "test-stack-joint-replay"), True)
        self.assertTrue(replay["ok"], replay.get("reason"))
        self.assertEqual(replay["verdict"]["checksum"],
                         green["checksum"])


if __name__ == "__main__":
    unittest.main()
