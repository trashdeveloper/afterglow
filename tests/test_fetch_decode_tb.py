"""Fetch-decode TB hardening tests (wave-5: kill-rate recommendation).

Implements the ``eval/tb_kill_rate.md`` RECOMMENDATION exactly: ONE
decoder-exercising scenario in a NEW testbench
(``sim/tb/picorv32-fetch-decode_tb.sv``) -- a single ALU-immediate
stream (ADDI x5,x0,123 / ADDI x6,x5,1, nonzero immediates) in the
boot-ROM plus an assertion on the retired register values -- targeting
the ``num_bump`` escape class (1211 of this TB's 2176 equivalents) that
the NOP-heavy boot never touches (every NOP writes x0, so any
writeback-path fault is invisible to the old oracle).

The original ``sim/tb/picorv32-fetch_tb.sv`` MUST stay byte-identical
(new file, new oracle, old oracle preserved for comparison).

STATIC + LOCAL-SIM ONLY: real Verilator builds/runs on scratch dirs
holding a COPY of the pinned picorv32.v (never the shared work-cache
tree, never the repo). No API, no model, no network, no writes to
``eval/``, ``runs/``, ``board.json``, or any bundle.

- test_original_tb_untouched: old TB hash-pinned + NOP-only.
- test_new_tb_compiles_lint_clean: verilator --lint-only, zero errors.
- test_red_on_decode_mutant: the recorded equivalent num_bump
  decode mutant (candidate 301 / pool 1152: the live main-decoder
  ``decoded_rd`` slice ``[11:7]`` -> ``[11:6]``, old-oracle PASS per
  board.json) goes RED under the new TB (TRAP: decode ...), fired
  through the loop's own pinned-line path. This is the blind-spot
  movement proof: old TB GREEN (static, from board oracle_lines) vs
  new TB RED (live local sim). (No kept num_bump decode mutant could
  show movement: the 8 kept num_bump are all already old-TB-RED
  control-path faults.)
- test_green_pristine_with_golden: pristine tree goes GREEN; the
  pass-path log is display-identical to the old TB, so the checksum
  MUST equal the known fetch golden
  5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a
  (CYCLES=2009). A different measured value would mean the scenario
  changed the deterministic log and must be documented as a new golden
  -- never edited to match.
- test_spec_wiring: ``sim/targets/picorv32-fetch-decode.yaml`` loads
  via the REAL ``afterglow_core.verilator`` mapping (the same loader
  ``afterglow run --spec`` uses) with top/tb/oracle matching the new
  TB; stem is in-tree so the loop's RED dispatch
  (``sim/sim.py --case <stem>``) resolves; the 8-case eval board is
  untouched (stem NOT in ``eval/cases.yaml``).
- test_kill_rate_projection_readonly: re-runs the
  ``eval.tb_kill_rate`` aggregation WITHOUT writing anything
  (``_parent_rows``, not ``generate``) to confirm the old numbers, then
  statically classifies which fetch-parent equivalents sit in paths the
  new oracle observes. A static projection, NOT a measured kill rate:
  the live measurement that would produce real numbers is firing every
  equivalent mutant tree under the new TB and counting RED.

Run: python3 tests/test_fetch_decode_tb.py -v   (from repo root)
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sim"))

import vbackend  # noqa: E402
from afterglow_core import verilator as vcore  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TB_OLD = os.path.join(ROOT, "sim", "tb", "picorv32-fetch_tb.sv")
TB_NEW = os.path.join(ROOT, "sim", "tb", "picorv32-fetch-decode_tb.sv")
SPEC_NEW = os.path.join(ROOT, "sim", "targets",
                        "picorv32-fetch-decode.yaml")
CASES_YAML = os.path.join(ROOT, "eval", "cases.yaml")
BOARD = os.path.join(ROOT, "eval", "mutant_pilot", "board.json")

# sha256 of sim/tb/picorv32-fetch_tb.sv at HEAD 617f227 (tree clean;
# `git diff HEAD` empty). Pins "leave the original TB byte-identical".
ORIGINAL_TB_SHA256 = (
    "60bff8d4b2b402024742252a8f9fbcff7fd0b4a75cb2e796df41e22eacd5cee3")

FETCH_GOLDEN = ("5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a")

# Recorded equivalent num_bump decode mutant, old-TB-blind by
# construction: candidate 301 / pool 1152 widens the rd slice on the
# LIVE main-decoder line, so every nonzero-rd instruction retires to
# the wrong register; NOPs (rd x0) still decode to x0 and pass.
DECODE_MUTANT = {
    "before": "\t\t\tdecoded_rd <= mem_rdata_latched[11:7];",
    "after": "\t\t\tdecoded_rd <= mem_rdata_latched[11:6];",
    "line": 882,
}

VERILATOR = shutil.which("verilator")
PINNED_TREE = os.path.expanduser(
    "~/.cache/afterglow/work/picorv32-ef203c2b0a3f")
PINNED_RTL = os.path.join(PINNED_TREE, "picorv32.v")

NEEDS_VERILATOR = unittest.skipUnless(
    VERILATOR, "verilator not on PATH (local-sim proof needs it)")
NEEDS_TREE = unittest.skipUnless(
    os.path.isfile(PINNED_RTL),
    "pinned picorv32 tree absent (no network fetch in tests)")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _scratch_tree(mutant=None):
    """Scratch dir holding ONLY picorv32.v (pristine or 1-line mutant).

    Minimal on purpose: vbackend.build resolves case files relative to
    the tree dir, so a lone RTL copy is a complete build tree. The
    shared pinned work-cache tree is never written.
    """
    tmp = tempfile.mkdtemp(prefix="fetch-decode-")
    with open(PINNED_RTL, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    if mutant is not None:
        idx = mutant["line"] - 1
        got = lines[idx].rstrip("\n")
        assert got == mutant["before"], (
            "mutant anchor drift: line %d is %r, want %r"
            % (mutant["line"], got, mutant["before"]))
        lines[idx] = mutant["after"] + "\n"
    with open(os.path.join(tmp, "picorv32.v"), "w",
              encoding="utf-8") as fh:
        fh.writelines(lines)
    return tmp


def _new_tb_case(name):
    return {"name": name, "project": "picorv32", "files": ["picorv32.v"],
            "top": "tb_picorv32_fetch_decode",
            "tb": "picorv32-fetch-decode_tb.sv",
            "anchor": DECODE_MUTANT["before"],
            "bug": DECODE_MUTANT["after"],
            "gate": "decode alu-immediate retired-immediate",
            "oracle_pass": "tohost: PASS",
            "oracle_trap": "TRAP:",
            "vflags": ["-Wno-PINMISSING"],
            "mutant_file": "picorv32.v",
            "mutant_line": DECODE_MUTANT["line"]}


def _build_and_run(tree, case, seed=42):
    binary = vbackend.build(tree, case)
    proc = subprocess.run(
        [str(binary), "+seed=%d" % seed], cwd=str(tree),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=300)
    return proc.stdout


def _checksum(log_text):
    det = [ln for ln in log_text.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


class TestFetchDecodeTb(unittest.TestCase):
    def test_original_tb_untouched(self):
        self.assertTrue(os.path.isfile(TB_OLD), TB_OLD)
        self.assertEqual(_sha256(TB_OLD), ORIGINAL_TB_SHA256,
                         "original fetch TB changed -- it must stay "
                         "byte-identical (new file, new oracle)")
        with open(TB_OLD, encoding="utf-8") as fh:
            old = fh.read()
        self.assertIn("256-word NOP", old)
        self.assertIn("module tb_picorv32_fetch", old)
        self.assertNotIn("cpuregs", old)
        self.assertNotIn("ADDI", old)

    def test_new_tb_exists_with_decode_oracle(self):
        self.assertTrue(os.path.isfile(TB_NEW), TB_NEW)
        with open(TB_NEW, encoding="utf-8") as fh:
            new = fh.read()
        with open(TB_OLD, encoding="utf-8") as fh:
            old = fh.read()
        self.assertNotEqual(new, old)
        self.assertIn("module tb_picorv32_fetch_decode", new)
        # The recommended scenario: nonzero-immediate ADDIs in boot ROM.
        self.assertIn("32'h07B00293", new)  # ADDI x5,x0,123
        self.assertIn("32'h00128313", new)  # ADDI x6,x5,1
        # The retired-immediate assertion + preserved old oracle.
        self.assertIn("cpuregs[5]", new)
        self.assertIn("cpuregs[6]", new)
        self.assertIn("tohost: PASS", new)
        self.assertIn("TRAP:", new)
        self.assertIn("CYCLES=", new)

    @NEEDS_VERILATOR
    @NEEDS_TREE
    def test_new_tb_compiles_lint_clean(self):
        proc = subprocess.run(
            [VERILATOR, "--lint-only", "--top-module",
             "tb_picorv32_fetch_decode",
             "-Wno-PINMISSING", PINNED_RTL, TB_NEW],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=300)
        self.assertEqual(proc.returncode, 0,
                         "lint failed:\n%s" % proc.stdout[-3000:])
        self.assertNotIn("Error", proc.stdout)

    @NEEDS_VERILATOR
    @NEEDS_TREE
    def test_red_on_decode_mutant(self):
        # Static half of the blind-spot proof (read-only): the board
        # recorded this mutant as equivalent (old-TB PASS, CYCLES=2009).
        with open(BOARD, encoding="utf-8") as fh:
            board = json.load(fh)
        matches = [e for e in board["discarded_equivalent"]
                   if e["record"]["file"] == "picorv32.v"
                   and e["record"]["line"] == DECODE_MUTANT["line"]
                   and e["record"]["after"] == DECODE_MUTANT["after"]]
        self.assertTrue(matches,
                        "recorded equivalent for the rd-slice mutant "
                        "vanished from board.json")
        self.assertIn("tohost: PASS", matches[0]["oracle_lines"])
        # Live half (local sim, no API): fire via the loop's OWN
        # pinned-line path (the exact code `afterglow run --spec`
        # uses for RED), then the new TB must go RED on it.
        from agent import loop as agent_loop
        dest = os.path.join(tempfile.mkdtemp(prefix="fetch-decode-mut-"),
                            "tree")
        try:
            case = _new_tb_case("fetch-decode-red")
            rec = agent_loop.prepare_mutant_tree(case, PINNED_TREE, dest)
            self.assertEqual(
                (rec["file"], rec["line"]),
                ("picorv32.v", DECODE_MUTANT["line"]))
            log = _build_and_run(dest, case)
            self.assertIn("tohost: FAIL", log,
                          "new TB stayed GREEN on the decode mutant:\n%s"
                          % log[-2000:])
            self.assertIn("TRAP: decode", log,
                          "new TB FAIL names the decode oracle:\n%s"
                          % log[-2000:])
            # Contrast on the IDENTICAL mutant tree: the old NOP-only
            # TB must stay GREEN (the escape, re-proved live, matching
            # the board's recorded oracle_lines).
            old_case = {"name": "fetch-decode-oldtb",
                        "project": "picorv32", "files": ["picorv32.v"],
                        "top": "tb_picorv32_fetch",
                        "tb": "picorv32-fetch_tb.sv",
                        "anchor": "x", "bug": "y", "gate": "contrast",
                        "oracle_pass": "tohost: PASS",
                        "oracle_trap": "TRAP:",
                        "vflags": ["-Wno-PINMISSING"]}
            old_log = _build_and_run(dest, old_case)
            self.assertIn("tohost: PASS", old_log,
                          "old TB went RED on the rd-slice mutant -- the "
                          "blind-spot contrast collapsed:\n%s"
                          % old_log[-2000:])
        finally:
            shutil.rmtree(os.path.dirname(dest), ignore_errors=True)

    @NEEDS_VERILATOR
    @NEEDS_TREE
    def test_green_pristine_with_golden(self):
        tree = _scratch_tree()
        try:
            log = _build_and_run(tree, _new_tb_case("fetch-decode-green"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)
        self.assertIn("tohost: PASS", log,
                      "new TB RED on pristine tree:\n%s" % log[-2000:])
        m = __import__("re").search(r"CYCLES=(\d+)", log)
        self.assertIsNotNone(m, "no CYCLES line:\n%s" % log[-2000:])
        self.assertEqual(int(m.group(1)), 2009,
                         "cycle count moved -- scenario timing changed")
        self.assertEqual(_checksum(log), FETCH_GOLDEN,
                         "pass-path checksum moved -- the new scenario "
                         "changed the deterministic log; document a new "
                         "golden instead of editing to match")

    def test_spec_wiring(self):
        # The same generic loader `afterglow run --spec` uses.
        self.assertTrue(os.path.isfile(SPEC_NEW), SPEC_NEW)
        case = vcore.load_case_file(SPEC_NEW)
        self.assertEqual(case["top"], "tb_picorv32_fetch_decode")
        self.assertEqual(case["tb"], "picorv32-fetch-decode_tb.sv")
        self.assertEqual(case["oracle_pass"], "tohost: PASS")
        self.assertEqual(case["project"], "picorv32")
        spec = vcore.load_spec(SPEC_NEW)
        self.assertEqual(spec.case_id, "picorv32-fetch-decode")
        # In-tree stem: the loop RED-dispatches sim/sim.py --case <stem>.
        self.assertTrue(os.path.isfile(
            os.path.join(ROOT, "sim", "targets",
                         spec.case_id + ".yaml")))
        # Same flat schema sim/sim.py runs (no network: pure YAML parse).
        sim_case = vbackend.load_case(spec.case_id)
        self.assertEqual(sim_case["top"], "tb_picorv32_fetch_decode")
        self.assertEqual(sim_case["tb"], "picorv32-fetch-decode_tb.sv")
        # The 8-case eval board is untouched by the new spec.
        with open(CASES_YAML, encoding="utf-8") as fh:
            board_cases = fh.read()
        self.assertNotIn("picorv32-fetch-decode", board_cases)
        # Spec anchor/bug is the proven RED fault, pinned to its
        # single line: the stripped anchor text occurs several times in
        # picorv32.v (main + compressed decoders), so line-anchored
        # inject would multi-fire -- the spec therefore carries
        # mutant_file/mutant_line and the loop fires the pinned line
        # only (same convention as sim/targets/mut-bigN-*.yaml).
        self.assertEqual(case.get("mutant_file"), "picorv32.v")
        self.assertEqual(int(case.get("mutant_line")),
                         DECODE_MUTANT["line"])
        with open(PINNED_RTL if os.path.isfile(PINNED_RTL) else TB_OLD,
                  encoding="utf-8", errors="replace") as fh:
            rtl_lines = fh.read().splitlines()
        pinned = rtl_lines[int(case["mutant_line"]) - 1]
        self.assertEqual(pinned.strip(), case["anchor"].strip())
        self.assertNotEqual(case["anchor"].strip(),
                            case["bug"].strip())

    def test_kill_rate_projection_readonly(self):
        from eval import tb_kill_rate as tkr
        rows, kept_cg, accounting = tkr._parent_rows()
        fetch = [r for r in rows if r["tb"] == "picorv32-fetch_tb.sv"][0]
        # Old numbers reproduced without writing anything.
        self.assertEqual((fetch["drawn"], fetch["kept"],
                          fetch["equivalent"], fetch["build_break"]),
                         (2269, 30, 2176, 63))
        self.assertAlmostEqual(fetch["kill_rate"], 30 / 2206, places=9)
        self.assertEqual(kept_cg, 0)
        # Static classification: fetch-parent equivalents in paths the
        # new ADDI/retired-immediate oracle observes (decode of ALU-imm,
        # I-immediate formation, rd routing, register writeback).
        with open(BOARD, encoding="utf-8") as fh:
            board = json.load(fh)
        tokens = ("decoded_imm", "decoded_rd", "cpuregs_wrdata",
                  "instr_addi", "is_alu_reg_imm", "latched_rd",
                  "cpuregs_rs1", "reg_op1", "alu_add_sub")
        pool, numb = 0, 0
        for e in board["discarded_equivalent"]:
            r = e["record"]
            if r["file"] != "picorv32.v":
                continue
            if any(t in (r["before"] + r["after"]) for t in tokens):
                pool += 1
                if r["op"] == "num_bump":
                    numb += 1
        self.assertGreater(pool, 0)
        print("\nprojection (STATIC, not measured): %d fetch-parent "
              "equivalents (%d num_bump) sit in ALU-imm decode/writeback "
              "paths the new oracle observes; old kill 30/2206=1.36%%; "
              "confirmed moved: candidate-301 rd-slice class (old GREEN "
              "per board, new RED per local sim)" % (pool, numb))


if __name__ == "__main__":
    unittest.main()
