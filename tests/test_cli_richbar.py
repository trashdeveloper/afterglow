"""Task CLI-RICH-BAR tests: rich TUI progress bar with stdlib fallback.

Run: python3 tests/test_cli_richbar.py -v   (from repo root)

Stdlib only for the harness, no model calls, no real sim builds:
- (a) with rich present: make_bar advances without raising, writes to
  STDERR only (zero STDOUT bytes), supports context-manager use.
- (b) rich import blocked (ImportError via sys.modules stub): fallback
  output is byte-identical to today's Task-8 stdlib path (assert exact
  stderr bytes equal loop.progress_tick / loop.progress_done output).
- (c) non-TTY stderr or --quiet: zero bytes on both streams (unchanged).
- (d) attempt loop completes phases and finishes the bar
  (fixture-driven, no sim, no API): six fixed phase steps driven
  through loop.phase_tick + loop.phase_finish finish the bar.
"""

import contextlib
import io
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop
from agent import progress


def _has_rich():
    try:
        import importlib
        importlib.import_module("rich.progress")
        importlib.import_module("rich.console")
        return True
    except Exception:
        return False


HAS_RICH = _has_rich()


class _TtyStderr(io.StringIO):
    def isatty(self):
        return True


class _PipeStderr(io.StringIO):
    def isatty(self):
        return False


@contextlib.contextmanager
def _rich_blocked():
    """Simulate `import rich` raising ImportError via sys.modules stubs."""
    touched = ("rich", "rich.progress", "rich.console")
    saved = {key: sys.modules[key] for key in touched if key in sys.modules}
    for key in touched:
        sys.modules[key] = None
    try:
        yield
    finally:
        for key in touched:
            if key in saved:
                sys.modules[key] = saved[key]
            else:
                sys.modules.pop(key, None)


def _args(quiet=False):
    return types.SimpleNamespace(quiet=quiet)


@unittest.skipUnless(HAS_RICH, "rich not installed: rich-path test n/a")
class TestRichBar(unittest.TestCase):
    def test_bar_advances_on_stderr_only(self):
        # Rich present + TTY + not --quiet: advance/finish work, stderr
        # only, zero stdout bytes.
        args = _args(quiet=False)
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            bar = progress.make_bar(6, "attempt 1", args)
            self.assertTrue(getattr(bar, "is_rich", False))
            bar.advance()
            bar.advance(phase="collect")
            bar.advance(2)
            bar.finish()
        self.assertEqual(out.getvalue(), "")
        self.assertNotEqual(err.getvalue(), "")

    def test_bar_context_manager(self):
        # `with make_bar(...)` advances and finishes without raising,
        # stderr only.
        args = _args(quiet=False)
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            with progress.make_bar(6, "attempt 1", args) as bar:
                bar.advance()
                bar.advance()
        self.assertEqual(out.getvalue(), "")
        self.assertNotEqual(err.getvalue(), "")


class TestStdlibFallback(unittest.TestCase):
    def test_blocked_rich_advance_matches_progress_tick_bytes(self):
        # Rich blocked: one fallback advance emits byte-identical output
        # to today's Task-8 stdlib progress_tick for the same inputs.
        args = _args(quiet=False)
        with _rich_blocked():
            out, err = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                loop.progress_tick(args, "collect", 1, 3, 42.0)
            want_err = err.getvalue()
            want_out = out.getvalue()
            out2, err2 = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out2), \
                    contextlib.redirect_stderr(err2):
                bar = progress.make_bar(6, "attempt 1", args)
                self.assertFalse(getattr(bar, "is_rich", False))
                bar.advance(phase="collect", attempt=1, total=3,
                            elapsed_s=42.0)
            self.assertEqual(out2.getvalue(), want_out)
            self.assertEqual(err2.getvalue(), want_err)
            self.assertTrue(err2.getvalue().startswith("\r"))
            self.assertIn("[collect] attempt 1/3", err2.getvalue())

    def test_blocked_rich_finish_matches_progress_done_bytes(self):
        # Rich blocked: fallback finish emits byte-identical output to
        # today's stdlib progress_done (one newline).
        args = _args(quiet=False)
        with _rich_blocked():
            out, err = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                loop.progress_done(args)
            want = err.getvalue()
            out2, err2 = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out2), \
                    contextlib.redirect_stderr(err2):
                bar = progress.make_bar(6, "attempt 1", args)
                bar.advance(phase="collect", attempt=1, total=3,
                            elapsed_s=1.0)
                bar.finish()
            self.assertEqual(err2.getvalue()[-len(want):], want)
            self.assertEqual(want, "\n")

    def test_fallback_context_manager(self):
        # Fallback bar also supports context-manager use.
        args = _args(quiet=False)
        with _rich_blocked():
            out, err = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                with progress.make_bar(6, "attempt 1", args) as bar:
                    bar.advance(phase="red", attempt=1, total=3,
                                elapsed_s=0.0)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("[red] attempt 1/3", err.getvalue())
            self.assertTrue(err.getvalue().endswith("\n"))


class TestQuietAndNonTty(unittest.TestCase):
    def test_pipe_stderr_zero_bytes(self):
        # Non-TTY stderr (pipes/CI): zero bytes either stream, rich or
        # fallback -- unchanged behavior.
        for quiet in (False, True):
            args = _args(quiet=quiet)
            out, err = io.StringIO(), _PipeStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                bar = progress.make_bar(6, "attempt 1", args)
                bar.advance()
                bar.advance(phase="diagnose")
                bar.finish()
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), "")

    def test_quiet_on_tty_zero_bytes(self):
        # --quiet on a TTY: zero bytes either stream -- unchanged.
        args = _args(quiet=True)
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            bar = progress.make_bar(6, "attempt 1", args)
            bar.advance()
            bar.advance(phase="diagnose")
            bar.finish()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


class TestAttemptLoopBar(unittest.TestCase):
    def test_fixture_attempt_completes_six_phases_and_finishes(self):
        # Fixture-driven attempt loop (no sim, no API): the six fixed
        # phase steps advance one bar to completion and finish it.
        args = _args(quiet=False)
        phases = ["red", "collect", "diagnose", "patch", "resim",
                  "verify"]
        with _rich_blocked():
            out, err = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                bar = progress.make_bar(len(phases), "attempt 1", args)
                for i, phase in enumerate(phases):
                    bar.advance(phase=phase, attempt=1, total=3,
                                elapsed_s=float(i))
                bar.finish()
            text = err.getvalue()
            self.assertEqual(out.getvalue(), "")
            for phase in phases:
                self.assertIn("[%s] attempt 1/3" % phase, text)
            self.assertTrue(text.endswith("\n"))

    def test_loop_helpers_route_phases_through_bar(self):
        # loop.phase_tick advances the bar once per phase and
        # loop.phase_finish finishes it (fixture FakeBar, no sim/API).
        seen = []

        class FakeBar:
            def advance(self, n=1, **kw):
                seen.append(("advance", kw.get("phase")))
                return None

            def finish(self):
                seen.append(("finish", None))
                return None

        args = _args(quiet=False)
        bar = FakeBar()
        t_start = __import__("time").monotonic()
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            for phase in ("red", "collect", "diagnose", "patch",
                          "resim", "verify"):
                loop.phase_tick(args, bar, phase, 1, 3, t_start)
            loop.phase_finish(args, bar)
        self.assertEqual(
            [kind for kind, _ in seen],
            ["advance"] * 6 + ["finish"])
        self.assertEqual(
            [name for _, name in seen[:6]],
            ["red", "collect", "diagnose", "patch", "resim", "verify"])
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
