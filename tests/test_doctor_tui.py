"""Doctor-TUI dispatch tests: `afterglow doctor` honors should_use_tui.

Run: python3 tests/test_doctor_tui.py -v   (from repo root)

Stdlib only, no model calls, no real sim builds:

- dispatch: TTY + binary present + AFTERGLOW_NO_TUI unset -> execs
  `tui doctor`; AFTERGLOW_NO_TUI=1, missing binary, or non-TTY ->
  plain-text path (os.execv never called).
- byte-identical: non-TTY `afterglow doctor` stdout + exit code equal
  the pre-change fixture `tests/fixtures/doctor_plain_stdout.txt`
  byte-for-byte (fixture captured BEFORE the TUI dispatch existed;
  same-machine only: lines embed local paths/tool versions).

Env control: both the fixture capture and the test scrub
MODEL_API_KEY/SPARK_API_KEY/META_API_KEY from the environment (the
repo .env still provides the key, exactly as at capture time).
"""

import importlib.util
import io
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")
FIXTURE = os.path.join(ROOT, "tests", "fixtures",
                       "doctor_plain_stdout.txt")


def _load_afterglow():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("afterglow_cli_doctor_tui", AFTERGLOW)
    spec = importlib.util.spec_from_loader("afterglow_cli_doctor_tui",
                                           loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


MOD = _load_afterglow()

_SCRUB = ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY")


def _scrubbed_env(no_tui=None):
    env = dict(os.environ)
    for key in _SCRUB:
        env.pop(key, None)
    if no_tui is None:
        env.pop("AFTERGLOW_NO_TUI", None)
    else:
        env["AFTERGLOW_NO_TUI"] = no_tui
    return env


def _run_doctor_plain(extra_env=None):
    env = _scrubbed_env()
    if extra_env:
        env.update(extra_env)
    # Pipes: stdout is not a TTY -> must take the plain-text path even
    # with the release TUI binary present.
    return subprocess.run([AFTERGLOW, "doctor"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, cwd=ROOT, timeout=120)


class TestDoctorDispatch(unittest.TestCase):
    def _run_main(self, is_tty, no_tui_env, bin_exists, exec_side_effect):
        # Fake stdout: writes captured (not leaked), isatty scripted.
        # (redirect_stdout alone would shadow a patched isatty, so the
        # fake carries both.)
        fake = mock.MagicMock(wraps=io.StringIO())
        fake.isatty.return_value = is_tty
        with mock.patch.object(sys, "stdout", fake), \
             mock.patch.object(os.path, "isfile",
                               return_value=bin_exists), \
             mock.patch.dict(os.environ, {}, clear=False):
            if no_tui_env is None:
                os.environ.pop("AFTERGLOW_NO_TUI", None)
            else:
                os.environ["AFTERGLOW_NO_TUI"] = no_tui_env
            with mock.patch.object(os, "execv",
                                   side_effect=exec_side_effect) as execv:
                rc = MOD.main(["doctor"])
        return rc, execv

    def _main_with(self, is_tty, no_tui_env, bin_exists):
        boom = AssertionError("execv must not return")
        try:
            rc, execv = self._run_main(is_tty, no_tui_env, bin_exists,
                                       boom)
        except AssertionError as exc:
            if exc is boom:
                raise AssertionError(
                    "plain-text path must not exec (is_tty=%r, "
                    "no_tui=%r, bin=%r)"
                    % (is_tty, no_tui_env, bin_exists))
            raise
        return rc, execv

    def test_tty_dispatches_to_tui_doctor(self):
        _, execv = self._run_main(True, None, True, None)
        execv.assert_called_once_with(MOD.TUI_BIN,
                                      [MOD.TUI_BIN, "doctor"])

    def test_no_tui_env_escapes_to_plain(self):
        rc, _ = self._main_with(True, "1", True)
        self.assertEqual(rc, 0)

    def test_no_tui_env_empty_means_tui(self):
        _, execv = self._run_main(True, "", True, None)
        execv.assert_called_once_with(MOD.TUI_BIN,
                                      [MOD.TUI_BIN, "doctor"])

    def test_non_tty_stays_plain(self):
        rc, _ = self._main_with(False, None, True)
        self.assertEqual(rc, 0)

    def test_missing_binary_stays_plain(self):
        rc, _ = self._main_with(True, None, False)
        self.assertEqual(rc, 0)

    def test_doctor_help_mentions_checklist_escape(self):
        parser = MOD.build_parser()
        help_text = parser.format_help()
        self.assertIn("AFTERGLOW_NO_TUI", help_text)


class TestDoctorPlainByteIdentical(unittest.TestCase):
    def test_non_tty_output_matches_pre_change_fixture(self):
        with open(FIXTURE, "rb") as fh:
            expected = fh.read()
        proc = _run_doctor_plain()
        self.assertEqual(proc.returncode, 0,
                         msg="stderr: %r" % proc.stderr[:500])
        self.assertEqual(proc.stderr, b"",
                         msg="plain doctor must not write stderr")
        self.assertEqual(proc.stdout, expected,
                         msg="non-TTY `afterglow doctor` output must stay "
                             "byte-identical to the pre-TUI fixture")

    def test_no_tui_env_output_matches_fixture(self):
        with open(FIXTURE, "rb") as fh:
            expected = fh.read()
        proc = _run_doctor_plain(extra_env={"AFTERGLOW_NO_TUI": "1"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, expected)


if __name__ == "__main__":
    unittest.main()
