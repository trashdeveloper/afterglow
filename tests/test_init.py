"""Dev-happiness track tests: `afterglow init` environment checks + scaffold.

Run: python3 tests/test_init.py -v   (from repo root)

No live API calls, no keys, no real Verilator builds -- all seams are
injected (PATH lookup, env dict, filesystem tmp dirs):

- verilator check: missing binary -> FAIL line names the exact fix
  (apt/brew install), never a traceback.
- key check: no MODEL_API_KEY/SPARK_API_KEY/META_API_KEY in env or .env
  -> FAIL line names `export MODEL_API_KEY=...`; key in .env counts.
- pins check: missing/unreadable foundation.pin or targets.pin -> FAIL
  naming the file; readable pins -> ok.
- scaffold: init writes an example CaseSpec YAML under sim/targets/
  from the template (never overwrites without --force) and the written
  file loads via afterglow_core.verilator.load_spec.
- cli: `afterglow init --help` documents the checks; `afterglow --help`
  lists init.
"""
import os
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import initenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")


class TestInitChecks(unittest.TestCase):
    def test_verilator_missing_is_fail_not_traceback(self):
        res = initenv.check_verilator(which=lambda _name: None)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("verilator", res["message"].lower())
        self.assertIn("install", res["message"].lower())

    def test_verilator_present_is_ok(self):
        res = initenv.check_verilator(which=lambda _name: "/usr/bin/verilator")
        self.assertEqual(res["status"], "ok")

    def test_key_missing_is_fail_with_export_hint(self):
        res = initenv.check_api_key(env={}, dotenv={})
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("MODEL_API_KEY", res["message"])

    def test_key_in_env_is_ok(self):
        res = initenv.check_api_key(env={"MODEL_API_KEY": "x"}, dotenv={})
        self.assertEqual(res["status"], "ok")

    def test_key_in_dotenv_counts(self):
        res = initenv.check_api_key(env={}, dotenv={"SPARK_API_KEY": "x"})
        self.assertEqual(res["status"], "ok")

    def test_pins_missing_is_fail_naming_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = initenv.check_pins(tmp)
            fails = [r for r in results if r["status"] == "FAIL"]
            self.assertTrue(fails)
            self.assertTrue(any("foundation.pin" in r["message"]
                                for r in fails))

    def test_pins_readable_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("foundation.pin", "targets.pin"):
                with open(os.path.join(tmp, name), "w",
                          encoding="utf-8") as fh:
                    fh.write("pin content\n")
            results = initenv.check_pins(tmp)
            self.assertTrue(all(r["status"] == "ok" for r in results))

    def test_scaffold_writes_loadable_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = initenv.scaffold_example(tmp, force=False)
            self.assertTrue(os.path.isfile(dest))
            from afterglow_core.verilator import load_spec
            spec = load_spec(dest)
            self.assertTrue(str(spec.oracle_pass).strip())

    def test_scaffold_no_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = initenv.scaffold_example(tmp, force=False)
            with open(first, "a", encoding="utf-8") as fh:
                fh.write("# local edit\n")
            second = initenv.scaffold_example(tmp, force=False)
            self.assertEqual(first, second)
            with open(second, encoding="utf-8") as fh:
                self.assertIn("local edit", fh.read())
            forced = initenv.scaffold_example(tmp, force=True)
            self.assertEqual(forced, first)
            with open(forced, encoding="utf-8") as fh:
                self.assertNotIn("local edit", fh.read())


class TestInitCli(unittest.TestCase):
    def test_init_help_documents_checks(self):
        proc = subprocess.run(
            [AFTERGLOW, "init", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("verilator", proc.stdout.lower())
        self.assertIn("MODEL_API_KEY", proc.stdout)

    def test_top_help_lists_init(self):
        proc = subprocess.run(
            [AFTERGLOW, "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("init", proc.stdout)
        self.assertIn("explain", proc.stdout)
        self.assertIn("run", proc.stdout)


if __name__ == "__main__":
    unittest.main()
