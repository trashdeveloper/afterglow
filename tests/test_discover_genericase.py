"""Task H3 (item 83): design-agnostic case spec red-first tests.

Plan contract (binding):
- Create: `agent/discover/genericase.py`
- Test: `tests/test_discover_genericase.py` (this file)
- Consumes: `afterglow_core/types.py` CaseSpec.
- Produces: `from_any(verilog_files, tb, oracle_cmd) -> CaseSpec`
  validated (files exist, oracle executable).
- Step 1 test: a toy 2-file design + shell-oracle TB runs end-to-end
  (fixture sim) through the spec.

Run: python3 -m unittest tests.test_discover_genericase -v (from repo root)
"""

import ast
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from discover import genericase  # noqa: E402  (RED before implementation: ModuleNotFoundError)
from afterglow_core.types import CaseSpec, decide_verdict  # noqa: E402


TOY_DUT = """module toy_dut(input wire clk, output reg q);
  always @(posedge clk) q <= ~q;
endmodule
"""

TOY_AUX = """module toy_aux(input wire a, output wire b);
  assign b = ~a;
endmodule
"""


def _write(path, text, executable=False):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if executable:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _toy_tree(tmp):
    """Toy 2-file design + shell-oracle TB; returns (dut, aux, tb, oracle)."""
    dut = _write(os.path.join(tmp, "dut.v"), TOY_DUT)
    aux = _write(os.path.join(tmp, "aux.v"), TOY_AUX)
    tb = _write(os.path.join(tmp, "toy_tb.sh"),
                "#!/bin/sh\necho 'tb: toy stimulus done'\n",
                executable=True)
    oracle = _write(os.path.join(tmp, "oracle.sh"),
                    "#!/bin/sh\necho 'tohost: PASS toy'\n",
                    executable=True)
    return dut, aux, tb, oracle


class TestFromAnyHappyPath(unittest.TestCase):
    def test_returns_casespec(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            spec = genericase.from_any([dut, aux], tb, oracle)
            self.assertIsInstance(spec, CaseSpec)

    def test_files_packed_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            spec = genericase.from_any([dut, aux], tb, oracle)
            self.assertEqual(spec.files, [dut, aux, tb])

    def test_oracle_marker_carried(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            spec = genericase.from_any([dut, aux], tb, oracle,
                                       oracle_pass="tohost: PASS toy")
            self.assertEqual(spec.oracle_pass, "tohost: PASS toy")

    def test_defaults_sane(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            spec = genericase.from_any([dut, aux], tb, oracle)
            self.assertTrue(spec.case_id)
            self.assertTrue(spec.project)
            self.assertTrue(spec.oracle_pass)
            self.assertTrue(spec.budget_s > 0)
            self.assertEqual(list(spec.allowlist), [])
            self.assertIsInstance(spec.lint_cmd, str)

    def test_accepts_list_oracle_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            spec = genericase.from_any([dut, aux], tb, [oracle])
            self.assertIsInstance(spec, CaseSpec)

    def test_case_id_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            spec = genericase.from_any([dut, aux], tb, oracle,
                                       case_id="toy-1", project="toyproj")
            self.assertEqual(spec.case_id, "toy-1")
            self.assertEqual(spec.project, "toyproj")


class TestFromAnyValidation(unittest.TestCase):
    def test_missing_verilog_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            with self.assertRaises((FileNotFoundError, ValueError)):
                genericase.from_any(
                    [dut, os.path.join(tmp, "nope.v")], tb, oracle)

    def test_missing_tb_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            with self.assertRaises((FileNotFoundError, ValueError)):
                genericase.from_any(
                    [dut, aux], os.path.join(tmp, "no-tb.sh"), oracle)

    def test_empty_verilog_list_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _dut, _aux, tb, oracle = _toy_tree(tmp)
            with self.assertRaises((TypeError, ValueError)):
                genericase.from_any([], tb, oracle)

    def test_non_executable_oracle_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, _oracle = _toy_tree(tmp)
            flat = _write(os.path.join(tmp, "flat.sh"),
                          "#!/bin/sh\necho hi\n")  # NOT executable
            self.assertFalse(os.access(flat, os.X_OK))
            with self.assertRaises((PermissionError, ValueError)):
                genericase.from_any([dut, aux], tb, flat)

    def test_missing_oracle_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, _oracle = _toy_tree(tmp)
            with self.assertRaises((FileNotFoundError, ValueError)):
                genericase.from_any(
                    [dut, aux], tb, os.path.join(tmp, "no-oracle.sh"))

    def test_blank_oracle_pass_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            with self.assertRaises(ValueError):
                genericase.from_any([dut, aux], tb, oracle, oracle_pass="  ")

    def test_blank_oracle_cmd_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, _oracle = _toy_tree(tmp)
            with self.assertRaises((TypeError, ValueError)):
                genericase.from_any([dut, aux], tb, "")

    def test_bad_budget_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            with self.assertRaises((TypeError, ValueError)):
                genericase.from_any([dut, aux], tb, oracle, budget_s=0)
            with self.assertRaises((TypeError, ValueError)):
                genericase.from_any([dut, aux], tb, oracle, budget_s=True)


class TestEndToEndFixtureSim(unittest.TestCase):
    """Toy 2-file design + shell-oracle TB through the spec (fixture sim).

    No Verilator build, no model call, no loop change: the oracle shell
    script stands in for the sim backend, and the verdict comes from the
    spec's oracle_pass marker via decide_verdict (exit code ignored).
    """

    def test_pass_oracle_verdicts_green_through_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, oracle = _toy_tree(tmp)
            spec = genericase.from_any([dut, aux], tb, oracle,
                                       case_id="toy-pass",
                                       oracle_pass="tohost: PASS toy")
            proc = subprocess.run([oracle], capture_output=True, text=True,
                                  timeout=30)
            verdict = decide_verdict(proc.stdout, spec.oracle_pass)
            self.assertTrue(getattr(verdict, "pass"))

    def test_fail_oracle_verdicts_red_through_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, _oracle = _toy_tree(tmp)
            bad = _write(os.path.join(tmp, "oracle_bad.sh"),
                         "#!/bin/sh\necho 'tohost: FAIL toy'\n",
                         executable=True)
            spec = genericase.from_any([dut, aux], tb, bad,
                                       case_id="toy-fail",
                                       oracle_pass="tohost: PASS toy")
            proc = subprocess.run([bad], capture_output=True, text=True,
                                  timeout=30)
            verdict = decide_verdict(proc.stdout, spec.oracle_pass)
            self.assertFalse(getattr(verdict, "pass"))

    def test_exit_code_ignored_pass_marker_rules(self):
        # Oracle exits nonzero yet prints the marker: marker still rules.
        with tempfile.TemporaryDirectory() as tmp:
            dut, aux, tb, _oracle = _toy_tree(tmp)
            weird = _write(os.path.join(tmp, "oracle_weird.sh"),
                           "#!/bin/sh\necho 'tohost: PASS toy'\nexit 3\n",
                           executable=True)
            spec = genericase.from_any([dut, aux], tb, weird,
                                       oracle_pass="tohost: PASS toy")
            proc = subprocess.run([weird], capture_output=True, text=True,
                                  timeout=30)
            self.assertNotEqual(proc.returncode, 0)
            verdict = decide_verdict(proc.stdout, spec.oracle_pass)
            self.assertTrue(getattr(verdict, "pass"))


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "genericase", text,
                "%s must not reference the genericase module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "genericase.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint({"requests", "urllib"}),
                        "no live HTTP imports allowed ($0 gate): %r"
                        % (imported,))
        for token in ("MODEL_API_KEY", "urllib", "requests"):
            self.assertNotIn(token, src,
                             "no live-API usage allowed ($0 gate): %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
