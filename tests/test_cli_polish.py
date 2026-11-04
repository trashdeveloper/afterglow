"""Task CLI-POLISH tests: clean errors, stderr progress, simpler run command.

Run: python3 tests/test_cli_polish.py -v   (from repo root)

Stdlib only, no model calls, no real sim builds:
- (a) unexpected exception in the run path -> single-line
  `afterglow: error: <message>` on STDERR, exit != 0, no traceback text
  without --debug; with --debug the traceback is fully available.
- (b) progress display writes to STDERR only (zero STDOUT bytes), shows
  phase + attempt counter + elapsed, rewrites one line via carriage
  return; disabled when stderr is not a TTY; --quiet suppresses it.
- (c) `afterglow run sim/targets/<stem>.yaml` (positional spec) resolves
  identically to `--spec <path>`; auto run-id matches the family pattern
  `<case>-YYYYMMDDTHHMMSSZ` when --run-id is absent.
"""

import contextlib
import importlib.util
import io
import os
import re
import shutil
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")
SPEC_YAML = os.path.join(ROOT, "sim", "targets", "uart-start-half.yaml")


def _load_afterglow():
    # The `afterglow` entry has no .py suffix, so the default
    # extension-based loader finds nothing: bind a source loader
    # explicitly.
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("afterglow_cli", AFTERGLOW)
    spec = importlib.util.spec_from_loader("afterglow_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestCleanError(unittest.TestCase):
    def test_unexpected_exception_is_single_line_no_traceback(self):
        # An unexpected failure in the run path must NOT dump a raw
        # traceback on stdout: single clean line on stderr, exit != 0,
        # and the real message must survive in that line.
        run_id = "test-polish-err-line"
        self.addCleanup(shutil.rmtree,
                        os.path.join(ROOT, "runs", run_id), True)
        real_ensure = loop.ensure_dirs

        def boom(_path):
            raise RuntimeError("kaboom-synth-4815")

        loop.ensure_dirs = boom
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = loop.main(["--spec", SPEC_YAML, "--seed", "42",
                                "--run-id", run_id])
        finally:
            loop.ensure_dirs = real_ensure
        self.assertNotEqual(rc, 0)
        self.assertIn("afterglow: error:", err.getvalue())
        self.assertIn("kaboom-synth-4815", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertNotIn("Traceback", out.getvalue())
        # Single line: no multi-line dump on either stream.
        self.assertLessEqual(len(err.getvalue().strip().splitlines()), 2)

    def test_debug_reraises_with_traceback(self):
        # --debug must reproduce today's full-traceback behavior exactly.
        run_id = "test-polish-err-debug"
        self.addCleanup(shutil.rmtree,
                        os.path.join(ROOT, "runs", run_id), True)
        real_ensure = loop.ensure_dirs

        def boom(_path):
            raise RuntimeError("kaboom-synth-4816")

        loop.ensure_dirs = boom
        try:
            with self.assertRaises(RuntimeError):
                loop.main(["--spec", SPEC_YAML, "--seed", "42",
                           "--run-id", run_id, "--debug"])
        finally:
            loop.ensure_dirs = real_ensure

    def test_cli_entry_wraps_unexpected_exception(self):
        # The `afterglow` entry itself wraps unexpected dispatch failures
        # the same way (clean stderr line, return != 0, no traceback).
        mod = _load_afterglow()
        real_main = loop.main

        def boom(_argv):
            raise RuntimeError("kaboom-synth-4817")

        loop.main = boom
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = mod.main(["run", "--spec", SPEC_YAML])
        finally:
            loop.main = real_main
        self.assertNotEqual(rc, 0)
        self.assertIn("afterglow: error:", err.getvalue())
        self.assertIn("kaboom-synth-4817", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertNotIn("Traceback", out.getvalue())


class _TtyStderr(io.StringIO):
    def isatty(self):
        return True


class _PipeStderr(io.StringIO):
    def isatty(self):
        return False


class TestProgressDisplay(unittest.TestCase):
    def test_progress_goes_to_stderr_only(self):
        # Phase + attempt counter + elapsed on stderr, carriage-return
        # rewritten single line; zero bytes added to stdout.
        args = types.SimpleNamespace(quiet=False)
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            loop.progress_tick(args, "collect", 1, 3, 42.0)
        text = err.getvalue()
        self.assertIn("collect", text)
        self.assertIn("1/3", text)
        self.assertIn("42s", text)
        self.assertTrue(text.startswith("\r"))
        self.assertEqual(out.getvalue(), "")

    def test_progress_suppressed_when_not_a_tty(self):
        # CI/tests capture pipes (not a TTY): byte-identical streams,
        # i.e. zero progress bytes on either stream.
        args = types.SimpleNamespace(quiet=False)
        out, err = io.StringIO(), _PipeStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            loop.progress_tick(args, "collect", 1, 3, 42.0)
            loop.progress_done(args)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_quiet_suppresses_progress_on_tty(self):
        args = types.SimpleNamespace(quiet=True)
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            loop.progress_tick(args, "diagnose", 2, 3, 7.0)
            loop.progress_done(args)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_fast_run_path_adds_no_stdout_bytes(self):
        # End to end on a fast RED path (unknown stem, no sim/model):
        # stdout stays empty and stderr carries no progress rewrites.
        run_id = "test-polish-noprog"
        self.addCleanup(shutil.rmtree,
                        os.path.join(ROOT, "runs", run_id), True)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from tests.test_spec_run import _write_valid_case_yaml
            spec_path = _write_valid_case_yaml(tmp, "stranger-zz9")
            out, err = io.StringIO(), _PipeStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = loop.main(["--spec", spec_path, "--seed", "42",
                                "--run-id", run_id, "--quiet"])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn("\r", err.getvalue())


class TestWonderPassthrough(unittest.TestCase):
    def test_parser_accepts_wonder(self):
        mod = _load_afterglow()
        parser = mod.build_parser()
        args = parser.parse_args(
            ["run", "sim/targets/uart-start-half.yaml", "--wonder"])
        self.assertTrue(args.wonder)

    def test_wonder_defaults_off(self):
        mod = _load_afterglow()
        parser = mod.build_parser()
        args = parser.parse_args(
            ["run", "sim/targets/uart-start-half.yaml"])
        self.assertFalse(args.wonder)


class TestPositionalSpec(unittest.TestCase):
    def test_positional_resolves_identically_to_spec_flag(self):
        mod = _load_afterglow()
        parser = mod.build_parser()
        via_flag = parser.parse_args(
            ["run", "--spec", "sim/targets/uart-start-half.yaml"])
        via_pos = parser.parse_args(
            ["run", "sim/targets/uart-start-half.yaml"])
        self.assertEqual(mod.resolve_run_spec(via_pos),
                         mod.resolve_run_spec(via_flag))
        self.assertEqual(mod.resolve_run_spec(via_pos),
                         "sim/targets/uart-start-half.yaml")

    def test_spec_flag_still_works_as_deprecated_alias(self):
        mod = _load_afterglow()
        parser = mod.build_parser()
        args = parser.parse_args(
            ["run", "--spec", "sim/targets/uart-start-half.yaml"])
        self.assertEqual(mod.resolve_run_spec(args),
                         "sim/targets/uart-start-half.yaml")

    def test_auto_run_id_matches_family_pattern(self):
        rid = loop.default_run_id("uart-start-half")
        self.assertRegex(
            rid, r"^uart-start-half-\d{8}T\d{6}Z$")
        # Explicit --run-id still passes through untouched.
        parser_args = loop.parse_args(
            ["--spec", SPEC_YAML, "--run-id", "pinned-123"])
        self.assertEqual(parser_args.run_id, "pinned-123")
        # Absent --run-id means auto (None -> generated in main).
        auto_args = loop.parse_args(["--spec", SPEC_YAML])
        self.assertIsNone(auto_args.run_id)


if __name__ == "__main__":
    unittest.main()
