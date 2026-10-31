"""Task WATCH tests: `afterglow watch -- <cmd...>` capture-to-draft plumbing.

Run: python3 tests/test_watch.py -v   (from repo root)

Static fixtures only (fake sim shell scripts emitting logs), no model
calls, no real sim builds. loop.main is stubbed at the seam for the
chain-handoff tests (the handoff argv is the assertion -- the real
triage run needs a user-completed spec + live API, out of scope here):

- test_extract_rtl_files: .v/.sv argv entries (incl. `key=value` and
  comma forms) become the draft files list; absolute paths collapse
  to basenames, relative paths stay tree-relative; non-HDL args ignored.
- test_marker_pick_tohost: a `tohost: FAIL` line drafts
  oracle_pass "tohost: PASS" with a marker source.
- test_marker_pick_mismatch: a MISMATCH line drafts a MATCH pass marker.
- test_exit_code_fallback: a marker-less log still drafts a non-blank
  oracle_pass whose source records the exit code (fail closed, never
  blank, never guessed green).
- test_run_captures_exit_and_log: fixture script exit code + full
  (stdout+stderr) log are captured verbatim.
- test_run_missing_binary_fails_loud: a nonexistent command raises
  WatchError (no spec, no chain) instead of drafting garbage.
- test_draft_roundtrip_parses: the rendered YAML loads via
  afterglow_core.verilator.load_case_file with all required keys and
  a non-blank oracle_pass.
- test_yes_flow_writes_and_chains: --yes writes sim/targets/<name>.yaml
  under the given root and calls loop.main with
  [--spec <path> --triage --seed N]; the chain exit code propagates.
- test_confirm_decline_writes_nothing: answering `n` writes no file
  and never calls loop.main.
- test_refuses_overwrite_without_force: an existing spec stem is
  refused (naming --force); --force overwrites.
- test_cli_watch_help: `afterglow watch --help` exits 0 and documents
  --yes; `afterglow --help` lists watch.
"""
import os
import stat
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import watch
from afterglow_core import verilator as verilator_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")


def _write_script(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)


class TestExtractRtlFiles(unittest.TestCase):
    def test_extract_rtl_files(self):
        argv = ["verilator", "--binary", "--timing", "--top-module", "tb_foo",
                "rtl/uart.v", "+define+FOO=1", "--exe", "tb/foo_tb.sv",
                "other.c"]
        files = watch.extract_rtl_files(argv)
        self.assertEqual(files, ["rtl/uart.v", "tb/foo_tb.sv"])

    def test_extract_key_value_and_absolute(self):
        argv = ["tool", "--src=/abs/path/core.v", "a.v,b.sv"]
        files = watch.extract_rtl_files(argv)
        self.assertEqual(files, ["core.v", "a.v", "b.sv"])

    def test_extract_ignores_non_hdl(self):
        self.assertEqual(watch.extract_rtl_files(["./sim", "+seed=1"]), [])


class TestPickMarkers(unittest.TestCase):
    def test_marker_pick_tohost(self):
        log = ("cycle 1: fetch ok\n"
               "cycle 6: tohost: FAIL 0x1\n"
               "CYCLES=10\n")
        m = watch.pick_markers(log, 1)
        self.assertEqual(m["oracle_pass"], "tohost: PASS")
        self.assertEqual(m["oracle_trap"], "TRAP:")
        self.assertIn("tohost: FAIL", m["oracle_source"])
        self.assertIn("tohost: FAIL 0x1", m["fail_line"])

    def test_marker_pick_mismatch(self):
        log = "boot ok\nchecksum MISMATCH got=aa want=bb\n"
        m = watch.pick_markers(log, 1)
        self.assertIn("MATCH", m["oracle_pass"])
        self.assertNotIn("MISMATCH", m["oracle_pass"])
        self.assertTrue(m["oracle_pass"].strip())

    def test_exit_code_fallback(self):
        log = "hello world\nsim done\n"
        m = watch.pick_markers(log, 3)
        self.assertIsNone(m["fail_line"])
        self.assertTrue(m["oracle_pass"].strip())
        self.assertIn("3", m["oracle_source"])
        self.assertIn("exit", m["oracle_source"])


class TestRunCmd(unittest.TestCase):
    def test_run_captures_exit_and_log(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "fail_sim.sh")
            _write_script(script, 'echo "cycle 6: tohost: FAIL 0x1"\n'
                                   'echo "oops on stderr" >&2\n'
                                   'exit 2\n')
            rc, log = watch.run_cmd([script, "+seed=1"], timeout=30)
            self.assertEqual(rc, 2)
            self.assertIn("tohost: FAIL 0x1", log)
            self.assertIn("oops on stderr", log)

    def test_run_missing_binary_fails_loud(self):
        with self.assertRaises(watch.WatchError):
            watch.run_cmd(["/nonexistent/afterglow-watch-probe-xyz"],
                          timeout=30)


class TestDraftRoundtrip(unittest.TestCase):
    def test_draft_roundtrip_parses(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["./obj_dir/Vtb", "rtl/uart.v"]
            log = "cycle 6: tohost: FAIL 0x1\n"
            draft = watch.draft_spec("demo-watch", argv, log, 1)
            text = watch.render_yaml(draft)
            path = watch.write_spec(tmp, "demo-watch", text)
            self.assertTrue(path.endswith("sim/targets/demo-watch.yaml"))
            case = verilator_mod.load_case_file(path)
            for key in ("project", "files", "top", "tb", "anchor", "bug",
                        "gate", "oracle_pass"):
                self.assertIn(key, case)
            self.assertTrue(str(case["oracle_pass"]).strip())
            self.assertEqual(case["files"], ["rtl/uart.v"])


class TestConfirmAndChain(unittest.TestCase):
    def _fail_argv(self, tmp):
        script = os.path.join(tmp, "fail_sim.sh")
        _write_script(script, 'echo "cycle 6: tohost: FAIL 0x1"\nexit 1\n')
        return [script, "rtl/uart.v"]

    def test_yes_flow_writes_and_chains(self):
        import tempfile
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as tmp:
            argv = self._fail_argv(tmp)
            calls = []

            def fake_loop_main(loop_argv):
                calls.append(list(loop_argv))
                return 2

            with mock.patch.object(watch.loop, "main",
                                   side_effect=fake_loop_main):
                rc = watch.main(["--name", "demo", "--yes",
                                 "--", argv[0], argv[1]], root=tmp)
            self.assertEqual(rc, 2)
            spec_path = os.path.join(tmp, "sim", "targets", "demo.yaml")
            self.assertTrue(os.path.isfile(spec_path))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "--spec")
            self.assertEqual(calls[0][1], spec_path)
            self.assertIn("--triage", calls[0])

    def test_confirm_decline_writes_nothing(self):
        import tempfile
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as tmp:
            argv = self._fail_argv(tmp)
            with mock.patch.object(watch.loop, "main") as loop_main, \
                 mock.patch("builtins.input", return_value="n"):
                rc = watch.main(["--name", "demo",
                                 "--", argv[0], argv[1]], root=tmp)
            self.assertEqual(rc, 1)
            self.assertFalse(os.path.exists(
                os.path.join(tmp, "sim", "targets", "demo.yaml")))
            loop_main.assert_not_called()

    def test_refuses_overwrite_without_force(self):
        import tempfile
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as tmp:
            argv = self._fail_argv(tmp)
            base = ["--name", "demo", "--yes", "--",
                    argv[0], argv[1]]
            with mock.patch.object(watch.loop, "main",
                                   return_value=2):
                self.assertEqual(watch.main(base, root=tmp), 2)
            # Second run without --force must refuse loudly, no chain.
            with mock.patch.object(watch.loop, "main") as loop_main:
                rc = watch.main(base, root=tmp)
            self.assertEqual(rc, 1)
            loop_main.assert_not_called()
            # --force overwrites and chains again.
            with mock.patch.object(watch.loop, "main",
                                   return_value=2) as loop_main:
                rc = watch.main(["--name", "demo", "--yes", "--force",
                                 "--", argv[0], argv[1]], root=tmp)
            self.assertEqual(rc, 2)
            loop_main.assert_called_once()


class TestCliHelp(unittest.TestCase):
    def test_cli_watch_help(self):
        p = subprocess.run([AFTERGLOW, "watch", "--help"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertIn("--yes", p.stdout)
        self.assertIn("--", p.stdout)

    def test_cli_watch_decline_path(self):
        # Real `afterglow watch ... -- <cmd>` dispatch with piped `n`:
        # exercises the top-parser REMAINDER forwarding (the `--`
        # separator must not double up into the sim command), declines
        # the confirm, writes nothing, chains nothing, exits 1.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "cli_sim.sh")
            _write_script(script, 'echo "cycle 6: tohost: FAIL 0x1"\n'
                                   'exit 1\n')
            p = subprocess.run(
                [AFTERGLOW, "watch", "--name", "cli-decline-probe",
                 "--", script, "rtl/uart.v"],
                input="n\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=60, cwd=ROOT)
            self.assertEqual(p.returncode, 1)
            self.assertIn("aborted", p.stdout)
            self.assertFalse(os.path.exists(os.path.join(
                ROOT, "sim", "targets", "cli-decline-probe.yaml")))

    def test_cli_top_help_lists_watch(self):
        p = subprocess.run([AFTERGLOW, "--help"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertIn("watch", p.stdout)


if __name__ == "__main__":
    unittest.main()
