"""Task GRADIENT-BAR tests: REAL gradient bar (opencode-style) in progress_rich.

Run: python3 tests/test_progress_gradient.py -v   (from repo root)

Covers agent/progress_rich.py GradientBarColumn + blend_hex:
- blend_hex endpoints / midpoint exact hex (pure stdlib, no rich needed).
- GradientBarColumn.render(task) at 0%/50%/100% on a forced-truecolor
  Console: the partial bar's ANSI bytes contain >=3 distinct 24-bit color
  codes spanning cyan (#00d7ff) -> magenta (#ff00ff); the 100% bar uses
  the bright-green finished style.
- StdlibBar byte-identity vs loop.progress_tick / loop.progress_done is
  unchanged (fallback untouched).
- rich-missing (ImportError stub) and non-TTY stderr still fail closed:
  no rich bar, zero bytes where the Task-8 rule requires silence.
"""

import contextlib
import io
import os
import re
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import progress_rich


def _has_rich():
    try:
        import importlib
        importlib.import_module("rich.progress")
        importlib.import_module("rich.console")
        return True
    except Exception:
        return False


HAS_RICH = _has_rich()

CYAN = "#00d7ff"
MAGENTA = "#ff00ff"


class _TtyStderr(io.StringIO):
    def isatty(self):
        return True


class _PipeStderr(io.StringIO):
    def isatty(self):
        return False


@contextlib.contextmanager
def _rich_blocked():
    """Simulate `import rich` raising ImportError via sys.modules stubs."""
    touched = ("rich", "rich.progress", "rich.console", "rich.text",
               "rich.style")
    saved = {key: sys.modules[key] for key in touched if key in sys.modules}
    for key in touched:
        sys.modules[key] = None
    try:
        # Drop any cached lazy class so the blocked import is re-exercised.
        saved_cls = progress_rich.__dict__.pop("GradientBarColumn", None)
        try:
            yield
        finally:
            if saved_cls is not None:
                progress_rich.__dict__["GradientBarColumn"] = saved_cls
    finally:
        for key in touched:
            if key in saved:
                sys.modules[key] = saved[key]
            else:
                sys.modules.pop(key, None)


def _render_ansi(column, completed, total=100, width=40):
    """Render column for a fake task; return (Text, ansi_bytes)."""
    from rich.console import Console
    from rich.progress import Progress
    progress = Progress()
    task_id = progress.add_task("attempt 1", total=total)
    progress.update(task_id, completed=completed)
    task = next(t for t in progress.tasks if t.id == task_id)
    text = column.render(task)
    buf = io.StringIO()
    # NOTE: the sandbox exports NO_COLOR=1, so force no_color=False to
    # capture the real ANSI bytes a live terminal would see.
    console = Console(file=buf, force_terminal=True, width=width + 20,
                      color_system="truecolor", no_color=False)
    console.print(text, end="")
    return text, buf.getvalue()


def _truecolor_codes(ansi):
    return re.findall(r"38;2;(\d+);(\d+);(\d+)", ansi)


class TestBlendHex(unittest.TestCase):
    def test_endpoints_exact(self):
        self.assertEqual(
            progress_rich.blend_hex(CYAN, MAGENTA, 0.0).lower(), CYAN)
        self.assertEqual(
            progress_rich.blend_hex(CYAN, MAGENTA, 1.0).lower(), MAGENTA)

    def test_midpoint_exact(self):
        # r: (0+255)/2=128 -> 0x80; g: (215+0)/2=108 -> 0x6c; b: 255 -> 0xff
        self.assertEqual(
            progress_rich.blend_hex(CYAN, MAGENTA, 0.5).lower(), "#806cff")

    def test_clamps_and_accepts_variants(self):
        self.assertEqual(
            progress_rich.blend_hex(CYAN, MAGENTA, -0.5).lower(), CYAN)
        self.assertEqual(
            progress_rich.blend_hex(CYAN, MAGENTA, 1.5).lower(), MAGENTA)
        # Uppercase / no-hash inputs normalize to lowercase #rrggbb.
        self.assertEqual(
            progress_rich.blend_hex("00D7FF", "FF00FF", 0.5).lower(),
            "#806cff")


@unittest.skipUnless(HAS_RICH, "rich not installed: gradient test n/a")
class TestGradientColumn(unittest.TestCase):
    def test_half_bar_carries_cyan_to_magenta_gradient(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        _, ansi = _render_ansi(column, 50, total=100)
        codes = _truecolor_codes(ansi)
        self.assertGreaterEqual(
            len(set(codes)), 3,
            "50%% bar should carry >=3 distinct 24-bit colors, got: %r"
            % (codes,))
        # Spans cyan -> magenta: first completed segment is cyan, the
        # completed run ends on magenta.
        self.assertEqual(codes[0], ("0", "215", "255"))
        self.assertIn(("255", "0", "255"), set(codes))

    def test_zero_bar_renders_without_gradient(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 0, total=100)
        self.assertTrue(ansi, "0% bar must still emit ANSI bytes")
        codes = _truecolor_codes(ansi)
        self.assertNotIn(("0", "215", "255"), set(codes))
        self.assertNotIn(("255", "0", "255"), set(codes))
        self.assertEqual(text.plain.count("\u2501"), 0)

    def test_full_bar_uses_bright_green_finished_style(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 100, total=100)
        self.assertTrue(ansi, "100% bar must still emit ANSI bytes")
        # Finished bar is one Text with a bright-green base style (no
        # per-segment spans needed at 100%).
        self.assertIn("bright_green", str(text.style))

    def test_is_progress_column_subclass(self):
        from rich.progress import ProgressColumn
        self.assertTrue(
            issubclass(progress_rich.GradientBarColumn, ProgressColumn))


@unittest.skipUnless(HAS_RICH, "rich not installed: rich-path test n/a")
class TestRichBarWiring(unittest.TestCase):
    def test_rich_bar_uses_gradient_column(self):
        args = types.SimpleNamespace(quiet=False, verbose=False)
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            bar = progress_rich.make_bar(6, "attempt 1", args)
            self.assertTrue(getattr(bar, "is_rich", False))
            columns = list(bar._progress.columns)
            self.assertTrue(
                any(isinstance(c, progress_rich.GradientBarColumn)
                    for c in columns),
                "RichBar must wire GradientBarColumn, got: %r" % (columns,))
            bar.advance()
            bar.finish()
        self.assertEqual(out.getvalue(), "")


class TestStdlibFallbackUntouched(unittest.TestCase):
    def test_advance_byte_identical_to_progress_tick(self):
        from agent import loop
        args = types.SimpleNamespace(quiet=False, verbose=False)
        with _rich_blocked():
            out, err = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                loop.progress_tick(args, "collect", 1, 3, 42.0)
            want_err = err.getvalue()
            out2, err2 = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out2), \
                    contextlib.redirect_stderr(err2):
                bar = progress_rich.make_bar(6, "attempt 1", args)
                self.assertFalse(getattr(bar, "is_rich", True))
                bar.advance(phase="collect", attempt=1, total=3,
                            elapsed_s=42.0)
            self.assertEqual(out2.getvalue(), "")
            self.assertEqual(err2.getvalue(), want_err)

    def test_finish_byte_identical_to_progress_done(self):
        from agent import loop
        args = types.SimpleNamespace(quiet=False, verbose=False)
        with _rich_blocked():
            out, err = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                loop.progress_done(args)
            want = err.getvalue()
            out2, err2 = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out2), \
                    contextlib.redirect_stderr(err2):
                bar = progress_rich.make_bar(6, "attempt 1", args)
                bar.advance(phase="collect", attempt=1, total=3,
                            elapsed_s=1.0)
                bar.finish()
            self.assertEqual(err2.getvalue()[-len(want):], want)
            self.assertEqual(want, "\n")

    def test_rich_missing_fails_closed(self):
        args = types.SimpleNamespace(quiet=False, verbose=False)
        with _rich_blocked():
            out, err = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                bar = progress_rich.make_bar(6, "attempt 1", args)
                self.assertFalse(getattr(bar, "is_rich", True))
            with self.assertRaises(ImportError):
                progress_rich.GradientBarColumn  # noqa: B018

    def test_non_tty_zero_bytes(self):
        for quiet in (False, True):
            args = types.SimpleNamespace(quiet=quiet, verbose=False)
            out, err = io.StringIO(), _PipeStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                bar = progress_rich.make_bar(6, "attempt 1", args)
                self.assertFalse(getattr(bar, "is_rich", True))
                bar.advance()
                bar.advance(phase="diagnose")
                bar.finish()
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
