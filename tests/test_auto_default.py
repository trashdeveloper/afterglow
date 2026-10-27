"""Full-auto-by-default tests: `afterglow run --spec` with no flags attempts
diagnose -> patch -> resim -> verify end-to-end.

Run: python3 tests/test_auto_default.py -v   (from repo root)

Static fixtures + unittest.mock seams only -- no live API calls, no keys,
no real sim builds:

- test_default_is_auto: no flags -> mode "live", patch NOT withheld.
- test_triage_flag_withholds: --triage / --stop-before-patch -> triage.
- test_auto_flag_compat: --auto still accepted and means live.
- test_degrade_api_flake_to_triage: injected LiveAPIError on every
  diagnose attempt -> exit 2, meta status "triage" (mode stays "live",
  degraded recorded, error kept), bundle keeps its shape, and the
  bundle scores as a TRIAGE row (never silently green/red).
- test_degrade_guard_reject_to_triage: injected guard refusal on every
  attempt (diagnosis names no gate signal, so no bootstrap) -> exit 2
  triage with the rejected diff kept.
- test_resim_red_stays_red: patch applies but resim stays RED
  through all retries -> exit 1 red, never exit 2 triage.
- test_degraded_triage_scores_triage_row: a mode-live/status-triage
  bundle renders TRIAGE via eval/score.py (not GREEN, never UNRUN).
- test_cli_help_mentions_autonomy: `afterglow run --help` documents the
  default-auto contract and the triage opt-out flag.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop
from eval import score

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AFTERGLOW = os.path.join(ROOT, "afterglow")
CASE = "uart-start-half"

FAKE_BOOT_RED = (
    "cycle 0: reset assert\n"
    "cycle 1: fetch pc=0x00\n"
    "TRAP: test trap\n"
    "tohost: FAIL 0x1\n"
    "CYCLES=1234\n"
)

MODEL_OK = {
    "content": json.dumps({
        "diagnosis": "receiver clock tree glitch delays bit capture",
        "expected": "tohost: PASS",
        "patch_diff": (
            "diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v\n"
            "--- a/picosoc/simpleuart.v\n"
            "+++ b/picosoc/simpleuart.v\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    }),
    "raw": {"id": "mock-id"},
    "latency_ms": 7,
    "request_id": "mock-req",
    "model": loop.TEXT_MODEL,
    "usage": {},
}

IMG_OK = {"latency_ms": 5, "request_id": "mock-img", "bytes": 100,
          "sha256": "abc123", "transport": "mock",
          "model": "muse-image-1.0"}


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _fake_red_sim(case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write(FAKE_BOOT_RED)
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case": case, "pass": False,
                             "tohost": "FAIL 0x1",
                             "checksum": "deadbeef", "cycles": 1234,
                             "seed": seed}) + "\n")
    return 1, "mock RED"


def _run_loop_main(run_id):
    return loop.main(["--case", CASE, "--seed", "42",
                      "--run-id", run_id])


def _mocked_loop(patches):
    """Apply mock.patch list; returns the started patchers' mocks."""
    started = [p.start() for p in patches]
    return started


class TestAutoDefault(unittest.TestCase):
    def test_default_is_auto(self):
        args = loop.parse_args(["--case", "dummy"])
        self.assertEqual(loop.resolve_mode(args), "live")
        self.assertIs(loop.should_withhold_patch(args), False)

    def test_triage_flag_withholds(self):
        for flag in ("--triage", "--stop-before-patch"):
            args = loop.parse_args(["--case", "dummy", flag])
            self.assertEqual(loop.resolve_mode(args), "triage",
                             msg=flag)
            self.assertIs(loop.should_withhold_patch(args), True,
                          msg=flag)

    def test_auto_flag_compat(self):
        args = loop.parse_args(["--case", "dummy", "--auto"])
        self.assertEqual(loop.resolve_mode(args), "live")
        self.assertIs(loop.should_withhold_patch(args), False)

    def _base_patches(self, run_dir_holder):
        return [
            mock.patch.object(loop, "run_sim_before",
                              side_effect=_fake_red_sim),
            mock.patch.object(loop, "rtl_excerpt",
                              return_value="line 1: fake excerpt"),
        ]

    def test_degrade_api_flake_to_triage(self):
        run_id = "test-auto-degrade-apiflake"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        patches = self._base_patches(None) + [
            mock.patch.object(
                loop, "call_text_with_backoff",
                side_effect=loop.LiveAPIError("mock flake: down")),
        ]
        for p in patches:
            self.addCleanup(p.stop)
        _mocked_loop(patches)
        rc = _run_loop_main(run_id)
        self.assertEqual(rc, 2)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "triage")
        self.assertEqual(meta["mode"], "live")
        self.assertTrue(meta.get("degraded"))
        self.assertIn("error", meta)
        # Bundle keeps its shape: request persisted, response placeholder,
        # empty patch, RED verdict_after, cost ledger row with est_usd.
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "api", "text_request.json")))
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "api", "text_response.json")))
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "patch.diff")))
        verdict_after = _read_json(os.path.join(
            run_dir, "artifacts", "verdict_after.json"))
        self.assertIs(verdict_after["pass"], False)
        self.assertIn("est_usd", meta)

    def test_degrade_guard_reject_to_triage(self):
        run_id = "test-auto-degrade-guard"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)
        patches = self._base_patches(None) + [
            mock.patch.object(
                loop, "call_text_with_backoff",
                return_value=(dict(MODEL_OK), [])),
            mock.patch.object(loop.visual, "generate",
                              return_value=dict(IMG_OK)),
            mock.patch.object(
                loop, "try_apply_diff",
                return_value={"guard_ok": False,
                              "reasons": ["mock reject"],
                              "applied": False, "repaired": False,
                              "patch_lines": 0, "files": []}),
        ]
        for p in patches:
            self.addCleanup(p.stop)
        _mocked_loop(patches)
        rc = _run_loop_main(run_id)
        self.assertEqual(rc, 2)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "triage")
        self.assertEqual(meta["mode"], "live")
        self.assertTrue(meta.get("degraded"))
        self.assertTrue(meta.get("diagnosed"))
        # Rejected verbatim diff is kept as trust evidence.
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "rejected_model.diff")))
        self.assertIn("est_usd", meta)

    def test_resim_red_stays_red(self):
        run_id = "test-auto-degrade-resim"
        run_dir = os.path.join(ROOT, "runs", run_id)
        self.addCleanup(shutil.rmtree, run_dir, True)

        def _fake_resim(tree, case, seed, boot_path, verdict_path):
            with open(boot_path, "w", encoding="utf-8") as fh:
                fh.write("tohost: FAIL 0x1\nCYCLES=10\n")
            verdict = {"case": case["name"], "pass": False,
                       "tohost": "FAIL 0x1", "checksum": "c" * 64,
                       "cycles": 10, "seed": seed}
            with open(verdict_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(verdict) + "\n")
            return verdict

        patches = self._base_patches(None) + [
            mock.patch.object(
                loop, "call_text_with_backoff",
                return_value=(dict(MODEL_OK), [])),
            mock.patch.object(loop.visual, "generate",
                              return_value=dict(IMG_OK)),
            mock.patch.object(
                loop, "try_apply_diff",
                return_value={"guard_ok": True, "reasons": [],
                              "applied": True, "repaired": False,
                              "patch_lines": 2,
                              "files": ["picosoc/simpleuart.v"],
                              "applied_diff": "diff text\n"}),
            mock.patch.object(loop, "resim_after",
                              side_effect=_fake_resim),
        ]
        for p in patches:
            self.addCleanup(p.stop)
        _mocked_loop(patches)
        rc = _run_loop_main(run_id)
        self.assertEqual(rc, 1)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta["status"], "red")
        self.assertEqual(meta["mode"], "live")
        self.assertFalse(meta.get("degraded"))
        self.assertIn("est_usd", meta)

    def test_degraded_triage_scores_triage_row(self):
        # A live-mode run that degraded to triage must render TRIAGE:
        # never GREEN, never silently UNRUN.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "degraded-1"))
            with open(os.path.join(tmp, "degraded-1", "meta.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "case_id": CASE, "status": "triage",
                    "mode": "live", "degraded": True,
                    "wall_end_utc": "2026-10-26T19:00:00Z"}) + "\n")
            runs = score.load_runs(tmp)
            latest = score.latest_by_case(runs)
            cases = [{"id": CASE, "tier": "stress", "budget_s": "?"}]
            board, lg, lt, fg, ft = score.build_board(cases, latest)
            self.assertIn("TRIAGE", board)
            self.assertNotIn("GREEN", board)
            self.assertNotIn("UNRUN", board)
            self.assertEqual((lg, lt, fg, ft), (0, 0, 0, 1))

    def test_cli_help_mentions_autonomy(self):
        proc = subprocess.run(
            [AFTERGLOW, "run", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--spec", proc.stdout)
        self.assertIn("--stop-before-patch", proc.stdout)


if __name__ == "__main__":
    unittest.main()
