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
MID = "#7a5cff"
MAGENTA = "#ff00ff"
WHITE = "#ffffff"

# opencode dark palette (verbatim from
# packages/tui/src/theme/assets/opencode.json): the filled run ramps
# blue -> purple -> peach; the glow head lerps toward light peach.
BLUE = "#5c9cf5"
PURPLE = "#9d7cd8"
PEACH = "#fab283"
LIGHT_PEACH = "#ffc09f"

EIGHTHS = ("▏", "▎", "▍", "▌", "▋", "▊", "▉")
FULL = "█"
TRACK = "─"


def _filled_and_track_counts(text):
    plain = text.plain
    filled = sum(plain.count(c) for c in (FULL,) + EIGHTHS)
    return filled, plain.count(TRACK)


def _rgb_tuple(hex_color):
    """``#rrggbb`` -> ``("r", "g", "b")`` decimal strings (ANSI form)."""
    text = hex_color.lstrip("#")
    return (str(int(text[0:2], 16)), str(int(text[2:4], 16)),
            str(int(text[4:6], 16)))


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
    def test_half_bar_carries_blue_to_peach_sweep(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        _, ansi = _render_ansi(column, 50, total=100)
        codes = _truecolor_codes(ansi)
        self.assertGreaterEqual(
            len(set(codes)), 3,
            "50%% bar should carry >=3 distinct 24-bit colors, got: %r"
            % (codes,))
        # Spans blue -> peach: the first filled cell is the blue ramp
        # start lifted slightly by the echo tail (pinned via the
        # independently tested primitives, not the bar itself).
        self.assertEqual(
            codes[0],
            _rgb_tuple(progress_rich.blend_hex(
                BLUE, LIGHT_PEACH,
                max(progress_rich.intensity_at(0, 19.0),
                    0.5 * progress_rich.intensity_at(0, 11.0)))))
        text, _ = _render_ansi(column, 50, total=100)
        filled, _ = _filled_and_track_counts(text)
        penultimate = tuple(int(c) for c in codes[filled - 2])
        for got, want in zip(penultimate, (250, 178, 131)):
            self.assertLessEqual(abs(got - want), 32)

    def test_zero_bar_renders_without_gradient(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 0, total=100)
        self.assertTrue(ansi, "0% bar must still emit ANSI bytes")
        codes = _truecolor_codes(ansi)
        self.assertNotIn(("92", "156", "245"), set(codes))
        self.assertNotIn(("250", "178", "131"), set(codes))
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


class TestThreeStopRamp(unittest.TestCase):
    def test_gradient_endpoints_exact(self):
        self.assertEqual(
            progress_rich.gradient_hex(0.0).lower(), BLUE)
        self.assertEqual(
            progress_rich.gradient_hex(1.0).lower(), PEACH)

    def test_gradient_midpoint_exact(self):
        # Three-stop ramp hits purple at t=0.5 exactly.
        self.assertEqual(
            progress_rich.gradient_hex(0.5).lower(), PURPLE)

    def test_gradient_quarter_hits_first_leg(self):
        # t=0.25 is halfway down the blue -> purple leg.
        self.assertEqual(
            progress_rich.gradient_hex(0.25).lower(),
            progress_rich.blend_hex(BLUE, PURPLE, 0.5).lower())

    def test_gradient_three_quarters_hits_second_leg(self):
        self.assertEqual(
            progress_rich.gradient_hex(0.75).lower(),
            progress_rich.blend_hex(PURPLE, PEACH, 0.5).lower())

    def test_gradient_clamps(self):
        self.assertEqual(
            progress_rich.gradient_hex(-0.5).lower(), BLUE)
        self.assertEqual(
            progress_rich.gradient_hex(1.5).lower(), PEACH)

    def test_gradient_differs_from_naive_two_stop(self):
        # The mid leg must actually bend the ramp: at t=0.25 the
        # three-stop color is far from the straight blue->peach line.
        three = progress_rich.gradient_hex(0.25).lower()
        two = progress_rich.blend_hex(BLUE, PEACH, 0.25).lower()
        self.assertNotEqual(three, two)


@unittest.skipUnless(HAS_RICH, "rich not installed: gradient test n/a")
class TestSmoothBanding(unittest.TestCase):
    def test_neighbors_step_small_at_40_width(self):
        # Adjacent non-head cells must differ by small steps (no flat
        # per-cell banding): max per-channel delta <= 32 across the run.
        column = progress_rich.GradientBarColumn(bar_width=40)
        _, ansi = _render_ansi(column, 50, total=100)
        codes = _truecolor_codes(ansi)
        text, _ = _render_ansi(column, 50, total=100)
        filled, _ = _filled_and_track_counts(text)
        self.assertGreater(filled, 5)
        run = codes[:filled - 1]  # exclude the bright head cell
        worst = 0
        for prev, cur in zip(run, run[1:]):
            for a, b in zip(prev, cur):
                worst = max(worst, abs(int(a) - int(b)))
        self.assertLessEqual(
            worst, 32,
            "neighbor steps must be smooth, got max delta %d in %r"
            % (worst, codes))

    def test_many_distinct_colors_no_flat_bands(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 75, total=100)
        filled, _ = _filled_and_track_counts(text)
        run = _truecolor_codes(ansi)[:filled - 1]
        self.assertGreaterEqual(
            len(set(run)), 8,
            "40-wide run must not collapse to flat bands, got: %r"
            % (run,))


@unittest.skipUnless(HAS_RICH, "rich not installed: gradient test n/a")
class TestBrightHead(unittest.TestCase):
    def test_head_is_light_peach_glow_peak(self):
        # The frontier cell glows fully: peach ramp end lerped by
        # intensity 1.0 -> light peach (255, 192, 159) exactly.
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 50, total=100)
        filled, _ = _filled_and_track_counts(text)
        codes = _truecolor_codes(ansi)
        head = codes[filled - 1]
        self.assertEqual(head, ("255", "192", "159"))

    def test_head_at_least_as_bright_as_second_to_last(self):
        # 8-bit rounding can tie the frontier with its neighbor;
        # nothing in the fill may be strictly brighter than the head.
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 50, total=100)
        filled, _ = _filled_and_track_counts(text)
        codes = _truecolor_codes(ansi)
        prev_sum = sum(int(c) for c in codes[filled - 2])
        head_sum = sum(int(c) for c in codes[filled - 1])
        self.assertGreaterEqual(head_sum, prev_sum)

    def test_second_to_last_is_glow_dominated(self):
        # Under the intensity glow the cell behind the tip no longer
        # sits on the bare ramp: it lands near light peach too.
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 50, total=100)
        filled, _ = _filled_and_track_counts(text)
        codes = _truecolor_codes(ansi)
        prev = tuple(int(c) for c in codes[filled - 2])
        for got, want in zip(prev, (255, 192, 159)):
            self.assertLessEqual(abs(got - want), 20)


@unittest.skipUnless(HAS_RICH, "rich not installed: gradient test n/a")
class TestEighthBlocks(unittest.TestCase):
    def test_fractional_progress_renders_partial_block(self):
        # 1/80 of a 40-wide bar = 4 eighths: one partial cell, no
        # full cells, and the rest stays track.
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, _ = _render_ansi(column, 1, total=80)
        self.assertIn("▌", text.plain)
        self.assertNotIn(FULL, text.plain)
        filled, track = _filled_and_track_counts(text)
        self.assertEqual(filled, 1)
        self.assertEqual(track, 39)

    def test_fill_advances_finer_than_whole_cells(self):
        # Neighboring fractions must render differently (1/8-cell
        # steps, not whole-cell jumps).
        column = progress_rich.GradientBarColumn(bar_width=40)
        plains = set()
        for done in (0, 1, 2, 3):
            text, _ = _render_ansi(column, done, total=80)
            plains.add(text.plain)
        self.assertEqual(len(plains), 4)

    def test_exact_half_has_no_partial_cell(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, _ = _render_ansi(column, 50, total=100)
        self.assertEqual(text.plain.count(FULL), 20)
        for part in EIGHTHS:
            self.assertNotIn(part, text.plain)
        filled, track = _filled_and_track_counts(text)
        self.assertEqual((filled, track), (20, 20))

    def test_zero_bar_is_all_track(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, _ = _render_ansi(column, 0, total=100)
        filled, track = _filled_and_track_counts(text)
        self.assertEqual((filled, track), (0, 40))


class TestWidthAndDescCaps(unittest.TestCase):
    def test_truncate_desc_is_pure_and_bounded(self):
        long_desc = "attempt 1 " + "x" * 200
        short = progress_rich._truncate_desc(long_desc)
        self.assertLessEqual(len(short), 33)
        self.assertTrue(short.endswith("…"))
        self.assertEqual(progress_rich._truncate_desc("attempt 1"),
                         "attempt 1")

    @unittest.skipUnless(HAS_RICH, "rich not installed: rich test n/a")
    def test_bar_min_width_20(self):
        column = progress_rich.GradientBarColumn(bar_width=5)
        self.assertEqual(column.bar_width, 20)
        column40 = progress_rich.GradientBarColumn(bar_width=40)
        self.assertEqual(column40.bar_width, 40)

    @unittest.skipUnless(HAS_RICH, "rich not installed: rich test n/a")
    def test_rich_bar_truncates_long_desc(self):
        bar = progress_rich.RichBar(6, "y" * 200)
        try:
            self.assertLessEqual(len(bar._desc), 33)
            self.assertTrue(bar._desc.endswith("…"))
        finally:
            bar.finish()


if __name__ == "__main__":
    unittest.main()
