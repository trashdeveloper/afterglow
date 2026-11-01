"""Task SWEEP-MATH tests: opencode's ACTUAL tab-pulse sweep math in progress_rich.

Run: python3 tests/test_progress_sweep.py -v   (from repo root)

Ports the verbatim pure-math vectors from opencode's
packages/tui/src/component/tab-pulse.tsx (dev branch, fetched live):
- clamp(v) = max(0, min(1, v))
- smootherstep(v) = v*v*v*(v*(v*6-15)+10)
- intensity_at(index, front, head=4, tail=18):
    distance = front - index
    distance < 0 -> smootherstep(clamp(1 + distance/head))
    else         -> smootherstep(clamp(1 - distance/tail))
- coast(value) with ramp=0.2 (PULSE_FPS=20, RUN_DURATION=2800ms easing)
- blend layering (blendTabPulseColor order): out = bg;
  out += (glow - out) * glow; out += (running - out) * running.

Palette (verbatim from packages/tui/src/theme/assets/opencode.json dark):
peach #fab283, light peach #ffc09f, blue #5c9cf5, purple #9d7cd8.

Bar-level (determinate, full-width):
- Filled run base ramp blue -> purple -> peach across the fill.
- Glow head AT the fill frontier: intensity_at(cell, frontier_pos);
  cell = lerp(base_ramp, #ffc09f, intensity). 4-cell hot attack,
  18-cell decay tail -- the actual opencode look.
- OPTIONAL shimmer render(frac, phase): secondary echo front at
  frontier-8, half intensity (their dual-front sweep); static default
  phase=0 is fully deterministic. No timer loop (Rich Live drives
  frames if ever animated).

Stdlib only for the harness: pure-math tests need no rich; render
tests reuse the rich column. Fallback bytes stay identical to
loop.progress_tick / loop.progress_done.
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

BLUE = "#5c9cf5"
PURPLE = "#9d7cd8"
PEACH = "#fab283"
LIGHT_PEACH = "#ffc09f"

FULL = "\u2588"
EIGHTHS = ("\u258f", "\u258e", "\u258d", "\u258c",
           "\u258b", "\u258a", "\u2589")
TRACK = "\u2500"


def _filled_and_track_counts(text):
    plain = text.plain
    filled = sum(plain.count(c) for c in (FULL,) + EIGHTHS)
    return filled, plain.count(TRACK)


def _render_ansi(column, completed, total=100, width=40):
    from rich.console import Console
    from rich.progress import Progress
    progress = Progress()
    task_id = progress.add_task("attempt 1", total=total)
    progress.update(task_id, completed=completed)
    task = next(t for t in progress.tasks if t.id == task_id)
    text = column.render(task)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=width + 20,
                      color_system="truecolor", no_color=False)
    console.print(text, end="")
    return text, buf.getvalue()


def _truecolor_codes(ansi):
    return re.findall(r"38;2;(\d+);(\d+);(\d+)", ansi)


class _TtyStderr(io.StringIO):
    def isatty(self):
        return True


@contextlib.contextmanager
def _rich_blocked():
    touched = ("rich", "rich.progress", "rich.console", "rich.text",
               "rich.style")
    saved = {key: sys.modules[key] for key in touched if key in sys.modules}
    for key in touched:
        sys.modules[key] = None
    try:
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


class TestSmootherstep(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(progress_rich.smootherstep(0), 0)

    def test_one(self):
        self.assertEqual(progress_rich.smootherstep(1), 1)

    def test_half(self):
        # 0.125 * (0.5*(3-15)+10) = 0.125*4 = 0.5 exactly.
        self.assertAlmostEqual(progress_rich.smootherstep(0.5), 0.5)

    def test_monotonic_and_bounded(self):
        prev = 0.0
        for i in range(11):
            val = progress_rich.smootherstep(i / 10.0)
            self.assertGreaterEqual(val, 0.0)
            self.assertLessEqual(val, 1.0)
            self.assertGreaterEqual(val, prev)
            prev = val


class TestClamp(unittest.TestCase):
    def test_inside_passes_through(self):
        self.assertEqual(progress_rich.clamp(0.3), 0.3)

    def test_below_clamps_to_zero(self):
        self.assertEqual(progress_rich.clamp(-2.5), 0)

    def test_above_clamps_to_one(self):
        self.assertEqual(progress_rich.clamp(7.0), 1)


class TestIntensityAt(unittest.TestCase):
    def test_at_front_is_one(self):
        self.assertAlmostEqual(
            progress_rich.intensity_at(10, 10), 1.0)

    def test_attack_edge_zero(self):
        # distance = front-index = -4 -> 1 + (-4)/4 = 0.
        self.assertAlmostEqual(
            progress_rich.intensity_at(14, 10), 0.0)

    def test_deep_attack_stays_zero(self):
        # Any cell >= head past the front stays 0 (smootherstep clamps).
        self.assertAlmostEqual(
            progress_rich.intensity_at(28, 10), 0.0)

    def test_decay_edge_zero(self):
        # distance = 18 -> 1 - 18/18 = 0.
        self.assertAlmostEqual(
            progress_rich.intensity_at(10, 28), 0.0)

    def test_decay_tail_monotonic(self):
        # Behind the front, intensity rises monotonically toward it
        # (equivalently: falls monotonically to 0 over the 18-cell
        # tail when read back from the frontier).
        front = 30.0
        prev = -1.0
        for cell in range(12, 31):
            val = progress_rich.intensity_at(cell, front)
            self.assertGreaterEqual(val, 0.0)
            self.assertLessEqual(val, 1.0)
            self.assertGreaterEqual(val, prev)
            prev = val
        self.assertAlmostEqual(
            progress_rich.intensity_at(12, front), 0.0)

    def test_attack_rises_over_head(self):
        # Ahead of the front, intensity rises over the 4-cell head.
        front = 10.0
        vals = [progress_rich.intensity_at(c, front)
                for c in (14, 13, 12, 11, 10)]
        for prev_v, cur_v in zip(vals, vals[1:]):
            self.assertLess(prev_v, cur_v)
        self.assertAlmostEqual(vals[0], 0.0)
        self.assertAlmostEqual(vals[-1], 1.0)

    def test_near_tail_is_hot_not_zero(self):
        # Pin the live-source behavior 4 cells behind the front:
        # tail leg 1-4/18 -> smootherstep ~ 0.906 (NOT zero). The
        # zero on the decay side sits at front-18, not front-4.
        val = progress_rich.intensity_at(6, 10)
        self.assertGreater(val, 0.9)
        self.assertLess(val, 1.0)


class TestCoast(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(progress_rich.coast(0), 0)

    def test_one(self):
        self.assertEqual(progress_rich.coast(1), 1)

    def test_half(self):
        # (0.5 - 0.1) / 0.8 = 0.5 exactly.
        self.assertAlmostEqual(progress_rich.coast(0.5), 0.5)

    def test_monotonic_and_bounded(self):
        prev = -1.0
        for i in range(21):
            val = progress_rich.coast(i / 20.0)
            self.assertGreaterEqual(val, 0.0)
            self.assertLessEqual(val, 1.0)
            self.assertGreaterEqual(val, prev)
            prev = val


class TestBlendLayering(unittest.TestCase):
    def test_glow_then_running_order(self):
        # blendTabPulseColor order: out = bg; out += (glow-out)*glow;
        # out += (running-out)*running (sequential lerp).
        out = progress_rich.blend_layers(
            bg=(0, 0, 0), glow=(255, 255, 255), glow_i=1.0,
            running=(255, 0, 0), running_i=0.0)
        self.assertEqual(tuple(int(round(c)) for c in out), (255, 255, 255))
        out = progress_rich.blend_layers(
            bg=(0, 0, 0), glow=(255, 255, 255), glow_i=1.0,
            running=(255, 0, 0), running_i=1.0)
        self.assertEqual(tuple(int(round(c)) for c in out), (255, 0, 0))

    def test_zero_intensities_return_bg(self):
        out = progress_rich.blend_layers(
            bg=(10, 20, 30), glow=(255, 255, 255), glow_i=0.0,
            running=(255, 0, 0), running_i=0.0)
        self.assertEqual(tuple(int(round(c)) for c in out), (10, 20, 30))


class TestSweepRamp(unittest.TestCase):
    def test_ramp_endpoints_are_opencode_palette(self):
        self.assertEqual(progress_rich.base_ramp_hex(0.0).lower(), BLUE)
        self.assertEqual(progress_rich.base_ramp_hex(1.0).lower(), PEACH)

    def test_ramp_midpoint_is_purple(self):
        self.assertEqual(progress_rich.base_ramp_hex(0.5).lower(), PURPLE)

    def test_gradient_hex_matches_sweep_ramp(self):
        # The bar's three-stop ramp IS the opencode palette now.
        self.assertEqual(progress_rich.gradient_hex(0.0).lower(), BLUE)
        self.assertEqual(progress_rich.gradient_hex(0.5).lower(), PURPLE)
        self.assertEqual(progress_rich.gradient_hex(1.0).lower(), PEACH)


@unittest.skipUnless(HAS_RICH, "rich not installed: sweep test n/a")
class TestSweepBar(unittest.TestCase):
    def test_frontier_cell_is_brightest(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 50, total=100)
        filled, _ = _filled_and_track_counts(text)
        codes = _truecolor_codes(ansi)
        self.assertEqual(filled, 20)
        sums = [sum(int(c) for c in rgb) for rgb in codes[:filled]]
        # The frontier cell reaches the glow peak (ties from 8-bit
        # rounding one cell back are allowed; nothing is brighter).
        self.assertEqual(sums[filled - 1], max(sums))
        # Exact glow peak: frontier cell lerps fully to light peach.
        self.assertEqual(codes[filled - 1], ("255", "192", "159"))

    def test_tail_decays_monotonically_over_18_cells(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 50, total=100)
        filled, _ = _filled_and_track_counts(text)
        codes = _truecolor_codes(ansi)
        run = codes[:filled]
        # The 18 filled cells behind the frontier fade monotonically
        # (no brighter bump looking back), ignoring the half-intensity
        # echo hump is NOT allowed: echo uses max() so the combined
        # profile must still fall monotonically away from the head.
        sums = [sum(int(c) for c in rgb) for rgb in run]
        for newer, older in zip(reversed(sums), reversed(sums[:-1])):
            self.assertGreaterEqual(newer, older)

    def test_neighbor_delta_small_inside_fill(self):
        # No flat per-cell banding inside the fill: adjacent cells
        # step smoothly (max per-channel delta <= 32).
        column = progress_rich.GradientBarColumn(bar_width=40)
        _, ansi = _render_ansi(column, 50, total=100)
        text, _ = _render_ansi(column, 50, total=100)
        filled, _ = _filled_and_track_counts(text)
        codes = _truecolor_codes(ansi)
        run = codes[:filled]
        worst = 0
        for prev_c, cur_c in zip(run, run[1:]):
            for a, b in zip(prev_c, cur_c):
                worst = max(worst, abs(int(a) - int(b)))
        self.assertLessEqual(worst, 32)

    def test_echo_front_adds_secondary_hump(self):
        # With phase moved off 0 the echo front shifts: the rendered
        # colors must differ from the static frame, deterministically.
        still = progress_rich.sweep_cell_colors(40, 0.5, phase=0.0)
        moved = progress_rich.sweep_cell_colors(40, 0.5, phase=0.5)
        self.assertNotEqual(still, moved)
        self.assertEqual(
            progress_rich.sweep_cell_colors(40, 0.5, phase=0.5), moved)

    def test_eighth_partials_preserved(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, _ = _render_ansi(column, 1, total=80)
        self.assertIn("\u258c", text.plain)
        self.assertNotIn(FULL, text.plain)
        filled, track = _filled_and_track_counts(text)
        self.assertEqual((filled, track), (1, 39))

    def test_zero_bar_is_all_track(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, _ = _render_ansi(column, 0, total=100)
        filled, track = _filled_and_track_counts(text)
        self.assertEqual((filled, track), (0, 40))

    def test_full_bar_is_bold_bright_green(self):
        column = progress_rich.GradientBarColumn(bar_width=40)
        text, ansi = _render_ansi(column, 100, total=100)
        self.assertIn("bright_green", str(text.style))
        self.assertEqual(text.plain, FULL * 40)


class TestSweepDeterminism(unittest.TestCase):
    def test_static_phase_zero_is_repeatable(self):
        first = progress_rich.sweep_cell_colors(40, 0.5, phase=0.0)
        second = progress_rich.sweep_cell_colors(40, 0.5, phase=0.0)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 40)

    def test_frac_zero_and_one_edges(self):
        empty = progress_rich.sweep_cell_colors(40, 0.0)
        self.assertEqual(len(empty), 40)
        full = progress_rich.sweep_cell_colors(40, 1.0)
        self.assertEqual(len(full), 40)
        self.assertNotEqual(empty, full)


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
            self.assertEqual(want, "\n")
            out2, err2 = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out2), \
                    contextlib.redirect_stderr(err2):
                bar = progress_rich.make_bar(6, "attempt 1", args)
                bar.advance(phase="collect", attempt=1, total=3,
                            elapsed_s=1.0)
                bar.finish()
            self.assertEqual(err2.getvalue()[-len(want):], want)


if __name__ == "__main__":
    unittest.main()
