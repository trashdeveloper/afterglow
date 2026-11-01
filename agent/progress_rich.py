"""Task CLI-QUIET-RICH: opencode-style progress bar (rich TUI, stdlib fallback).

Quiet-by-default display layer ONLY: no loop/guard/score semantics change,
no bundle content change, no proof logging change.

One bar per attempt: ``make_bar(total, desc, args)`` returns an object with
``.advance(n=1)`` / ``.finish()`` plus context-manager support.

* Rich path: when ``rich`` imports cleanly AND stderr is a TTY AND not
  ``--quiet``, the bar drives ``rich.progress`` on STDERR only (never
  stdout): spinner column (spins during model calls), gradient-style bar
  column, stage-label description column (RED/collect/diagnose/patch/
  resim/verify), percentage + elapsed columns.
* Fallback: when ``rich`` is missing (ImportError) or stderr is not a TTY
  (or --quiet), the bar emits the current plain single-line updater bytes
  ``\\r[<phase>] attempt <n>/<total> ... <elapsed>s`` + ``\\n`` on finish
  -- byte-identical to ``loop.progress_tick`` / ``loop.progress_done``
  for the same inputs. ``--quiet`` and non-TTY stderr stay at zero
  bytes, exactly as today.

``rich`` is imported lazily inside the rich bar only and guarded by
try/except ImportError: it is NEVER hard-required. This module itself
imports stdlib only at top level.
"""

from __future__ import annotations

import sys
import time

# Documented stage labels for the bar description (RED/collect/diagnose/
# patch/resim/verify). Lowercase phase names from the loop map to the
# same labels ("red" -> "RED").
STAGES = ("RED", "collect", "diagnose", "patch", "resim", "verify")
_TOTAL_STEPS = len(STAGES)

# opencode dark palette, verbatim from
# packages/tui/src/theme/assets/opencode.json (dark): darkStep9 peach,
# darkStep10 light peach, darkSecondary blue, darkAccent purple. The
# filled run ramps blue -> purple -> peach (dark -> light); the glow
# head lerps toward light peach.
_RAMP_BLUE = "#5c9cf5"
_RAMP_PURPLE = "#9d7cd8"
_RAMP_PEACH = "#fab283"
_GLOW_LIGHT_PEACH = "#ffc09f"
_GRADIENT_WIDTH = 40

# Sweep geometry, verbatim from tab-pulse.tsx: RUN_HEAD = 4 (hot attack
# cells), RUN_TAIL = 18 (decay tail cells). The echo front sits 8 cells
# behind the main frontier at half intensity (their dual-front sweep:
# secondProgress = (cycles + 0.5) % 1); phase shifts it for shimmer.
_SWEEP_HEAD = 4
_SWEEP_TAIL = 18
_ECHO_OFFSET = -8
_ECHO_SCALE = 0.5

# Track (unfilled remainder) style: dimmed #555555 dashes.
_TRACK_COLOR = "#555555"
_TRACK_CHAR = "\u2500"

# Fractional fill: one terminal cell renders 1/8-cell steps with the
# left-eighth blocks; a fully covered cell is a full block.
_FULL_BLOCK = "\u2588"
_EIGHTH_BLOCKS = ("\u258f", "\u258e", "\u258d", "\u258c",
                  "\u258b", "\u258a", "\u2589")

# Width caps: the bar never shrinks below this (long descriptions are
# truncated instead of squeezing the bar out); descriptions truncate
# past this length with an ellipsis.
_MIN_BAR_WIDTH = 20
_MAX_DESC_LEN = 32


def _hex_to_rgb(value: str) -> tuple:
    """Parse ``#rrggbb`` (case-insensitive, hash optional) to (r, g, b)."""
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError("expected #rrggbb hex color, got %r" % (value,))
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def blend_hex(a: str, b: str, t: float) -> str:
    """Linear blend of two ``#rrggbb`` colors; ``t`` clamped to [0, 1].

    Pure function (no rich dependency) so unit tests can pin exact hex
    stops: ``blend_hex("#00d7ff", "#ff00ff", 0.0)`` is cyan,
    ``t=1.0`` is magenta, ``t=0.5`` is ``#806cff`` (half-up rounding).
    """
    try:
        amount = float(t)
    except (TypeError, ValueError):
        amount = 0.0
    amount = min(1.0, max(0.0, amount))
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)

    def mix(u: int, v: int) -> int:
        return min(255, max(0, int(u + (v - u) * amount + 0.5)))

    return "#%02x%02x%02x" % (mix(ra, rb), mix(ga, gb), mix(ba, bb))


def gradient_hex(t: float) -> str:
    """Three-stop ramp color at position ``t`` in [0, 1] (clamped).

    ``t=0`` is blue ``#5c9cf5``, ``t=0.5`` is purple ``#9d7cd8``,
    ``t=1`` is peach ``#fab283``; each leg blends with
    :func:`blend_hex`. Pure function (no rich dependency) so unit
    tests can pin exact stops.
    """
    return base_ramp_hex(t)


def base_ramp_hex(t: float) -> str:
    """Filled-run base ramp: blue -> purple -> peach across the fill.

    Verbatim opencode dark palette (``opencode.json``: darkSecondary,
    darkAccent, darkStep9). ``t`` clamped to [0, 1]; the first leg
    blends blue -> purple, the second purple -> peach.
    """
    try:
        amount = float(t)
    except (TypeError, ValueError):
        amount = 0.0
    amount = min(1.0, max(0.0, amount))
    if amount <= 0.5:
        return blend_hex(_RAMP_BLUE, _RAMP_PURPLE, amount * 2.0)
    return blend_hex(_RAMP_PURPLE, _RAMP_PEACH, (amount - 0.5) * 2.0)


# --- Verbatim ports from opencode's
# packages/tui/src/component/tab-pulse.tsx (dev branch). ---

def clamp(value) -> float:
    """Port of ``clamp``: ``max(0, min(1, value))``."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, amount))


def smootherstep(value) -> float:
    """Port of ``smootherstep``: ``v*v*v*(v*(v*6-15)+10)``."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return 0.0
    return val * val * val * (val * (val * 6 - 15) + 10)


def intensity_at(index, front, head=_SWEEP_HEAD, tail=_SWEEP_TAIL) -> float:
    """Port of ``intensityAt``: glow intensity of ``index`` at ``front``.

    ``distance = front - index``; ahead of the front (``distance < 0``)
    the 4-cell hot attack rises via ``smootherstep(clamp(1 + d/head))``,
    behind it the 18-cell tail decays via
    ``smootherstep(clamp(1 - d/tail))``. True zeros sit at
    ``front + head`` (attack edge) and ``front - tail`` (decay edge).
    """
    try:
        idx = float(index)
    except (TypeError, ValueError):
        idx = 0.0
    try:
        fro = float(front)
    except (TypeError, ValueError):
        fro = 0.0
    try:
        hed = float(head)
    except (TypeError, ValueError):
        hed = float(_SWEEP_HEAD)
    try:
        tal = float(tail)
    except (TypeError, ValueError):
        tal = float(_SWEEP_TAIL)
    if hed <= 0 or tal <= 0:
        return 1.0 if idx == fro else 0.0
    distance = fro - idx
    if distance < 0:
        return smootherstep(clamp(1 + distance / hed))
    return smootherstep(clamp(1 - distance / tal))


def coast(value) -> float:
    """Port of ``coast``: eased sweep clock (ramp = 0.2).

    Their ``PULSE_FPS = 20`` / ``RUN_DURATION = 2800`` sweep advances
    ``front = start + coast(progress) * (end - start)``.
    """
    try:
        val = float(value)
    except (TypeError, ValueError):
        return 0.0
    ramp = 0.2
    if val < ramp:
        return (val * val) / (2 * ramp * (1 - ramp))
    if val > 1 - ramp:
        return 1 - ((1 - val) * (1 - val)) / (2 * ramp * (1 - ramp))
    return (val - ramp / 2) / (1 - ramp)


def blend_layers(bg, glow, glow_i, running, running_i) -> tuple:
    """Two-layer port of ``blendTabPulseColor`` layering order.

    ``out = bg; out += (glow - out) * glow_i`` (their glow layer),
    then ``out += (running - out) * running_i`` (their running layer):
    sequential lerp toward each layer color. RGB triples in/out.
    """
    try:
        gi = max(0.0, min(1.0, float(glow_i)))
    except (TypeError, ValueError):
        gi = 0.0
    try:
        ri = max(0.0, min(1.0, float(running_i)))
    except (TypeError, ValueError):
        ri = 0.0
    out = [float(c) for c in bg]
    for k in range(3):
        out[k] = out[k] + (float(glow[k]) - out[k]) * gi
    for k in range(3):
        out[k] = out[k] + (float(running[k]) - out[k]) * ri
    return (out[0], out[1], out[2])


def _combined_intensity(cell, frontier, phase=0.0) -> float:
    """Glow intensity at ``cell``: main front + half-intensity echo.

    Mirrors their dual-front sweep (``max(intensityAt(front0),
    intensityAt(front1))``): the echo front trails at
    ``frontier - 8 * (1 - phase)`` with half weight. ``phase`` in
    [0, 1] shifts the echo for shimmer; static default ``phase=0``
    parks it 8 cells behind the frontier (fully deterministic).
    """
    try:
        pha = float(phase)
    except (TypeError, ValueError):
        pha = 0.0
    pha = max(0.0, min(1.0, pha))
    primary = intensity_at(cell, frontier, _SWEEP_HEAD, _SWEEP_TAIL)
    echo = _ECHO_SCALE * intensity_at(
        cell, frontier + _ECHO_OFFSET * (1.0 - pha),
        _SWEEP_HEAD, _SWEEP_TAIL)
    return max(primary, echo)


def _glow_cell_hex(base_hex: str, intensity) -> str:
    """Lerp a filled cell's base ramp color toward light peach.

    ``intensity = intensity_at(cell, frontier_pos, 4, 18)``; the 4-cell
    hot attack + 18-cell decay tail is the actual opencode look.
    """
    try:
        amount = float(intensity)
    except (TypeError, ValueError):
        amount = 0.0
    return blend_hex(base_hex, _GLOW_LIGHT_PEACH,
                     max(0.0, min(1.0, amount)))


def sweep_cell_colors(width, frac, phase=0.0) -> list:
    """Per-cell hex colors of a ``width``-wide bar at fraction ``frac``.

    Pure stdlib + deterministic (``phase=0.0`` default): filled cells
    carry the blue -> purple -> peach base ramp with the glow head at
    the fill frontier (intensity 1 at the frontier cell, decaying over
    the 18-cell tail, echo hump 8 cells back); unfilled cells are the
    dim ``#555555`` track. No rich dependency; the Rich column renders
    these same colors.
    """
    try:
        wide = max(1, int(width))
    except (TypeError, ValueError):
        wide = _GRADIENT_WIDTH
    try:
        fill_frac = max(0.0, min(1.0, float(frac)))
    except (TypeError, ValueError):
        fill_frac = 0.0
    eighths = min(wide * 8, int(fill_frac * wide * 8))
    full = eighths // 8
    rem = eighths % 8
    nfill = full + (1 if rem else 0)
    frontier = eighths / 8.0 - 1.0
    colors = []
    for k in range(wide):
        if k >= nfill:
            colors.append(_TRACK_COLOR)
            continue
        pos = (k / (nfill - 1)) if nfill > 1 else 1.0
        base = base_ramp_hex(pos)
        colors.append(_glow_cell_hex(
            base, _combined_intensity(k, frontier, phase)))
    return colors


def _truncate_desc(desc: str, max_len: int = _MAX_DESC_LEN) -> str:
    """Bound a bar description to ``max_len`` chars (pure, no rich).

    Longer text truncates with a trailing ellipsis so long attempt
    names cannot blow out the bar layout; short text passes through.
    """
    text = "" if desc is None else str(desc)
    limit = max(2, int(max_len))
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def _gradient_column_class():
    """Build the GradientBarColumn class (rich imported lazily).

    Kept out of module top level so this module stays stdlib-only on
    import: rich-missing / non-TTY paths keep failing closed to the
    StdlibBar. Exposed as ``progress_rich.GradientBarColumn`` via the
    module ``__getattr__`` below (PEP 562).
    """
    from rich.progress import ProgressColumn
    from rich.style import Style
    from rich.text import Text

    class GradientBarColumn(ProgressColumn):
        """Opencode sweep bar: ramp + intensity glow + eighth blocks.

        Renders ``rich.text.Text`` with one Style per filled cell: each
        filled cell takes the blue -> purple -> peach base ramp at its
        position across the FILLED run, lerped toward light peach by
        ``intensity_at(cell, frontier_pos, 4, 18)`` -- the verbatim
        opencode sweep (4-cell hot attack + 18-cell decay tail, plus a
        half-intensity echo front 8 cells back, like their dual-front
        sweep; static phase=0, fully deterministic). Fill advances in
        1/8-cell steps via the left-eighth blocks (a partial cell takes
        the ramp color at the run end); the remainder is dim track; a
        100% complete task renders the whole bar in ``finished_style``
        (bright green, bold).
        """

        def __init__(self, bar_width=None, start=_RAMP_BLUE,
                     mid=_RAMP_PURPLE, end=_RAMP_PEACH,
                     finished_style="bright_green",
                     table_column=None):
            raw = (_GRADIENT_WIDTH if bar_width is None
                   else max(1, int(bar_width)))
            self.bar_width = max(_MIN_BAR_WIDTH, raw)
            self.start = start
            self.mid = mid
            self.end = end
            self.finished_style = finished_style
            super().__init__(table_column=table_column)

        def _ramp(self, pos: float) -> str:
            """Three-stop ramp at ``pos`` using this column's stops."""
            amount = min(1.0, max(0.0, pos))
            if amount <= 0.5:
                return blend_hex(self.start, self.mid, amount * 2.0)
            return blend_hex(self.mid, self.end, (amount - 0.5) * 2.0)

        def render(self, task, phase: float = 0.0) -> "Text":
            total = task.total or 0
            done = max(0, task.completed or 0)
            width = self.bar_width
            if total > 0 and done >= total:
                return Text(_FULL_BLOCK * width,
                            style=Style(color=self.finished_style, bold=True))
            if total > 0:
                eighths = min(width * 8, (done * width * 8) // total)
            else:
                eighths = 0
            full = eighths // 8
            rem = eighths % 8
            n = full + (1 if rem else 0)
            # Fill frontier in cell units: intensity 1 lands exactly on
            # the last filled cell (static shimmer phase=0; Rich Live
            # drives frames if we ever animate -- no timer loop here).
            frontier = eighths / 8.0 - 1.0
            text = Text()
            for k in range(n):
                if k < full:
                    char = _FULL_BLOCK
                    pos = (k / (n - 1)) if n > 1 else 0.0
                else:
                    char = _EIGHTH_BLOCKS[rem - 1]
                    pos = 1.0 if n > 1 else 0.0
                base = self._ramp(pos)
                base = _glow_cell_hex(
                    base, _combined_intensity(k, frontier, phase))
                text.append(char, style=Style(color=base))
            if width > n:
                text.append(_TRACK_CHAR * (width - n),
                            style=Style(dim=True, color=_TRACK_COLOR))
            return text

    return GradientBarColumn


def __getattr__(name: str):
    if name == "GradientBarColumn":
        cls = _gradient_column_class()
        globals()["GradientBarColumn"] = cls
        return cls
    raise AttributeError("module %r has no attribute %r"
                         % (__name__, name))


def _rich_available() -> bool:
    """True iff ``rich.progress``/``rich.console`` import cleanly.

    A ``None`` entry in ``sys.modules`` (the tests' ImportError stub)
    counts as unavailable without attempting the import. Any other
    import failure (missing package, broken install) also means
    unavailable -- never raises.
    """
    for key in ("rich", "rich.progress", "rich.console"):
        if key in sys.modules and sys.modules[key] is None:
            return False
    try:
        import importlib
        importlib.import_module("rich.progress")
        importlib.import_module("rich.console")
        return True
    except ImportError:
        return False
    except Exception:  # noqa: BLE001 -- exotic import failure: no rich
        return False


def _progress_enabled(args) -> bool:
    """True only for live stderr TTYs without --quiet (Task-8 rule).

    Pipes (CI/tests) and --quiet both disable: progress then emits zero
    bytes on either stream, keeping machine-readable stdout identical.
    """
    if bool(getattr(args, "quiet", False)):
        return False
    try:
        return bool(sys.stderr.isatty())
    except Exception:  # noqa: BLE001 -- exotic streams fail closed
        return False


def use_rich(args) -> bool:
    """True iff this run should render the rich TUI bar."""
    return bool(_progress_enabled(args) and _rich_available())


def _stage_label(desc: str, phase: str | None) -> str:
    """Bar description for a phase ("attempt 1 RED", ...).

    ``phase="red"`` maps to the documented ``RED`` label; every other
    documented phase keeps its lowercase name; unknown phases pass
    through unchanged.
    """
    if phase is None:
        return desc or "working"
    name = "RED" if str(phase) == "red" else str(phase)
    if desc:
        return "%s %s" % (desc, name)
    return name


class StdlibBar:
    """Fallback bar: current plain single-line bytes, no new dependency.

    ``advance`` accepts the Task-8 fields (``phase``, ``attempt``,
    ``total``, ``elapsed_s``); called bare as ``advance(n=1)`` it walks
    the stage list with a live elapsed clock. Byte-identical to
    ``loop.progress_tick`` / ``loop.progress_done`` for the same inputs.
    """

    is_rich = False

    def __init__(self, total, desc="", args=None, attempt=1,
                 total_attempts=1):
        self._total = total
        self._desc = desc
        self._args = args
        self._attempt = attempt
        self._total_attempts = total_attempts
        self._n = 0
        self._t0 = time.monotonic()

    def advance(self, n=1, **kwargs) -> None:
        if not _progress_enabled(self._args):
            return
        phase = kwargs.get("phase")
        if phase is None:
            idx = min(self._n, len(STAGES) - 1)
            phase = STAGES[idx].lower() if STAGES[idx] == "RED" else STAGES[idx]
            if phase == "RED":
                phase = "red"
        attempt = kwargs.get("attempt", self._attempt)
        total = kwargs.get("total", self._total_attempts)
        elapsed = kwargs.get("elapsed_s")
        if elapsed is None:
            elapsed = time.monotonic() - self._t0
        try:
            step = int(n)
        except (TypeError, ValueError):
            step = 1
        self._n += step
        try:
            elapsed_i = int(elapsed)
        except (TypeError, ValueError):
            elapsed_i = 0
        sys.stderr.write("\r[%s] attempt %d/%d ... %ds"
                         % (phase, attempt, total, elapsed_i))
        sys.stderr.flush()

    def finish(self) -> None:
        if not _progress_enabled(self._args):
            return
        sys.stderr.write("\n")
        sys.stderr.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.finish()
        return False


class RichBar:
    """Rich TUI bar: spinner + real gradient bar + stage labels on STDERR.

    Columns: spinner (animates during model calls) + description carrying
    the stage label + GradientBarColumn (opencode sweep: blue ->
    purple -> peach per-cell 24-bit ramp with intensity glow at the
    fill frontier, 1/8-cell fractional fill, dim remainder,
    bright-green finished
    style at 100%) + percentage + elapsed. Never touches stdout.

    Columns: spinner (animates during model calls) + description carrying
    the stage label + bar + percentage + elapsed. Never touches stdout.
    Any rich hiccup fails closed to the stdlib fallback at make_bar time
    (or is swallowed per-call so progress never breaks the run).
    """

    is_rich = True

    def __init__(self, total, desc=""):
        try:
            from rich.console import Console
            from rich.progress import (Progress, SpinnerColumn,
                                       TextColumn, TimeElapsedColumn)
            from agent.progress_rich import GradientBarColumn as _Gradient
        except ImportError:
            try:
                from progress_rich import GradientBarColumn as _Gradient
            except ImportError:
                raise
        self._desc = _truncate_desc(desc or "working")
        self._total = total
        self._n = 0
        self._console = Console(file=sys.stderr)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            _Gradient(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self._console,
            transient=False,
        )
        self._task = self._progress.add_task(self._desc, total=total)
        self._started = False

    def _ensure_started(self) -> None:
        if not self._started:
            self._progress.start()
            self._started = True

    def advance(self, n=1, **kwargs) -> None:
        try:
            step = int(n)
        except (TypeError, ValueError):
            step = 1
        label = _truncate_desc(_stage_label(self._desc, kwargs.get("phase")))
        self._ensure_started()
        try:
            self._progress.update(self._task, description=label)
        except Exception:  # noqa: BLE001 -- label never breaks bar
            pass
        try:
            self._progress.update(self._task, advance=step)
        except Exception:  # noqa: BLE001 -- advance API fallback below
            try:
                self._progress.advance(self._task, step)
            except Exception:  # noqa: BLE001 -- progress never breaks run
                pass
        self._n += step

    def finish(self) -> None:
        try:
            if self._started:
                self._progress.stop()
        except Exception:  # noqa: BLE001 -- stop never breaks the run
            pass

    def __enter__(self):
        self._ensure_started()
        return self

    def __exit__(self, *exc):
        self.finish()
        return False


def make_bar(total, desc="", args=None, **kwargs):
    """Create one attempt bar (rich TUI when available, else stdlib).

    ``total`` is the fixed phase-step count (6); ``desc`` names the
    attempt (e.g. ``"attempt 1"``). ``args`` carries ``.quiet`` when
    known. Extra ``attempt`` / ``total_attempts`` kwargs seed the
    fallback bar's Task-8 counters. Never raises for display reasons:
    any rich failure falls back to the stdlib bar.
    """
    if use_rich(args):
        try:
            return RichBar(total, desc)
        except Exception:  # noqa: BLE001 -- fail closed to stdlib
            pass
    return StdlibBar(
        total, desc, args,
        attempt=int(kwargs.get("attempt", 1)),
        total_attempts=int(kwargs.get("total_attempts",
                                      kwargs.get("attempts", 1))),
    )
