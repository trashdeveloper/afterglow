"""Task 4 tests: `afterglow run --spec` on a stranger case.

Run: python3 tests/test_spec_run.py -v   (from repo root)

Static fixtures only, no model calls, no real sim builds:
- test_resim_crash_stays_red: a SimBackend whose run() raises must yield
  bundle meta.json with status "red" + recorded error, exit 1, and a
  verdict_after.json whose pass is False -- a crash is never green.
- test_resim_red/green pin the clean-RED (exit 1, no crash error) and
  clean-GREEN (exit 0) directions through the same resim() seam.
- test_resim_empty_oracle_stays_red (fix round 1): a backend claiming
  pass with a blank oracle_pass in evidence must yield RED (fail closed:
  "" matches every log, so the seam must never manufacture green).
- test_verilator_backend/load_spec blank-oracle refusal (fix round 1):
  construction/loading with a blank oracle raises ValueError fast.
- test_load_spec_*: the --spec YAML path loads to a full 8-field
  CaseSpec; missing files/keys fail loudly (no silent defaults).
- test_unknown_stem_spec_rejected_fast (fix round 1): --spec is
  in-tree-only; a valid YAML whose stem has no sim/targets/<stem>.yaml
  is rejected RED before any sim build or model call (no boot.log).
- test_cli_*: the `afterglow` entrypoint fails fast (exit 1) on a bad
  --spec path and answers --help, without touching the model.
- test_score_triage_*: eval/score.py handles mode:"triage" explicitly:
  triage-only cases get a TRIAGE row (never silently vanish, never
  counted green); a later triage never masks an earlier decisive
  GREEN/RED for the same case.
- test_verdict_keyword_convention: static scan (same shape as
  test_policy.py) over this task's files -- Verdict is only ever built
  as Verdict(**{"pass": ..., "evidence": ...}) and read via
  getattr(v, "pass"); never positional, never .pass access.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import CaseSpec, Verdict
import afterglow_core.verilator as verilator
from agent import loop
from eval import score

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
AFTERGLOW = os.path.join(ROOT, "afterglow")


class CrashBackend:
    """SimBackend whose run() always raises (segfault-like)."""

    def build(self, tree) -> None:
        return None

    def run(self, seed: int) -> Verdict:
        raise RuntimeError("boom: sim segfault")


class RedBackend:
    """SimBackend returning a clean RED verdict (oracle marker absent)."""

    def build(self, tree) -> None:
        return None

    def run(self, seed: int) -> Verdict:
        return Verdict(
            **{"pass": False,
               "evidence": {"tohost": "FAIL 0xdead", "seed": seed}})


class GreenBackend:
    """SimBackend returning a clean GREEN verdict (oracle marker found)."""

    def build(self, tree) -> None:
        return None

    def run(self, seed: int) -> Verdict:
        return Verdict(
            **{"pass": True,
               "evidence": {"tohost": "PASS", "seed": seed}})


class EmptyOracleBackend:
    """SimBackend claiming pass while declaring a blank oracle marker."""

    def build(self, tree) -> None:
        return None

    def run(self, seed: int) -> Verdict:
        return Verdict(
            **{"pass": True,
               "evidence": {"oracle_pass": "", "tohost": "PASS",
                            "seed": seed}})


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class TestResimCrash(unittest.TestCase):
    def test_resim_crash_stays_red(self):
        # A crashing backend must NEVER read green: exit 1, meta.json
        # status red with the crash recorded, verdict_after pass False.
        # NOTE: `pass` is read via getattr (keyword, see types.Verdict).
        with tempfile.TemporaryDirectory() as tmp:
            rc = verilator.resim(CrashBackend(), seed=42, run_dir=tmp)
            self.assertEqual(rc, 1)
            meta = _read_json(os.path.join(tmp, "meta.json"))
            self.assertEqual(meta["status"], "red")
            self.assertIn("error", meta)
            self.assertIn("boom", meta["error"])
            verdict = _read_json(
                os.path.join(tmp, "artifacts", "verdict_after.json"))
            self.assertIs(getattr(
                Verdict(**{"pass": verdict["pass"], "evidence": {}}),
                "pass"), False)
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "artifacts", "boot_after.log")))

    def test_resim_clean_red(self):
        # A backend RED verdict (no crash) is exit 1 / status red, but
        # carries no crash "error" -- clean red, not a crash.
        with tempfile.TemporaryDirectory() as tmp:
            rc = verilator.resim(RedBackend(), seed=7, run_dir=tmp)
            self.assertEqual(rc, 1)
            meta = _read_json(os.path.join(tmp, "meta.json"))
            self.assertEqual(meta["status"], "red")
            self.assertNotIn("error", meta)
            verdict = _read_json(
                os.path.join(tmp, "artifacts", "verdict_after.json"))
            self.assertIs(verdict["pass"], False)

    def test_resim_clean_green(self):
        # A backend GREEN verdict is exit 0 / status green.
        with tempfile.TemporaryDirectory() as tmp:
            rc = verilator.resim(GreenBackend(), seed=7, run_dir=tmp)
            self.assertEqual(rc, 0)
            meta = _read_json(os.path.join(tmp, "meta.json"))
            self.assertEqual(meta["status"], "green")
            verdict = _read_json(
                os.path.join(tmp, "artifacts", "verdict_after.json"))
            self.assertIs(verdict["pass"], True)

    def test_resim_empty_oracle_stays_red(self):
        # Fix round 1: "" in <any log> is True, so a pass claimed under
        # a blank declared oracle must NEVER read green -- fail closed.
        with tempfile.TemporaryDirectory() as tmp:
            rc = verilator.resim(EmptyOracleBackend(), seed=42,
                                 run_dir=tmp)
            self.assertEqual(rc, 1)
            meta = _read_json(os.path.join(tmp, "meta.json"))
            self.assertEqual(meta["status"], "red")
            self.assertIn("error", meta)
            self.assertIn("empty oracle_pass", meta["error"])
            verdict = _read_json(
                os.path.join(tmp, "artifacts", "verdict_after.json"))
            self.assertIs(verdict["pass"], False)

    def test_verilator_backend_rejects_blank_oracle(self):
        # Fix round 1: the REAL backend refuses a blank oracle at
        # construction, before any build -- decide_verdict(log, "")
        # would otherwise report True on everything.
        with self.assertRaises(ValueError):
            verilator.VerilatorBackend({"name": "x", "oracle_pass": ""})
        with self.assertRaises(ValueError):
            verilator.VerilatorBackend({"name": "x", "oracle_pass": "   "})
        backend = verilator.VerilatorBackend(
            {"name": "x", "oracle_pass": "tohost: PASS"})
        self.assertEqual(backend.case["name"], "x")

    def test_verilator_run_evidence_carries_oracle(self):
        # Fix round 2: run()'s evidence must explicitly declare the
        # oracle it judged by, so resim's fail-closed gate never rests
        # on the __init__ guard (or decide_verdict's evidence shape)
        # alone. /bin/echo stands in for the sim binary: no verilator,
        # no model -- only the evidence plumbing is exercised.
        with tempfile.TemporaryDirectory() as tmp:
            backend = verilator.VerilatorBackend(
                {"name": "x", "oracle_pass": "tohost: PASS"})
            backend.tree = tmp
            backend.binary = "/bin/echo"
            verdict = backend.run(42)
            self.assertEqual(verdict.evidence.get("oracle_pass"),
                             "tohost: PASS")
            self.assertIs(getattr(verdict, "pass"), False)
            self.assertIn("+seed=42", verdict.evidence.get("log", ""))


class TestLoadSpec(unittest.TestCase):
    def test_load_spec_stranger_yaml(self):
        # Any in-tree target YAML loads to the full 8-field CaseSpec.
        spec = verilator.load_spec(
            os.path.join(ROOT, "sim", "targets", "uart-start-half.yaml"))
        self.assertIsInstance(spec, CaseSpec)
        self.assertEqual(
            sorted(vars(spec)),
            sorted(["case_id", "project", "files", "oracle_pass",
                    "oracle_trap", "budget_s", "allowlist", "lint_cmd"]))
        self.assertEqual(spec.case_id, "uart-start-half")
        self.assertEqual(spec.oracle_pass, "tohost: PASS")
        self.assertEqual(spec.files, ["picosoc/simpleuart.v"])
        self.assertEqual(spec.budget_s, 600)
        self.assertEqual(spec.lint_cmd, "")

    def test_load_spec_missing_file_fails_loud(self):
        with self.assertRaises(OSError):
            verilator.load_spec(os.path.join(ROOT, "sim", "nope.yaml"))

    def test_load_spec_missing_key_fails_loud(self):
        # A YAML without oracle_pass is not a runnable spec -- KeyError,
        # never a silent default marker.
        with tempfile.NamedTemporaryFile(
                "w", suffix=".yaml", delete=False) as fh:
            fh.write("project: picorv32\nfiles: [a.v]\n")
            path = fh.name
        try:
            with self.assertRaises(KeyError):
                verilator.load_spec(path)
        finally:
            os.unlink(path)

    def test_load_spec_blank_oracle_rejected(self):
        # Fix round 1: a blank oracle marker matches every log, so the
        # spec is refused at load (ValueError) -- never a runnable spec.
        with tempfile.NamedTemporaryFile(
                "w", suffix=".yaml", delete=False) as fh:
            fh.write("project: picorv32\nfiles: [a.v]\n"
                     "top: T\ntb: t.sv\nanchor: A\nbug: B\ngate: G\n"
                     'oracle_pass: ""\n')
            path = fh.name
        try:
            with self.assertRaises(ValueError):
                verilator.load_spec(path)
        finally:
            os.unlink(path)


def _write_valid_case_yaml(directory, stem):
    path = os.path.join(directory, stem + ".yaml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("project: picorv32\nfiles: [picosoc/simpleuart.v]\n"
                 "top: tb_picosoc_uart\ntb: picosoc-uart-div_tb.sv\n"
                 'anchor: "if (2*recv_divcnt > cfg_divider) begin"\n'
                 'bug: "if (recv_divcnt > cfg_divider) begin"\n'
                 'gate: "divider baud uart"\n'
                 'oracle_pass: "tohost: PASS"\n')
    return path


class TestUnknownStem(unittest.TestCase):
    def test_unknown_stem_spec_rejected_fast(self):
        # Fix round 1: --spec is in-tree-only. A fully valid YAML whose
        # stem has no sim/targets/<stem>.yaml is rejected RED before any
        # sim build or model call -- no boot.log, error names the rule.
        run_id = "test-unknown-stem-spec"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = _write_valid_case_yaml(tmp, "stranger-xyz")
            rc = loop.main(["--spec", spec_path, "--seed", "42",
                            "--run-id", run_id])
        self.assertEqual(rc, 1)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "red")
        self.assertIn("error", meta)
        self.assertIn("in-tree", meta["error"])
        self.assertFalse(os.path.isfile(
            os.path.join(run_dir, "artifacts", "boot.log")))


class TestCli(unittest.TestCase):
    def test_cli_missing_spec_exits_1(self):
        # Bad --spec path fails fast (exit 1) with no model call, no
        # bundle, no guessing.
        proc = subprocess.run(
            [AFTERGLOW, "run", "--spec",
             os.path.join(ROOT, "sim", "targets", "nope.yaml")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 1)

    def test_cli_help(self):
        proc = subprocess.run(
            [AFTERGLOW, "run", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--spec", proc.stdout)


def _write_meta(run_dir, meta):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "meta.json"), "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps(meta) + "\n")


class TestScoreTriage(unittest.TestCase):
    def test_triage_only_case_gets_triage_row(self):
        # A triage bundle (DIAGNOSE+EXPLAIN, patch withheld) must appear
        # as an explicit TRIAGE row: never silently vanished, never
        # counted green.
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(os.path.join(tmp, "triage-1"), {
                "case_id": "uart-start-half", "status": "triage",
                "mode": "triage", "wall_end_utc": "2026-10-26T19:00:00Z"})
            runs = score.load_runs(tmp)
            latest = score.latest_by_case(runs)
            cases = [{"id": "uart-start-half", "tier": "stress",
                      "budget_s": "?"}]
            board, lg, lt, fg, ft = score.build_board(cases, latest)
            self.assertIn("TRIAGE", board)
            self.assertNotIn("UNRUN", board)
            self.assertEqual((lg, lt, fg, ft), (0, 0, 0, 1))

    def test_triage_never_masks_decisive(self):
        # A later triage re-run must not flip an earlier decisive GREEN
        # (or RED): scoring prefers decisive bundles; triage proves a
        # diagnosis happened without claiming fix status.
        with tempfile.TemporaryDirectory() as tmp:
            _write_meta(os.path.join(tmp, "live-green"), {
                "case_id": "uart-start-half", "status": "green",
                "mode": "live", "wall_end_utc": "2026-10-26T18:00:00Z"})
            _write_meta(os.path.join(tmp, "triage-later"), {
                "case_id": "uart-start-half", "status": "triage",
                "mode": "triage", "wall_end_utc": "2026-10-26T19:00:00Z"})
            runs = score.load_runs(tmp)
            latest = score.latest_by_case(runs)
            cases = [{"id": "uart-start-half", "tier": "stress",
                      "budget_s": "?"}]
            board, _lg, _lt, fg, _ft = score.build_board(cases, latest)
            self.assertIn("GREEN", board)
            self.assertNotIn("TRIAGE", board)
            self.assertEqual(fg, 1)


class TestKeywordConvention(unittest.TestCase):
    def test_verdict_keyword_convention(self):
        # Same static rule as test_policy.py: Verdict only via **kwargs,
        # read only via getattr -- over this task's new/changed files.
        for name in ("test_spec_run.py", "../afterglow_core/verilator.py",
                     "../afterglow", "../eval/score.py", "../agent/loop.py"):
            path = os.path.join(THIS_DIR, name)
            with open(path, "rb") as fh:
                toks = list(tokenize.tokenize(fh.readline))
            code = " ".join(
                t.string
                for t in toks
                if t.type not in (
                    tokenize.COMMENT,
                    tokenize.STRING,
                    tokenize.FSTRING_START,
                    tokenize.FSTRING_MIDDLE,
                    tokenize.FSTRING_END,
                    tokenize.ENCODING,
                    tokenize.NL,
                    tokenize.NEWLINE,
                    tokenize.INDENT,
                    tokenize.DEDENT,
                    tokenize.ENDMARKER,
                )
            )
            self.assertIsNone(
                re.search(r"Verdict\s*\((?!\s*\*)", code),
                f"{name}: positional Verdict(...) construction found",
            )
            self.assertIsNone(
                re.search(r"\.\s*pass\b", code),
                f"{name}: attribute-style .pass access found",
            )


if __name__ == "__main__":
    unittest.main()
