"""Task TUI-DEFAULT tests: `./afterglow run` dispatches to the Rust TUI
on a TTY by default (one program, same dir, TUI default).

Run: python3 tests/test_tui_default.py -v   (from repo root)

Stdlib only, no model calls, no real sim builds, no TUI exec:
- test_tty_no_flags_binary_present: TTY + no --emit-events +
  AFTERGLOW_NO_TUI unset + binary exists -> True.
- test_non_tty: stdout not a TTY -> False (CI-safe pure-Python loop).
- test_emit_events_disables: --emit-events jsonl was passed -> False
  (this check alone breaks the TUI->backend exec cycle: the TUI
  always spawns its backend with --emit-events jsonl).
- test_no_tui_env_disables: AFTERGLOW_NO_TUI=1 -> False.
- test_no_tui_env_empty_means_enabled: AFTERGLOW_NO_TUI="" -> True
  (unset/empty both mean "no escape requested").
- test_missing_binary: release binary absent -> False (pure-Python
  loop, no error -- CI-safe default).
- test_tui_backend_spawn_has_emit_events: tui-rs/src/main.rs spawns
  its backend with --emit-events jsonl (the recursion-guard
  invariant the wrapper relies on).
- test_run_help_mentions_tui_default: `afterglow run --help` (and the
  Usage docstring) states the TTY-default-TUI + AFTERGLOW_NO_TUI
  escape.
"""

import importlib.util
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")
TUI_MAIN_RS = os.path.join(ROOT, "tui-rs", "src", "main.rs")
TUI_BIN = os.path.join(ROOT, "tui-rs", "target", "release", "tui")


def _load_afterglow():
    # The `afterglow` entry has no .py suffix, so the default
    # extension-based loader finds nothing: bind a source loader
    # explicitly (same pattern as test_cli_polish.py).
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("afterglow_cli_tui", AFTERGLOW)
    spec = importlib.util.spec_from_loader("afterglow_cli_tui", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


MOD = _load_afterglow()


def _decide(is_tty, emit_events=None, no_tui_env=None, tui_bin_exists=True):
    return MOD.should_use_tui(
        is_tty=is_tty,
        emit_events=emit_events,
        no_tui_env=no_tui_env,
        tui_bin_exists=tui_bin_exists,
    )


class TestShouldUseTui(unittest.TestCase):
    def test_tty_no_flags_binary_present(self):
        self.assertTrue(_decide(True, None, None, True))

    def test_non_tty(self):
        self.assertFalse(_decide(False, None, None, True))

    def test_emit_events_disables(self):
        # The recursion guard: TUI -> backend always carries
        # --emit-events jsonl, so the backend never re-execs the TUI.
        self.assertFalse(_decide(True, "jsonl", None, True))

    def test_no_tui_env_disables(self):
        self.assertFalse(_decide(True, None, "1", True))

    def test_no_tui_env_empty_means_enabled(self):
        self.assertTrue(_decide(True, None, "", True))

    def test_missing_binary(self):
        # No release binary: pure-Python loop, no error (CI-safe).
        self.assertFalse(_decide(True, None, None, False))


class TestTuiBackendSpawnFlag(unittest.TestCase):
    def test_tui_backend_spawn_has_emit_events(self):
        # Invariant the wrapper's recursion guard relies on: the TUI
        # spawns `<repo-root>/afterglow run <spec> --emit-events jsonl`.
        with open(TUI_MAIN_RS, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('.arg("--emit-events")', src)
        self.assertIn('"jsonl"', src)


class TestTuiDocs(unittest.TestCase):
    def test_run_help_mentions_tui_default(self):
        parser = MOD.build_parser()
        help_text = parser.format_help()
        self.assertIn("AFTERGLOW_NO_TUI", help_text)

    def test_usage_docstring_mentions_tui_default(self):
        self.assertIn("AFTERGLOW_NO_TUI", MOD.__doc__)
        self.assertIn("TUI", MOD.__doc__)


class TestDemoTuiDispatch(unittest.TestCase):
    def test_demo_tui_argv(self):
        # Pure helper: demo dispatches the TUI with the fastest-case
        # spec + seed 42 + --triage (patch withheld, same as demo).
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "eval"))
            with open(os.path.join(d, "eval", "results.md"), "w") as fh:
                fh.write("| picosoc-uart-div | GREEN | 44.5 | 4 |\n")
            argv = MOD.demo_tui_argv(root=d, tui_bin="/tmp/tui")
        self.assertEqual(
            argv,
            ["/tmp/tui",
             os.path.join(d, "sim", "targets", "picosoc-uart-div.yaml"),
             "--seed", "42", "--triage"])

    def test_demo_branch_dispatches_via_should_use_tui(self):
        # main()'s demo branch must consult should_use_tui + execv
        # (same TTY-default contract as run/doctor).
        import inspect
        src = inspect.getsource(MOD.main)
        demo_branch = src.split('"demo"')[1].split('"watch"')[0]
        self.assertIn("should_use_tui", demo_branch)
        self.assertIn("os.execv", demo_branch)

    def test_demo_tui_branch_prints_no_notice(self):
        # The TUI frame shows est. cost in its own footer; the wrapper
        # must not print a cost-notice line before exec (clean frame
        # from row 0).
        src = pathlib.Path("afterglow").read_text()
        self.assertNotIn("burns ONE model diagnosis", src)


if __name__ == "__main__":
    unittest.main()
