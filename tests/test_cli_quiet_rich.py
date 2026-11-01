"""Task CLI-QUIET-RICH tests: quiet-by-default CLI + opencode-style rich bar.

Run: python3 tests/test_cli_quiet_rich.py -v   (from repo root)

Stdlib only for the harness, no model calls, no real sim builds:
- (a) default-quiet: stage lines ([RED]/[COLLECT]/[DIAGNOSE]/[PATCH]/
  [RESIM]/[VERIFY] + sim_out + notes) are ABSENT without --verbose;
  only the progress bar line + final verdict line remain.
- (b) --verbose restores today's full output byte-for-byte (same streams,
  same text, same flush behavior as the pre-quiet code path).
- (c) rich-missing fallback (ImportError via sys.modules stubs):
  progress_rich.make_bar emits byte-identical output to today's stdlib
  path (loop.progress_tick / loop.progress_done) for the same inputs.
- (d) non-TTY stderr (or --quiet): zero bytes on both streams (unchanged).
- (e) `afterglow run` wrapper accepts --verbose and forwards it to the
  loop; its own "afterglow run:" banner is quiet-by-default too.
"""

import contextlib
import importlib.util
import io
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")


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


def _load_afterglow():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("afterglow_cli_qr", AFTERGLOW)
    spec = importlib.util.spec_from_loader("afterglow_cli_qr", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestVerboseFlag(unittest.TestCase):
    def test_loop_parser_defaults_to_quiet(self):
        args = loop.parse_args(["--spec", "sim/targets/uart-start-half.yaml"])
        self.assertTrue(hasattr(args, "verbose"))
        self.assertFalse(args.verbose)
        self.assertFalse(loop.is_verbose(args))

    def test_loop_verbose_flag_enables_stage_output(self):
        args = loop.parse_args(
            ["--spec", "sim/targets/uart-start-half.yaml", "--verbose"])
        self.assertTrue(args.verbose)
        self.assertTrue(loop.is_verbose(args))


class TestDefaultQuiet(unittest.TestCase):
    def test_stage_lines_absent_by_default(self):
        # Default (no --verbose): every [...] stage line is suppressed on
        # both streams; only progress + final verdict remain.
        args = loop.parse_args(
            ["--spec", "sim/targets/uart-start-half.yaml"])
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            loop.emit_stage(args, "[COLLECT] lines=10 tokens=20", flush=True)
            loop.emit_stage(args, "[VERIFY] tohost=PASS 0x1", flush=True)
            loop.emit_stage(args, "[RED] sim/sim.py --case x", flush=True)
            loop.emit_stage(args, "[DIAGNOSE] attempt 1 latency=5ms",
                            flush=True)
            loop.emit_stage(args, "[PATCH] verbatim model diff applied",
                            flush=True)
            loop.emit_stage(args, "[RESIM] rebuilding patched tree...",
                            flush=True)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_stderr_stage_lines_absent_by_default(self):
        # file=sys.stderr stage lines also stay silent by default.
        args = loop.parse_args(
            ["--spec", "sim/targets/uart-start-half.yaml"])
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            loop.emit_stage(args, "[LADDER] locked", file=sys.stderr,
                            flush=True)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


class TestVerboseRestores(unittest.TestCase):
    def test_verbose_restores_stdout_stage_bytes(self):
        # --verbose: byte-for-byte identical to today's print() output
        # on the same stream (stdout + flush=True).
        msg = "[COLLECT] lines=10 tokens=20 (trace 5 lines/6 tok, prompt 20)"
        args = loop.parse_args(
            ["--spec", "sim/targets/uart-start-half.yaml", "--verbose"])
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            loop.emit_stage(args, msg, flush=True)
        self.assertEqual(out.getvalue(), msg + "\n")
        self.assertEqual(err.getvalue(), "")

    def test_verbose_restores_stderr_stage_bytes(self):
        # --verbose preserves file=sys.stderr routing byte-for-byte.
        msg = "[LADDER] locked (ladder_used=False)"
        args = loop.parse_args(
            ["--spec", "sim/targets/uart-start-half.yaml", "--verbose"])
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            loop.emit_stage(args, msg, file=sys.stderr, flush=True)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), msg + "\n")

    def test_verbose_restores_each_stage_label(self):
        # Every documented stage label survives the round trip verbatim.
        labels = ["[RED]", "[COLLECT]", "[DIAGNOSE]", "[PATCH]",
                  "[RESIM]", "[VERIFY]"]
        args = loop.parse_args(
            ["--spec", "sim/targets/uart-start-half.yaml", "--verbose"])
        for lab in labels:
            out, err = io.StringIO(), _TtyStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                loop.emit_stage(args, "%s probe" % lab, flush=True)
            self.assertEqual(out.getvalue(), "%s probe\n" % lab)


class TestProgressRichFallback(unittest.TestCase):
    def test_rich_missing_matches_stdlib_tick_bytes(self):
        # rich ImportError: fallback advance emits byte-identical output
        # to loop.progress_tick for the same inputs.
        from agent import progress_rich
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
            self.assertTrue(err2.getvalue().startswith("\r"))
            self.assertIn("[collect] attempt 1/3", err2.getvalue())

    def test_rich_missing_finish_matches_stdlib_done_bytes(self):
        # rich ImportError: fallback finish emits byte-identical output
        # to loop.progress_done (one newline).
        from agent import progress_rich
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

    def test_stage_labels_cover_all_phases(self):
        # The rich module names every documented stage label.
        from agent import progress_rich
        labels = " ".join(getattr(progress_rich, "STAGES", ()))
        for want in ("RED", "collect", "diagnose", "patch", "resim",
                     "verify"):
            self.assertIn(want, labels)


class TestNonTtyFallback(unittest.TestCase):
    def test_pipe_stderr_zero_bytes(self):
        # Non-TTY stderr (pipes/CI): zero progress bytes either stream,
        # rich or fallback -- unchanged behavior.
        from agent import progress_rich
        for quiet in (False, True):
            args = types.SimpleNamespace(quiet=quiet, verbose=False)
            out, err = io.StringIO(), _PipeStderr()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                bar = progress_rich.make_bar(6, "attempt 1", args)
                bar.advance()
                bar.advance(phase="diagnose")
                bar.finish()
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), "")

    def test_quiet_on_tty_zero_progress_bytes(self):
        # --quiet on a TTY: zero progress bytes either stream.
        from agent import progress_rich
        args = types.SimpleNamespace(quiet=True, verbose=False)
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            bar = progress_rich.make_bar(6, "attempt 1", args)
            bar.advance()
            bar.advance(phase="diagnose")
            bar.finish()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


@unittest.skipUnless(HAS_RICH, "rich not installed: rich-path test n/a")
class TestRichBarActive(unittest.TestCase):
    def test_rich_bar_writes_stderr_only(self):
        # Rich present + TTY + not --quiet: advance/finish work,
        # stderr only, zero stdout bytes.
        from agent import progress_rich
        args = types.SimpleNamespace(quiet=False, verbose=False)
        out, err = io.StringIO(), _TtyStderr()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            bar = progress_rich.make_bar(6, "attempt 1", args)
            self.assertTrue(getattr(bar, "is_rich", False))
            bar.advance()
            bar.advance(phase="diagnose")
            bar.finish()
        self.assertEqual(out.getvalue(), "")
        self.assertNotEqual(err.getvalue(), "")


class TestWrapperVerbose(unittest.TestCase):
    def test_run_parser_has_verbose_flag(self):
        mod = _load_afterglow()
        parser = mod.build_parser()
        args = parser.parse_args(["run", "sim/targets/uart-start-half.yaml"])
        self.assertTrue(hasattr(args, "verbose"))
        self.assertFalse(args.verbose)
        args2 = parser.parse_args(
            ["run", "sim/targets/uart-start-half.yaml", "--verbose"])
        self.assertTrue(args2.verbose)

    def test_wrapper_banner_quiet_by_default(self):
        # The wrapper's own "afterglow run:" banner is quiet-by-default:
        # absent without --verbose, present with it (loop mocked).
        mod = _load_afterglow()
        real_main = loop.main
        loop.main = lambda argv: 0
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = mod.main(["run", "sim/targets/uart-start-half.yaml",
                               "--seed", "42"])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue(), "")
            out2, err2 = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out2), \
                    contextlib.redirect_stderr(err2):
                rc2 = mod.main(["run", "sim/targets/uart-start-half.yaml",
                                "--seed", "42", "--verbose"])
            self.assertEqual(rc2, 0)
            self.assertIn("afterglow run:", out2.getvalue())
        finally:
            loop.main = real_main


if __name__ == "__main__":
    unittest.main()
