"""Task A2 (TB blind-spot hunter) red-first tests: agent/discover/blindspot.py.

- hunt_blindspots(pristine_tree, case, mutants) injects each kept mutant
  (file/line/before/after, board.json kept-entry shape) into a scratch
  tree, runs the EXISTING TB unmodified via vbackend build/run
  primitives, and reports per-mutant killed/survived.
  kill = oracle_pass ABSENT from the sim log.
- Fixture with 2 killed + 1 survived fixture mutants yields a pinned
  2/3 report.
- The TB is never modified: the TB file hash is identical after a run,
  and a mutant targeting the TB itself is refused loudly.
- Errors (build/run/empty-log/anchor-mismatch) raise loudly and are
  NEVER reported as kills or survivals.

Run: python3 tests/test_discover_blindspot.py -v   (from repo root)
"""
import hashlib
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import blindspot  # noqa: E402  (RED before implementation: ModuleNotFoundError)


REAL_TB = os.path.join(ROOT, "sim", "tb", "picosoc-uart-div_tb.sv")
ORACLE = "tohost: PASS"


def _tiny_case():
    return {
        "name": "blindspot-tiny",
        "project": "picorv32",
        "files": ["rtl/dummy.v"],
        "top": "tb_dummy",
        "tb": "picosoc-uart-div_tb.sv",
        "oracle_pass": ORACLE,
    }


def _write_pristine(tmpdir):
    """Minimal pristine tree: one RTL file with three pinned lines."""
    pristine = os.path.join(tmpdir, "pristine")
    rtl_dir = os.path.join(pristine, "rtl")
    os.makedirs(rtl_dir, exist_ok=True)
    with open(os.path.join(rtl_dir, "dummy.v"), "w",
              encoding="utf-8") as fh:
        fh.write("wire a = 1'b0;\n"
                 "wire b = 1'b1;\n"
                 "wire c = 1'b0;\n")
    return pristine


def _fixture_mutants():
    return [
        {"mutant_id": "fix-killed-1", "file": "rtl/dummy.v", "line": 1,
         "before": "wire a = 1'b0;", "after": "wire a = 1'b1;"},
        {"mutant_id": "fix-killed-2", "file": "rtl/dummy.v", "line": 2,
         "before": "wire b = 1'b1;", "after": "wire b = 1'b0;"},
        {"mutant_id": "fix-survived-1", "file": "rtl/dummy.v", "line": 3,
         "before": "wire c = 1'b0;", "after": "wire c = 1'b1;"},
    ]


def _fixture_run_factory():
    """Fake sim: first two mutants kill the oracle, third survives."""
    calls = []

    def _fake_run(binary, seed, mutant):
        calls.append((binary, seed, dict(mutant)))
        if mutant.get("mutant_id") == "fix-survived-1":
            return "tohost: PASS\nCYCLES=2000\n"
        return "tohost: FAIL 0xdead\nTRAP: fixture kill\nCYCLES=2001\n"

    _fake_run.calls = calls
    return _fake_run


def _sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class TestHuntReport(unittest.TestCase):
    def test_two_killed_one_survived_yields_pinned_two_thirds(self):
        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            report = blindspot.hunt_blindspots(
                pristine, _tiny_case(), _fixture_mutants(),
                build_fn=lambda t, c: "/fake/binary",
                run_fn=_fixture_run_factory(),
                workdir=os.path.join(tmp, "scratch"))
        self.assertEqual(report["n"], 3)
        self.assertEqual(report["killed"],
                         ["fix-killed-1", "fix-killed-2"])
        self.assertEqual(report["survived"], ["fix-survived-1"])
        self.assertAlmostEqual(report["kill_rate"], 2 / 3, places=9)
        verdicts = {r["mutant_id"]: r["verdict"]
                    for r in report["results"]}
        self.assertEqual(verdicts, {
            "fix-killed-1": "killed",
            "fix-killed-2": "killed",
            "fix-survived-1": "survived",
        })
        # Every result carries its oracle evidence.
        for r in report["results"]:
            self.assertIn("oracle_present", r)
            self.assertEqual(r["verdict"],
                             "survived" if r["oracle_present"] else "killed")

    def test_empty_mutants_raises_never_reports_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            with self.assertRaises(ValueError):
                blindspot.hunt_blindspots(
                    pristine, _tiny_case(), [],
                    build_fn=lambda t, c: "/fake/binary",
                    run_fn=_fixture_run_factory())


class TestTbNeverModified(unittest.TestCase):
    def test_tb_file_hash_unchanged_after_run(self):
        before = _sha256_file(REAL_TB)
        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            blindspot.hunt_blindspots(
                pristine, _tiny_case(), _fixture_mutants(),
                build_fn=lambda t, c: "/fake/binary",
                run_fn=_fixture_run_factory(),
                workdir=os.path.join(tmp, "scratch"))
        self.assertEqual(_sha256_file(REAL_TB), before)

    def test_mutant_targeting_tb_is_refused(self):
        evil = [{"mutant_id": "evil-tb", "file": "picosoc-uart-div_tb.sv",
                 "line": 1, "before": "x", "after": "y"}]
        before = _sha256_file(REAL_TB)
        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            with self.assertRaises(ValueError):
                blindspot.hunt_blindspots(
                    pristine, _tiny_case(), evil,
                    build_fn=lambda t, c: "/fake/binary",
                    run_fn=_fixture_run_factory(),
                    workdir=os.path.join(tmp, "scratch"))
        # The TB on disk is byte-identical to before the refused call.
        self.assertEqual(_sha256_file(REAL_TB), before)

    def test_run_fn_modifying_tb_is_detected_loudly(self):
        with open(REAL_TB, "rb") as fh:
            original = fh.read()
        try:
            def _evil_run(binary, seed, mutant):
                with open(REAL_TB, "ab") as fh:
                    fh.write(b"\n// evil\n")
                return "tohost: FAIL 0xdead\n"

            with tempfile.TemporaryDirectory() as tmp:
                pristine = _write_pristine(tmp)
                with self.assertRaises(RuntimeError):
                    blindspot.hunt_blindspots(
                        pristine, _tiny_case(), _fixture_mutants()[:1],
                        build_fn=lambda t, c: "/fake/binary",
                        run_fn=_evil_run,
                        workdir=os.path.join(tmp, "scratch"))
        finally:
            with open(REAL_TB, "wb") as fh:
                fh.write(original)
        self.assertEqual(_sha256_file(REAL_TB),
                         hashlib.sha256(original).hexdigest())


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_build_error_propagates_never_counted(self):
        def _bad_build(tree, case):
            raise RuntimeError("verilator build failed")

        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            with self.assertRaises(RuntimeError):
                blindspot.hunt_blindspots(
                    pristine, _tiny_case(), _fixture_mutants()[:1],
                    build_fn=_bad_build,
                    run_fn=_fixture_run_factory())

    def test_run_error_propagates_never_counted(self):
        def _boom(binary, seed, mutant):
            raise RuntimeError("sim transport died")

        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            with self.assertRaises(RuntimeError):
                blindspot.hunt_blindspots(
                    pristine, _tiny_case(), _fixture_mutants()[:1],
                    build_fn=lambda t, c: "/fake/binary",
                    run_fn=_boom)

    def test_empty_log_raises_instead_of_faking_a_kill(self):
        def _silent(binary, seed, mutant):
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            with self.assertRaises(RuntimeError):
                blindspot.hunt_blindspots(
                    pristine, _tiny_case(), _fixture_mutants()[:1],
                    build_fn=lambda t, c: "/fake/binary",
                    run_fn=_silent)

    def test_anchor_mismatch_raises_instead_of_misfiring(self):
        bad = [{"mutant_id": "bad-anchor", "file": "rtl/dummy.v", "line": 1,
                "before": "wire a = 1'b1;",  # pristine holds 1'b0 here
                "after": "wire a = 1'bx;"}]
        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            with self.assertRaises(RuntimeError):
                blindspot.hunt_blindspots(
                    pristine, _tiny_case(), bad,
                    build_fn=lambda t, c: "/fake/binary",
                    run_fn=_fixture_run_factory())

    def test_pristine_tree_never_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            pristine = _write_pristine(tmp)
            rtl = os.path.join(pristine, "rtl", "dummy.v")
            with open(rtl, "rb") as fh:
                before = fh.read()
            blindspot.hunt_blindspots(
                pristine, _tiny_case(), _fixture_mutants(),
                build_fn=lambda t, c: "/fake/binary",
                run_fn=_fixture_run_factory(),
                workdir=os.path.join(tmp, "scratch"))
            with open(rtl, "rb") as fh:
                self.assertEqual(fh.read(), before)


if __name__ == "__main__":
    unittest.main()
