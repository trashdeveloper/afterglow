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
    """Rich TUI bar: spinner + gradient-style bar + stage labels on STDERR.

    Columns: spinner (animates during model calls) + description carrying
    the stage label + bar + percentage + elapsed. Never touches stdout.
    Any rich hiccup fails closed to the stdlib fallback at make_bar time
    (or is swallowed per-call so progress never breaks the run).
    """

    is_rich = True

    def __init__(self, total, desc=""):
        try:
            from rich.console import Console
            from rich.progress import (BarColumn, Progress, SpinnerColumn,
                                       TextColumn, TimeElapsedColumn)
        except ImportError:
            raise
        self._desc = desc or "working"
        self._total = total
        self._n = 0
        self._console = Console(file=sys.stderr)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
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
        label = _stage_label(self._desc, kwargs.get("phase"))
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
