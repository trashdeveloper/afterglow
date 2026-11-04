"""Task EMIT-EVENTS tests: `--emit-events jsonl` on the run loop.

Run: python3 tests/test_emit_events.py -v   (from repo root)

Stdlib only, no model calls, no real sim builds:
- (a) default path is byte-identical to today: stdout carries NO JSON
  lines (human text stays exactly where it is; the flag is opt-in).
- (b) opt-in `--emit-events jsonl`: loop progress (tick/done),
  attempt boundaries, and the final verdict are ALSO emitted as
  single-line JSON objects on stdout:
    {"type": "progress", ...}
    {"type": "attempt", "attempt": N, "phase": "start"|"end", ...}
    {"type": "verdict", "verdict": "GREEN"|"RED"|"TRIAGE", ...}
- (c) the `afterglow run` CLI accepts and forwards `--emit-events`.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")


def _load_afterglow():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("afterglow_cli", AFTERGLOW)
    spec = importlib.util.spec_from_loader("afterglow_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _json_lines(text):
    """Parse stdout into JSON objects; raise on any non-JSON line."""
    objs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        objs.append(json.loads(line))
    return objs


class TestEmitEventsDefault(unittest.TestCase):
    def test_flag_defaults_off(self):
        args = loop.parse_args(["--case", "uart-start-half"])
        self.assertIsNone(getattr(args, "emit_events", "MISSING"))

    def test_default_stdout_has_no_json_lines(self):
        # Fast-fail path (unknown case: no sim, no model): the default
        # run must not put a single JSON line on stdout. This pins the
        # "default path byte-identical" contract: the new events are
        # strictly opt-in.
        run_id = "test-emit-default-nojson"
        self.addCleanup(shutil.rmtree,
                        os.path.join(ROOT, "runs", run_id), True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = loop.main(["--case", "no-such-case-emit-events",
                            "--run-id", run_id])
        self.assertEqual(rc, loop.EXIT_RED)
        for line in out.getvalue().splitlines():
            if not line.strip():
                continue
            with self.assertRaises(json.JSONDecodeError,
                                   msg="default stdout must carry no "
                                       "JSON lines, got: %r" % (line,)):
                json.loads(line)

    def test_emit_event_helper_is_noop_when_off(self):
        args = argparse.Namespace(emit_events=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loop.emit_event(args, {"type": "progress"})
            loop.emit_attempt_start(args, 1)
            loop.emit_attempt_end(args, "red")
        self.assertEqual(out.getvalue(), "")


class TestEmitEventsJsonl(unittest.TestCase):
    def test_fast_fail_emits_progress_and_verdict(self):
        # Same fast-fail path with the flag: stdout is JSONL carrying at
        # least one progress event and exactly one terminal verdict.
        run_id = "test-emit-jsonl-fastfail"
        self.addCleanup(shutil.rmtree,
                        os.path.join(ROOT, "runs", run_id), True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = loop.main(["--case", "no-such-case-emit-events",
                            "--run-id", run_id,
                            "--emit-events", "jsonl"])
        self.assertEqual(rc, loop.EXIT_RED)
        objs = _json_lines(out.getvalue())
        self.assertGreater(len(objs), 0,
                           msg="expected JSONL events on stdout, got %r"
                               % (out.getvalue(),))
        kinds = {o.get("type") for o in objs}
        self.assertIn("progress", kinds)
        self.assertIn("verdict", kinds)
        verdicts = [o for o in objs if o.get("type") == "verdict"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "RED")
        self.assertEqual(verdicts[0]["run_id"], run_id)

    def test_attempt_boundary_events(self):
        # Attempt boundaries: start(1), start(2) closes 1 as continued,
        # end closes the open attempt with the terminal outcome. A
        # repeated start for the SAME attempt (transport retry) emits
        # nothing new.
        args = argparse.Namespace(emit_events="jsonl")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loop.emit_attempt_start(args, 1)
            loop.emit_attempt_start(args, 1)  # retry: no new boundary
            loop.emit_attempt_start(args, 2)
            loop.emit_attempt_end(args, "green")
        objs = _json_lines(out.getvalue())
        attempts = [o for o in objs if o.get("type") == "attempt"]
        self.assertEqual(
            [(a["attempt"], a["phase"]) for a in attempts],
            [(1, "start"), (1, "end"), (2, "start"), (2, "end")])
        self.assertEqual(attempts[1]["outcome"], "continued")
        self.assertEqual(attempts[3]["outcome"], "green")

    def test_verdict_values_cover_green_and_triage(self):
        args = argparse.Namespace(emit_events="jsonl")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loop.emit_verdict(args, "GREEN", run_id="r1", patch_lines=7,
                              seconds=12.5)
            loop.emit_verdict(args, "TRIAGE", run_id="r2", patch_lines=0,
                              seconds=3.0)
        objs = _json_lines(out.getvalue())
        self.assertEqual([o["verdict"] for o in objs], ["GREEN", "TRIAGE"])
        self.assertEqual(objs[0]["patch_lines"], 7)


class TestAfterglowCliForwarding(unittest.TestCase):
    def test_run_accepts_and_defaults_emit_events(self):
        mod = _load_afterglow()
        parser = mod.build_parser()
        default = parser.parse_args(
            ["run", "sim/targets/uart-start-half.yaml"])
        self.assertIsNone(getattr(default, "emit_events", "MISSING"))
        flagged = parser.parse_args(
            ["run", "sim/targets/uart-start-half.yaml",
             "--emit-events", "jsonl"])
        self.assertEqual(flagged.emit_events, "jsonl")

    def test_run_rejects_unknown_emit_events_value(self):
        mod = _load_afterglow()
        parser = mod.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                ["run", "sim/targets/uart-start-half.yaml",
                 "--emit-events", "yaml"])


class TestTriageCostEvent(unittest.TestCase):
    """Footer cost wiring: triage stop must emit a cost-carrying event.

    Red-first for the footer fix (reviewer: footer shows -- until a
    backend cost event arrives; triage runs never emit one).
    """

    def _args(self):
        return argparse.Namespace(emit_events="jsonl", quiet=False)

    def test_phase_tick_carries_est_cost_when_given(self):
        import time
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop.phase_tick(self._args(), None, "diagnose", 1, 3,
                            time.monotonic(), est_cost=0.05)
        objs = _json_lines(buf.getvalue())
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]["type"], "progress")
        self.assertEqual(objs[0]["est_cost"], 0.05)

    def test_phase_tick_omits_est_cost_by_default(self):
        import time
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop.phase_tick(self._args(), None, "diagnose", 1, 3,
                            time.monotonic())
        objs = _json_lines(buf.getvalue())
        self.assertEqual(len(objs), 1)
        self.assertNotIn("est_cost", objs[0])

    def test_triage_cost_event_shape(self):
        payload = loop.triage_cost_event(0.05, attempt=1, elapsed_s=9.5)
        self.assertEqual(payload["type"], "progress")
        self.assertEqual(payload["phase"], "triage")
        self.assertEqual(payload["est_cost"], 0.05)
        self.assertIsInstance(payload["elapsed_s"], int)
        json.dumps(payload)  # single-line JSON-serializable

    def test_triage_cost_event_elapsed_truncated_to_int(self):
        # Regression: the TUI parses elapsed_s as u64 and drops the
        # whole event on a float, which stuck the footer at $0.00 even
        # though the spend was known. The helper must truncate.
        payload = loop.triage_cost_event(0.064168, attempt=1,
                                         elapsed_s=56.60412745596841)
        self.assertEqual(payload["elapsed_s"], 56)
        self.assertIsInstance(payload["elapsed_s"], int)


if __name__ == "__main__":
    unittest.main()
