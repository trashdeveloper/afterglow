"""ONBOARD-SIMPLE tests: `afterglow doctor`, `afterglow demo`, `init --here`.

Run: python3 tests/test_onboard_simple.py -v   (from repo root)

Static fixtures + unittest.mock seams only -- no live API calls, no keys,
no real sim builds, no real Verilator:

- doctor: checklist lines (python / verilator --version / api-key
  presence-only / pins / pinned trees / suite smoke) with fixture envs;
  exit 0 all-pass, exit 1 naming each miss + its one-command fix.
- demo: runs the documented fastest-locked-GREEN case in triage mode
  (loop.main stubbed at the seam, like tests/test_watch.py does);
  no key -> loud FAIL with the exact fix, loop.main never called.
- init --here: drafts a starter CaseSpec from the cwd's .v/.sv files
  (watch's draft logic reused) that load_case_file accepts.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import doctor
from agent import watch
from afterglow_core.verilator import load_case_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")


def _pin_dir(projects=("picorv32",)):
    tmp = tempfile.TemporaryDirectory()
    with open(os.path.join(tmp.name, "targets.pin"), "w",
              encoding="utf-8") as fh:
        for p in projects:
            fh.write("%s https://example.invalid/%s.git %s\n"
                     % (p, p, "a" * 40))
    with open(os.path.join(tmp.name, "foundation.pin"), "w",
              encoding="utf-8") as fh:
        fh.write("picorv32 https://example.invalid/picorv32.git %s\n"
                 % ("b" * 40))
    return tmp


class TestDoctorLines(unittest.TestCase):
    def test_python_old_is_fail_with_upgrade_fix(self):
        res = doctor.check_python(py_version=(3, 8, 0))
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("3.10", res["fix"])

    def test_python_current_is_pass(self):
        res = doctor.check_python()
        self.assertEqual(res["status"], "PASS")
        self.assertIn(str(sys.version_info.major), res["message"])

    def test_verilator_missing_is_fail_with_install_fix(self):
        res = doctor.check_verilator(which=lambda _name: None)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("install", res["message"].lower())
        self.assertIn("verilator", res["fix"].lower())

    def test_verilator_present_reports_version(self):
        res = doctor.check_verilator(
            which=lambda _name: "/usr/bin/verilator",
            run=lambda _cmd: (0, "Verilator 5.052 2026-09-05\n"))
        self.assertEqual(res["status"], "PASS")
        self.assertIn("5.052", res["message"])

    def test_verilator_version_cmd_fails_is_fail(self):
        res = doctor.check_verilator(
            which=lambda _name: "/usr/bin/verilator",
            run=lambda _cmd: (1, "boom"))
        self.assertEqual(res["status"], "FAIL")

    def test_api_key_ok_never_prints_value(self):
        secret = "sk-fake-SECRET-xyz-123"
        res = doctor.check_api_key(env={"MODEL_API_KEY": secret},
                                   dotenv={})
        self.assertEqual(res["status"], "PASS")
        self.assertNotIn(secret, res["message"])

    def test_api_key_missing_fail_names_export_fix(self):
        res = doctor.check_api_key(env={}, dotenv={})
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("MODEL_API_KEY", res["message"])
        self.assertIn("export MODEL_API_KEY", res["fix"])

    def test_pins_missing_is_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = doctor.check_pins(tmp)
            fails = [r for r in results if r["status"] == "FAIL"]
            self.assertTrue(fails)
            self.assertTrue(any("foundation.pin" in r["message"]
                                for r in fails))

    def test_pinned_tree_missing_is_fail_with_fetch_fix(self):
        tmp = _pin_dir()
        try:
            work = os.path.join(tmp.name, "work")
            os.makedirs(work)
            results = doctor.check_pinned_trees(
                tmp.name, pin_path=os.path.join(tmp.name, "targets.pin"),
                work_dir=work)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "FAIL")
            self.assertIn("ensure_tree", results[0]["fix"])
        finally:
            tmp.cleanup()

    def test_pinned_tree_cached_is_pass(self):
        tmp = _pin_dir()
        try:
            work = os.path.join(tmp.name, "work")
            os.makedirs(os.path.join(work, "picorv32-" + "a" * 12))
            results = doctor.check_pinned_trees(
                tmp.name, pin_path=os.path.join(tmp.name, "targets.pin"),
                work_dir=work)
            self.assertEqual(results[0]["status"], "PASS")
        finally:
            tmp.cleanup()

    def test_suite_smoke_is_skip_with_full_command(self):
        res = doctor.check_suite_smoke()
        self.assertEqual(res["status"], "SKIP")
        self.assertIn("unittest", res["message"])

    def test_run_doctor_all_pass_exits_zero(self):
        tmp = _pin_dir()
        try:
            work = os.path.join(tmp.name, "work")
            os.makedirs(os.path.join(work, "picorv32-" + "a" * 12))
            results, rc = doctor.run_doctor(
                tmp.name,
                py_version=(3, 12, 0),
                which=lambda _name: "/usr/bin/verilator",
                run=lambda _cmd: (0, "Verilator 5.052\n"),
                env={"MODEL_API_KEY": "x"}, dotenv={},
                pin_path=os.path.join(tmp.name, "targets.pin"),
                work_dir=work)
            self.assertEqual(rc, 0)
            self.assertTrue(all(r["status"] in ("PASS", "SKIP")
                                for r in results))
            lines = [doctor.format_line(r) for r in results]
            self.assertTrue(any(l.startswith("PASS:") for l in lines))
        finally:
            tmp.cleanup()

    def test_run_doctor_missing_key_exits_one_with_fix(self):
        tmp = _pin_dir()
        try:
            results, rc = doctor.run_doctor(
                tmp.name,
                py_version=(3, 12, 0),
                which=lambda _name: None,
                env={}, dotenv={},
                pin_path=os.path.join(tmp.name, "targets.pin"),
                work_dir=os.path.join(tmp.name, "work"))
            self.assertEqual(rc, 1)
            fails = [r for r in results if r["status"] == "FAIL"]
            self.assertTrue(len(fails) >= 2)
            for r in fails:
                self.assertIn("fix", r)
                self.assertTrue(r["fix"].strip())
        finally:
            tmp.cleanup()


class TestDemo(unittest.TestCase):
    def test_pick_demo_case_from_fixture_picks_fastest_locked_green(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md",
                                         delete=False) as fh:
            fh.write("# board\n")
            fh.write("| picosoc-uart-div | locked | GREEN | r1 | 44.5 | 4 | 0 |\n")
            fh.write("| picorv32-fetch-misalign | locked | GREEN | r2 | 57.9 | 2 | 0 |\n")
            fh.write("| uart-tx-div | locked | GREEN | r3 | 62.0 | 2 | 0 |\n")
            fh.write("| fetch-highword | stress | GREEN | r4 | 52.6 | 2 | 0 |\n")
            path = fh.name
        try:
            self.assertEqual(doctor.pick_demo_case(path),
                             "picosoc-uart-div")
        finally:
            os.unlink(path)

    def test_pick_demo_case_real_file_is_documented_case(self):
        # Verified live from eval/results.md (locked GREEN times, Nov-2
        # board re-verify: uart-div 53.0s < fetch-misalign 57.9s) -- the
        # demo case below must stay in sync with the file, never a guess.
        self.assertEqual(doctor.pick_demo_case(), "picosoc-uart-div")
        self.assertEqual(doctor.DEMO_CASE, "picosoc-uart-div")

    def test_demo_no_key_fails_loud_without_loop_call(self):
        buf = io.StringIO()
        with mock.patch.object(doctor.loop, "main") as loop_main, \
                contextlib.redirect_stdout(buf):
            rc = doctor.run_demo(ROOT, env={}, dotenv={})
        self.assertNotEqual(rc, 0)
        loop_main.assert_not_called()
        self.assertIn("export MODEL_API_KEY", buf.getvalue())

    def test_demo_dotenv_only_key_still_fails_loud(self):
        # The loop reads os.environ only (agent/loop.py), never .env:
        # a .env-only key must fail the demo gate, not burn retries.
        buf = io.StringIO()
        with mock.patch.object(doctor.loop, "main") as loop_main, \
                contextlib.redirect_stdout(buf):
            rc = doctor.run_demo(ROOT, env={},
                                 dotenv={"MODEL_API_KEY": "k"})
        self.assertNotEqual(rc, 0)
        loop_main.assert_not_called()
        self.assertIn("export MODEL_API_KEY", buf.getvalue())

    def test_demo_triage_runs_demo_spec_with_cost_upfront(self):
        buf = io.StringIO()
        with mock.patch.object(doctor.loop, "main",
                               return_value=2) as loop_main, \
                contextlib.redirect_stdout(buf):
            rc = doctor.run_demo(ROOT, env={"MODEL_API_KEY": "k"},
                                 dotenv={})
        self.assertEqual(rc, 2)
        loop_main.assert_called_once()
        argv = loop_main.call_args[0][0]
        self.assertIn("--triage", argv)
        self.assertIn("--spec", argv)
        spec = argv[argv.index("--spec") + 1]
        self.assertTrue(spec.endswith("picosoc-uart-div.yaml"),
                        msg=spec)
        out = buf.getvalue()
        self.assertIn("$0.05", out)
        self.assertNotIn("MODEL_API_KEY=k", out)


class TestInitHere(unittest.TestCase):
    def test_here_writes_loadable_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            for fn in ("top.v", "tb.sv", "notes.txt"):
                with open(os.path.join(tmp, fn), "w",
                          encoding="utf-8") as fh:
                    fh.write("// stub\n")
            dest = doctor.draft_here_spec(tmp, force=False)
            self.assertTrue(os.path.isfile(dest))
            case = load_case_file(dest)
            self.assertEqual(sorted(case["files"]), ["tb.sv", "top.v"])
            self.assertTrue(str(case["oracle_pass"]).strip())

    def test_here_no_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.v"), "w",
                      encoding="utf-8") as fh:
                fh.write("// stub\n")
            first = doctor.draft_here_spec(tmp, force=False)
            with open(first, "a", encoding="utf-8") as fh:
                fh.write("# local edit\n")
            with self.assertRaises(watch.WatchError):
                doctor.draft_here_spec(tmp, force=False)
            second = doctor.draft_here_spec(tmp, force=True)
            self.assertEqual(first, second)
            with open(second, encoding="utf-8") as fh:
                self.assertNotIn("local edit", fh.read())

    def test_here_no_hdl_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(watch.WatchError):
                doctor.draft_here_spec(tmp, force=False)
            self.assertEqual(os.listdir(tmp), [])


class TestOnboardCli(unittest.TestCase):
    def test_doctor_help(self):
        proc = subprocess.run(
            [AFTERGLOW, "doctor", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("verilator", proc.stdout.lower())

    def test_demo_help_mentions_cost(self):
        proc = subprocess.run(
            [AFTERGLOW, "demo", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("$0.05", proc.stdout)

    def test_init_help_mentions_here(self):
        proc = subprocess.run(
            [AFTERGLOW, "init", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--here", proc.stdout)

    def test_top_help_lists_doctor_demo(self):
        proc = subprocess.run(
            [AFTERGLOW, "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("doctor", proc.stdout)
        self.assertIn("demo", proc.stdout)


if __name__ == "__main__":
    unittest.main()
