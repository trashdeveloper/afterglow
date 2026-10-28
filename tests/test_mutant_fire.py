"""Mutant live-fire tests (TDD, fixtures + mocks only).

Enables the loop to fire the mutation-pilot CaseSpecs WITHOUT weakening
the in-tree-only SPEC rule and WITHOUT trusting /tmp trees:

- Sanctioned sim/targets/mut-pilot-*.yaml family: in-tree stems, so the
  existing stem check passes unchanged; each spec pins its single-point
  fault (mutant_file/mutant_line + anchor/bug + seed/op).
- Firing path (option c): at run start the loop copies the PINNED
  pristine tree into runs/scratch/<run-id>, applies anchor->bug at the
  PINNED LINE ONLY, verifies the single-line diff, then REDs via the
  same build+run+verdict schema as resim_after. The pristine tree is
  never written; /tmp trees are never read (provenance only).

Why line-targeted, not line-anchored inject (documented): 2/5 pilot
anchors occur twice in pristine simpleuart.v
(mut-pilot-02 "recv_state <= 0;" lines 68+93; mut-pilot-05
"send_pattern <= ~0;" lines 114+120). sim/sim.py inject replaces EVERY
matching line, so firing via inject would build DOUBLE faults for those
two -- wrong fault distribution for big-N fix-rate stats. The sim.py
guard below makes that misuse loud instead of silent.

No model calls, no network, no real Verilator builds: pristine/mutant
trees are tiny fixtures, build/run are mocked, and oracle RED/GREEN is
proven with the REAL recorded pilot logs
(eval/mutant_pilot/logs/*.log, /tmp/mutpilot/base-boot.log).

Run: python3 tests/test_mutant_fire.py -v   (from repo root)
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop  # noqa: E402  (adds sim/ to sys.path)
import vbackend  # noqa: E402
from afterglow_core.types import decide_verdict  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = os.path.join(ROOT, "sim", "targets")
PILOT = os.path.join(ROOT, "eval", "mutant_pilot")

MUTANTS = ("mut-pilot-01", "mut-pilot-02", "mut-pilot-03",
           "mut-pilot-04", "mut-pilot-05")
# (stem, pilot tree, fault line, anchor-before, bug-after) from manifest.json
PILOT_TRUTH = {
    "mut-pilot-01": ("cand-02", 78, "0: begin", "1: begin"),
    "mut-pilot-02": ("cand-04", 68, "recv_state <= 0;",
                      "recv_state <= 1;"),
    "mut-pilot-03": ("cand-05", 59,
                      "if (reg_div_we[0]) cfg_divider[ 7: 0] "
                      "<= reg_div_di[ 7: 0];",
                      "if (reg_div_we[0]) cfg_divider[ 7: 1] "
                      "<= reg_div_di[ 7: 0];"),
    "mut-pilot-04": ("cand-11", 42, "reg [7:0] recv_buf_data;",
                      "reg [7:1] recv_buf_data;"),
    "mut-pilot-05": ("cand-15", 120, "send_pattern <= ~0;",
                      "send_pattern <= ~1;"),
}


def _tiny_pristine(tmp):
    """Two-line-duplicate fixture: anchor text occurs twice on purpose."""
    src = os.path.join(tmp, "pristine")
    os.makedirs(os.path.join(src, "picosoc"))
    lines = [
        "module simpleuart(input clk);",
        "reg [31:0] recv_divcnt;",
        "if (recv_divcnt > cfg_divider) begin",
        "recv_state <= 0;",          # line 4: pinned fault site
        "recv_divcnt <= recv_divcnt + 1;",
        "send_pattern <= ~0;",       # line 6
        "recv_state <= 0;",          # line 7: SAME text, must stay pristine
        "send_pattern <= ~0;",       # line 8: SAME text, must stay pristine
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


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class TestSanctionedSpecs(unittest.TestCase):
    def test_all_five_in_tree_specs_exist_and_load(self):
        for stem in MUTANTS:
            path = os.path.join(TARGETS, stem + ".yaml")
            self.assertTrue(os.path.isfile(path), path)
            case = vbackend.load_case(stem)
            for req in ("project", "files", "top", "tb", "anchor",
                        "bug", "gate", "oracle_pass"):
                self.assertIn(req, case, "%s:%s" % (stem, req))
            self.assertTrue(str(case["oracle_pass"]).strip(),
                            "%s blank oracle" % stem)

    def test_specs_pin_single_point_fault(self):
        for stem in MUTANTS:
            case = vbackend.load_case(stem)
            _cand, line, anchor, bug = PILOT_TRUTH[stem]
            self.assertEqual(case["mutant_file"], "picosoc/simpleuart.v")
            self.assertEqual(int(case["mutant_line"]), line)
            self.assertEqual(case["anchor"].strip(), anchor)
            self.assertEqual(case["bug"].strip(), bug)
            self.assertTrue(loop.is_mutant_case(case), stem)
            # eval pilot spec agrees on the fault (same red, same green)
            eval_case = loop.load_spec_case(
                os.path.join(PILOT, stem + ".yaml"))
            self.assertEqual(eval_case["anchor"].strip(), anchor)
            self.assertEqual(eval_case["bug"].strip(), bug)
            self.assertEqual(eval_case["oracle_pass"],
                             case["oracle_pass"])

    def test_non_mutant_cases_unaffected(self):
        case = vbackend.load_case("picosoc-uart-div")
        self.assertFalse(loop.is_mutant_case(case))


class TestPrepareMutantTree(unittest.TestCase):
    def test_prepare_applies_pinned_line_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            before_hash = _sha(os.path.join(
                src, "picosoc", "simpleuart.v"))
            dest = os.path.join(tmp, "scratch", "run-1")
            record = loop.prepare_mutant_tree(_tiny_case(), src, dest)
            self.assertEqual(record["line"], 4)
            with open(os.path.join(
                    dest, "picosoc", "simpleuart.v")) as fh:
                got = fh.read().splitlines()
            self.assertEqual(got[3], "recv_state <= 1;")  # pinned line
            self.assertEqual(got[6], "recv_state <= 0;")  # dup untouched
            self.assertEqual(got[5], "send_pattern <= ~0;")
            self.assertEqual(got[7], "send_pattern <= ~0;")
            # pristine tree byte-identical: no cross-contamination
            self.assertEqual(_sha(os.path.join(
                src, "picosoc", "simpleuart.v")), before_hash)

    def test_prepare_verifies_single_line_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            dest = os.path.join(tmp, "scratch", "run-1")
            record = loop.prepare_mutant_tree(_tiny_case(), src, dest)
            self.assertEqual(record["before"], "recv_state <= 0;")
            self.assertEqual(record["after"], "recv_state <= 1;")
            self.assertEqual(record["file"], "picosoc/simpleuart.v")

    def test_prepare_refuses_anchor_mismatch_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            dest = os.path.join(tmp, "scratch", "run-1")
            bad = _tiny_case(line=5)  # line 5 is not the anchor
            with self.assertRaises(loop.LoopError):
                loop.prepare_mutant_tree(bad, src, dest)

    def test_prepare_refuses_existing_dest_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            dest = os.path.join(tmp, "scratch", "run-1")
            loop.prepare_mutant_tree(_tiny_case(), src, dest)
            with self.assertRaises(loop.LoopError):
                loop.prepare_mutant_tree(_tiny_case(), src, dest)
            # force rebuild is deterministic: identical bytes
            h1 = _sha(os.path.join(dest, "picosoc", "simpleuart.v"))
            loop.prepare_mutant_tree(_tiny_case(), src, dest, force=True)
            h2 = _sha(os.path.join(dest, "picosoc", "simpleuart.v"))
            self.assertEqual(h1, h2)

    def test_prepare_rebuild_matches_historical_mutant_tree(self):
        # Spec-driven rebuild == the pilot's seed-42 tree, byte for byte.
        # /tmp is ephemeral: skip (don't fail) when the pilot trees are
        # absent; FIRING.md documents regeneration from the spec instead.
        base = "/tmp/mutpilot/base"
        if not os.path.isdir(base):
            self.skipTest("/tmp/mutpilot/base absent (ephemeral)")
        for stem in MUTANTS:
            cand, _line, _a, _b = PILOT_TRUTH[stem]
            hist = os.path.join("/tmp/mutpilot", cand,
                                "picosoc", "simpleuart.v")
            if not os.path.isfile(hist):
                self.skipTest("/tmp/mutpilot/%s absent" % cand)
        pristine_hash = _sha(os.path.join(
            base, "picosoc", "simpleuart.v"))
        with tempfile.TemporaryDirectory() as tmp:
            for stem in MUTANTS:
                cand, _line, _a, _b = PILOT_TRUTH[stem]
                case = vbackend.load_case(stem)
                dest = os.path.join(tmp, stem)
                loop.prepare_mutant_tree(case, base, dest)
                with open(os.path.join(
                        dest, "picosoc", "simpleuart.v"), "rb") as fh:
                    rebuilt = fh.read()
                with open(os.path.join(
                        "/tmp/mutpilot", cand, "picosoc",
                        "simpleuart.v"), "rb") as fh:
                    historical = fh.read()
                self.assertEqual(rebuilt, historical,
                                 "%s rebuild != %s" % (stem, cand))
        # pristine base untouched by all five rebuilds
        self.assertEqual(pristine_hash, _sha(os.path.join(
            base, "picosoc", "simpleuart.v")))


class TestCanonicalRestorePinned(unittest.TestCase):
    def _dup_bug_tree(self, tmp):
        # Mutant state for a mut-pilot-01-like fault: the bug text
        # legitimately occurs on line 4 (pristine) AND on pinned line 2.
        os.makedirs(os.path.join(tmp, "tree"))
        with open(os.path.join(tmp, "tree", "f.v"), "w") as fh:
            fh.write("case (sel)\n"
                     "1: begin\n"
                     "other\n"
                     "1: begin\n"
                     "endcase\n")
        return os.path.join(tmp, "tree")

    def _mut_case(self):
        return {"name": "m", "project": "p", "files": ["f.v"],
                "anchor": "0: begin", "bug": "1: begin",
                "mutant_file": "f.v", "mutant_line": 2}

    def test_pinned_restore_hits_only_pinned_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._dup_bug_tree(tmp)
            diff, hits = loop.canonical_diff(tree, self._mut_case())
        self.assertEqual(hits, 1)
        # exactly one -/+ pair, restoring line 2 only
        self.assertEqual(diff.count("-1: begin"), 1)
        self.assertEqual(diff.count("+0: begin"), 1)

    def test_legacy_anchored_behavior_kept_without_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = self._dup_bug_tree(tmp)
            case = {"name": "m", "project": "p", "files": ["f.v"],
                    "anchor": "0: begin", "bug": "1: begin"}
            self.assertFalse(loop.is_mutant_case(case))
            _diff, hits = loop.canonical_diff(tree, case)
        self.assertEqual(hits, 2)


class TestDualFaultPairs(unittest.TestCase):
    """Two pinned faults in one spec: prepare injects both, canonical
    restores both, legacy single-fault path untouched. Red-first: the
    loop only knows mutant_file/mutant_line (singular) today."""

    def _pair_case(self):
        return {
            "name": "mut-pair-tiny", "project": "picorv32",
            "files": ["picosoc/simpleuart.v"],
            "top": "tb_x", "tb": "x_tb.sv",
            "gate": "t", "oracle_pass": "tohost: PASS",
            "faults": [
                {"file": "picosoc/simpleuart.v", "line": 4,
                 "anchor": "recv_state <= 0;",
                 "bug": "recv_state <= 1;"},
                {"file": "picosoc/simpleuart.v", "line": 6,
                 "anchor": "send_pattern <= ~0;",
                 "bug": "send_pattern <= ~1;"},
            ],
        }

    def test_pair_case_detected_as_mutant(self):
        self.assertTrue(loop.is_mutant_case(self._pair_case()))

    def test_prepare_applies_both_faults(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            dest = os.path.join(tmp, "scratch", "run-pair")
            record = loop.prepare_mutant_tree(
                self._pair_case(), src, dest)
            with open(os.path.join(
                    dest, "picosoc", "simpleuart.v")) as fh:
                got = fh.read().splitlines()
            self.assertEqual(got[3], "recv_state <= 1;")
            self.assertEqual(got[5], "send_pattern <= ~1;")
            self.assertEqual(got[6], "recv_state <= 0;")  # dup pristine
            self.assertEqual(got[7], "send_pattern <= ~0;")
            self.assertEqual(len(record["faults"]), 2)

    def test_prepare_rejects_second_fault_mismatch_loudly(self):
        bad = self._pair_case()
        bad["faults"][1]["line"] = 7  # line 7 is not the anchor
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            with self.assertRaises(loop.LoopError):
                loop.prepare_mutant_tree(
                    bad, src, os.path.join(tmp, "scratch", "run-bad"))

    def test_canonical_restores_both_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_pristine(tmp)
            dest = os.path.join(tmp, "scratch", "run-pair")
            loop.prepare_mutant_tree(self._pair_case(), src, dest)
            diff, hits = loop.canonical_diff(dest, self._pair_case())
            self.assertEqual(hits, 2)
            self.assertIn("-recv_state <= 1;", diff)
            self.assertIn("+recv_state <= 0;", diff)
            self.assertIn("-send_pattern <= ~1;", diff)
            self.assertIn("+send_pattern <= ~0;", diff)


class TestOracleRedGreen(unittest.TestCase):
    def _log(self, name):
        with open(os.path.join(PILOT, "logs", name)) as fh:
            return fh.read()

    def test_all_five_pilot_logs_are_red(self):
        for stem in MUTANTS:
            log = self._log(stem + "-boot.log")
            verdict = decide_verdict(log, "tohost: PASS")
            self.assertIs(getattr(verdict, "pass"), False, stem)
            self.assertIn("tohost: FAIL", log, stem)

    def test_pristine_control_is_green(self):
        with open("/tmp/mutpilot/base-boot.log") as fh:
            log = fh.read()
        verdict = decide_verdict(log, "tohost: PASS")
        self.assertIs(getattr(verdict, "pass"), True)
        self.assertIn("tohost: PASS", log)


class TestMutantRedPath(unittest.TestCase):
    def test_sim_refuses_mutant_stem_loudly(self):
        # sim.py inject is line-anchored (replaces EVERY match): it must
        # refuse mutant stems instead of silently building double faults.
        with self.assertRaises(RuntimeError) as ctx:
            vbackend.run_case("mut-pilot-02", seed=42,
                              boot="/tmp/nope.log",
                              verdict="/tmp/nope.json")
        self.assertIn("loop", str(ctx.exception).lower())

    def test_red_verdict_plumbing_red_and_green(self):
        # resim_after (reused for mutant RED) judges by the oracle marker
        # only; mocked build/run, no verilator.
        with tempfile.TemporaryDirectory() as tmp:
            case = _tiny_case()
            with mock.patch.object(
                    vbackend, "build", return_value="/tmp/fake_bin"):
                with mock.patch.object(
                        vbackend, "sh",
                        return_value=(0, "tohost: FAIL 0xdead\nTRAP: x\n")):
                    verdict = loop.resim_after(
                        tmp, case, 42, os.path.join(tmp, "boot.log"),
                        os.path.join(tmp, "v.json"))
            self.assertIs(verdict["pass"], False)
            with open(os.path.join(tmp, "v.json")) as fh:
                disk = json.load(fh)
            self.assertIs(disk["pass"], False)
            self.assertEqual(disk["tohost"], "FAIL 0xdead")
            with mock.patch.object(
                    vbackend, "build", return_value="/tmp/fake_bin"):
                with mock.patch.object(
                        vbackend, "sh",
                        return_value=(0, "tohost: PASS\nCYCLES=2652\n")):
                    verdict = loop.resim_after(
                        tmp, case, 42, os.path.join(tmp, "boot.log"),
                        os.path.join(tmp, "v.json"))
            self.assertIs(verdict["pass"], True)
            self.assertEqual(verdict["cycles"], 2652)


if __name__ == "__main__":
    unittest.main()
