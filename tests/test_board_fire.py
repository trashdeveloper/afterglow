"""Firing-path contract tests for the big-N board.

agent/loop.py is FROZEN (another track owns it): these tests pin its
prepare_mutant_tree contract for the new board's shapes -- a picorv32.v
fault and non-num_bump ops must rebuild byte-identically, and wrong
pristine revisions must fail loudly. Cases use the same schema as
sim/targets/mut-bigN-*.yaml (written by eval/draw_board.write_spec),
so the generator -> firing-path contract is tested end to end without
depending on generated files. No Verilator build here (prepare only
copies + edits); the campaign's --self-check covers the full 50.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import loop
from afterglow_core.verilator import load_case_file
from eval.draw_board import write_spec


def _make_tree(files):
    """Minimal pristine tree {relpath: [lines]} under a tmp dir."""
    tmp = tempfile.mkdtemp(prefix="boardfire-")
    for rel, lines in files.items():
        p = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return tmp


PICORV32_EXCERPT = [
    "wire mem_valid;",
    "assign mem_la_addr = (mem_do_prefetch || mem_do_rinst) ? {next_pc[31:2], 2'b00} : {reg_op1[31:2], 2'b00};",
    "if (mem_la_firstword_xfer > 0) begin",
    "mem_valid <= 1;",
    "end",
]

UART_EXCERPT = [
    "reg send_dummy;",
    "assign reg_dat_wait = reg_dat_we && (send_bitcnt || send_dummy);",
    "send_pattern <= ~0;",
]


class TestPrepareNewBoardShapes(unittest.TestCase):
    def test_cond_grow_on_picorv32_rebuilds_byte_identical(self):
        pristine = _make_tree({"picorv32.v": list(PICORV32_EXCERPT)})
        self.addCleanup(shutil.rmtree, pristine, True)
        dest = pristine + "-fired"
        self.addCleanup(shutil.rmtree, dest, True)
        case = {
            "mutant_file": "picorv32.v",
            "mutant_line": 3,
            "anchor": "if (mem_la_firstword_xfer > 0) begin",
            "bug": "if (mem_la_firstword_xfer >= 0) begin",
            "mutant_seed": 7,
            "mutant_op": "cond_grow",
        }
        rec = loop.prepare_mutant_tree(case, pristine, dest)
        self.assertEqual(rec["op"], "cond_grow")
        self.assertEqual(rec["line"], 3)
        with open(os.path.join(dest, "picorv32.v")) as fh:
            fired = fh.read().splitlines()
        want = list(PICORV32_EXCERPT)
        want[2] = "if (mem_la_firstword_xfer >= 0) begin"
        self.assertEqual(fired, want)
        with open(os.path.join(pristine, "picorv32.v")) as fh:
            self.assertEqual(fh.read().splitlines(), PICORV32_EXCERPT)

    def test_logic_swap_on_simpleuart_rebuilds_byte_identical(self):
        pristine = _make_tree({"picosoc/simpleuart.v": list(UART_EXCERPT)})
        self.addCleanup(shutil.rmtree, pristine, True)
        dest = pristine + "-fired"
        self.addCleanup(shutil.rmtree, dest, True)
        case = {
            "mutant_file": "picosoc/simpleuart.v",
            "mutant_line": 2,
            "anchor": "assign reg_dat_wait = reg_dat_we && (send_bitcnt || send_dummy);",
            "bug": "assign reg_dat_wait = reg_dat_we || (send_bitcnt || send_dummy);",
            "mutant_seed": 7,
            "mutant_op": "logic_swap",
        }
        loop.prepare_mutant_tree(case, pristine, dest)
        with open(os.path.join(dest, "picosoc/simpleuart.v")) as fh:
            fired = fh.read().splitlines()
        self.assertEqual(
            fired[1],
            "assign reg_dat_wait = reg_dat_we || (send_bitcnt || send_dummy);")

    def test_wrong_anchor_fails_loudly(self):
        pristine = _make_tree({"picorv32.v": list(PICORV32_EXCERPT)})
        self.addCleanup(shutil.rmtree, pristine, True)
        dest = pristine + "-fired"
        self.addCleanup(shutil.rmtree, dest, True)
        case = {
            "mutant_file": "picorv32.v",
            "mutant_line": 3,
            "anchor": "if (mem_la_firstword_xfer > 1) begin",
            "bug": "if (mem_la_firstword_xfer >= 1) begin",
            "mutant_seed": 7,
            "mutant_op": "cond_grow",
        }
        with self.assertRaises(loop.LoopError):
            loop.prepare_mutant_tree(case, pristine, dest)


class TestSpecRoundTrip(unittest.TestCase):
    def test_write_spec_feeds_prepare_mutant_tree(self):
        kept = {
            "mutant_id": "mut-bigN-01",
            "file": "picorv32.v",
            "line": 3,
            "before": "if (mem_la_firstword_xfer > 0) begin",
            "after": "if (mem_la_firstword_xfer >= 0) begin",
            "op": "cond_grow",
            "seed": 7,
            "parent_case": "picorv32-fetch-misalign",
            "parent": {
                "project": "picorv32",
                "files": ["picorv32.v"],
                "top": "tb_picorv32_fetch",
                "tb": "picorv32-fetch_tb.sv",
                "vflags": ["-Wno-PINMISSING"],
                "oracle_pass": "tohost: PASS",
                "oracle_trap": "TRAP:",
                "gate": "fetch test gate",
            },
            "tree": "/tmp/mutboard/kept/mut-bigN-01",
        }
        tmp = tempfile.mkdtemp(prefix="boardspec-")
        self.addCleanup(shutil.rmtree, tmp, True)
        spec_path = os.path.join(tmp, "mut-bigN-01.yaml")
        write_spec(kept, spec_path)
        # The generated spec must load through the loop's canonical
        # loader and fire the pinned fault onto a pristine tree.
        case = load_case_file(spec_path)
        self.assertEqual(int(case["mutant_line"]), 3)
        self.assertEqual(case["mutant_op"], "cond_grow")
        pristine = _make_tree({"picorv32.v": list(PICORV32_EXCERPT)})
        self.addCleanup(shutil.rmtree, pristine, True)
        dest = pristine + "-fired"
        self.addCleanup(shutil.rmtree, dest, True)
        rec = loop.prepare_mutant_tree(case, pristine, dest)
        self.assertEqual(rec["after"], kept["after"])
        with open(os.path.join(dest, "picorv32.v")) as fh:
            fired = fh.read().splitlines()
        want = list(PICORV32_EXCERPT)
        want[2] = kept["after"]
        self.assertEqual(fired, want)


if __name__ == "__main__":
    unittest.main()
