"""Escalating attempt ladder 3->5 (TDD, fixtures only -- no API/keys/builds).

CONTEXT: MAX_ATTEMPTS=3 is the binding constraint on hard bugs. With
diversity + failure-feedback + flake accounting in place, attempts 4-5
carry strictly more signal than attempts 1-3 ever had: the full
prior-attempts block plus an explicit STRATEGY-CHANGE directive.
Attempts 1-3 behave EXACTLY as today; the ladder unlocks ONLY when
attempts 1-3 all produced completed model responses that all went RED
with observable behavioral signal, in three distinct directions, with
zero transport failures (flakes never count), and accumulated est cost
under $0.50.

What this file proves (mocked seams, static fixtures):
- unlock: 3 distinct-direction completed responses, each resim RED with
  a changed checksum, zero transport failures, est under cap ->
  attempts 4 AND 5 fire; every ladder prompt carries the full
  PRIOR_ATTEMPTS block + the verbatim STRATEGY-CHANGE directive (in
  the logged text_request.json); meta ladder_used true.
- flakes: 3 exhausted transport sequences -> NO attempt 4, the loud
  transport error is preserved, ladder_used false.
- cost cap: fabricated over-cap cost ledger -> attempt 4 blocked,
  ladder_used false, ladder_cost_cap recorded in meta.
- base preservation: 3 REDs WITHOUT behavioral signal (constant
  checksum, exactly like every existing mocked fixture) stop at 3
  with no STRATEGY-CHANGE anywhere and ladder_used false -- this is
  why the existing suite passes unmodified.
- pure predicates: ladder_eligible / ladder_cost_ok / ladder_text_usd.

Run: PYTHONPATH=. python3 tests/test_attempt_ladder.py -v  (repo root)
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import loop

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASE = "uart-start-half"

FAKE_BOOT_RED = (
    "cycle 0: reset assert\n"
    "cycle 1: fetch pc=0x00\n"
    "TRAP: uart recv_divcnt mismatch got 0xE0 want 0xA5\n"
    "tohost: FAIL 0xdead\n"
    "CYCLES=1234\n"
)
FAKE_VERDICT = {"case": CASE, "pass": False, "tohost": "FAIL 0xdead",
                "checksum": "deadbeef", "cycles": 1234, "seed": 42}

# Five mutually-distinct directions (file and/or hunk lines differ, so
# direction_key differs pairwise -- the STRATEGY-CHANGE claim stays true).
DIFF_L1 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1446,4 +1446,4 @@
-wire decoder_trigger <= mem_do_rinst || mem_done;
+wire decoder_trigger <= mem_do_rinst;
"""

DIFF_L2 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -377,1 +377,1 @@
-reg mem_la_read = 0;
+reg mem_la_read = 1;
"""

DIFF_L3 = """diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v
--- a/picosoc/simpleuart.v
+++ b/picosoc/simpleuart.v
@@ -10,1 +10,1 @@
-recv_divcnt <= recv_divcnt + 1;
+recv_divcnt <= recv_divcnt + 2;
"""

DIFF_L4 = """diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -500,1 +500,1 @@
-reg instr_lui = 0;
+reg instr_lui = 1;
"""

DIFF_L5 = """diff --git a/ibex_cs_registers.sv b/ibex_cs_registers.sv
--- a/ibex_cs_registers.sv
+++ b/ibex_cs_registers.sv
@@ -20,1 +20,1 @@
-csr_rdata <= 32'h0;
+csr_rdata <= 32'h1;
"""

REAL_USAGE = {"prompt_tokens": 800, "completion_tokens": 1200,
              "total_tokens": 2000}


def _fake_red_sim(case, seed, boot_path, verdict_path):
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write(FAKE_BOOT_RED)
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(FAKE_VERDICT, case=case, seed=seed)) + "\n")
    return 1, "mock RED"


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _resp(diff, n, usage=None):
    return ({"content": json.dumps(
        {"diagnosis": "mock diagnosis %d" % n,
         "patch_diff": diff, "expected": "tohost: PASS"}),
        "raw": {}, "latency_ms": 5,
        "request_id": "mock-r%d" % n,
        "model": loop.TEXT_MODEL, "usage": dict(usage or {})}, [])


def _resim_red_sequence(checksums):
    """Fake resim_after: every resim RED, checksums consumed in order."""
    state = {"n": 0}

    def _fake(tree, case, seed, boot_path, verdict_path):
        idx = min(state["n"], len(checksums) - 1)
        state["n"] += 1
        with open(boot_path, "w", encoding="utf-8") as fh:
            fh.write("mock boot after\ntohost: FAIL 0xdead\n")
        verdict = {"case": case["name"], "pass": False,
                   "tohost": "FAIL 0xdead",
                   "checksum": checksums[idx],
                   "cycles": 2009, "seed": seed,
                   "sim_time_utc": loop.utc_now()}
        with open(verdict_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(verdict) + "\n")
        return verdict

    return _fake


def _run_loop(run_id, effects, resim_fn, seed_cost_json=None):
    """Run loop.main with mocked seams; returns (rc, run_dir, prompts)."""
    run_dir = os.path.join(ROOT, "runs", run_id)
    prompts = []

    def fake_call(prompt):
        prompts.append(prompt)
        eff = effects[len(prompts) - 1]
        if isinstance(eff, Exception):
            raise eff
        return eff

    def fake_apply(diff_text, tree, tmp_path, spec=None):
        return {"guard_ok": True, "reasons": [], "applied": True,
                "repaired": False, "patch_lines": 2,
                "files": ["picorv32.v"], "applied_diff": diff_text}

    patches = [
        mock.patch.object(loop, "run_sim_before",
                          side_effect=_fake_red_sim),
        mock.patch.object(loop, "rtl_excerpt",
                          return_value="line 1: fake excerpt"),
        mock.patch.object(loop, "call_text_with_backoff",
                          side_effect=fake_call),
        mock.patch.object(loop, "try_apply_diff",
                          side_effect=fake_apply),
        mock.patch.object(loop, "resim_after", side_effect=resim_fn),
        mock.patch.object(loop.visual, "generate", return_value={
            "latency_ms": 1, "request_id": "img-mock", "bytes": 10,
            "sha256": "ab" * 32, "transport": "mock",
            "model": "mock-image"}),
    ]
    for p in patches:
        p.start()
    try:
        if seed_cost_json is not None:
            os.makedirs(os.path.join(run_dir, "api"), exist_ok=True)
            with open(os.path.join(run_dir, "cost.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(seed_cost_json) + "\n")
        v3 = os.path.join(ROOT, "agent", "prompts", "v3.txt")
        rc = loop.main(["--case", CASE, "--seed", "42",
                        "--run-id", run_id, "--prompt", v3])
    finally:
        for p in patches:
            try:
                p.stop()
            except RuntimeError:
                pass
    return rc, run_dir, prompts


class LadderUnlock(unittest.TestCase):
    def tearDown(self):
        for rid in ("test-ladder-unlock", "test-ladder-flakes",
                    "test-ladder-costcap", "test-ladder-base"):
            shutil.rmtree(os.path.join(ROOT, "runs", rid), ignore_errors=True)

    def _start(self, run_id, effects, resim_fn, seed_cost_json=None):
        run_dir = os.path.join(ROOT, "runs", run_id)
        shutil.rmtree(run_dir, ignore_errors=True)
        return _run_loop(run_id, effects, resim_fn, seed_cost_json)

    def test_unlocks_on_3_signal_RED(self):
        diffs = [DIFF_L1, DIFF_L2, DIFF_L3, DIFF_L4, DIFF_L5]
        effects = [_resp(d, i + 1, REAL_USAGE)
                   for i, d in enumerate(diffs)]
        resim_fn = _resim_red_sequence(
            ["ladder-c%d" % i for i in range(1, 6)])
        rc, run_dir, prompts = self._start(
            "test-ladder-unlock", effects, resim_fn)
        self.assertEqual(rc, 1)  # all five resims RED
        self.assertEqual(len(prompts), 5)  # 3 base + 2 ladder
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 5)
        self.assertEqual(meta.get("transport_failures"), 0)
        self.assertTrue(meta.get("ladder_used"))
        self.assertIn("unlocked-3-signal-RED",
                      meta.get("ladder_reason", ""))
        # Ladder prompts carry the full block + the verbatim directive;
        # base prompts are untouched by it.
        for p in prompts[:3]:
            self.assertNotIn("STRATEGY-CHANGE", p)
        for p in prompts[3:]:
            self.assertIn("STRATEGY-CHANGE", p)
            self.assertIn("PRIOR_ATTEMPTS", p)
            self.assertIn("qualitatively different repair class", p)
        treq = _read_json(os.path.join(run_dir, "api", "text_request.json"))
        self.assertIn("STRATEGY-CHANGE", treq.get("prompt", ""))
        self.assertIn("combinational logic", treq.get("prompt", ""))
        self.assertIn("sequential/reset/enable logic",
                      treq.get("prompt", ""))
        self.assertTrue(meta["attempts"][3].get("ladder"))
        self.assertTrue(meta["attempts"][4].get("ladder"))
        self.assertNotIn("ladder", meta["attempts"][0])

    def test_flakes_dont_unlock(self):
        effects = [loop.LiveAPIError("mock flake: EMPTY content") for _ in range(3)]
        resim_fn = _resim_red_sequence(["ladder-c1"])
        rc, run_dir, prompts = self._start(
            "test-ladder-flakes", effects, resim_fn)
        self.assertEqual(rc, 2)  # bounded degrade to triage, never RED-hang
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 0)
        self.assertEqual(meta.get("transport_failures"),
                         loop.MAX_TRANSPORT_FAILURES)
        self.assertFalse(meta.get("ladder_used"))
        # Loud error preserved (no heuristic fallback, no silent ladder).
        self.assertIn("transport", meta.get("error", "").lower())
        for p in prompts:
            self.assertNotIn("STRATEGY-CHANGE", p)

    def test_cost_cap_blocks_attempt_4(self):
        diffs = [DIFF_L1, DIFF_L2, DIFF_L3]
        effects = [_resp(d, i + 1, REAL_USAGE)
                   for i, d in enumerate(diffs)]
        resim_fn = _resim_red_sequence(
            ["ladder-c%d" % i for i in range(1, 4)])
        rc, run_dir, prompts = self._start(
            "test-ladder-costcap", effects, resim_fn,
            seed_cost_json={"est_usd": 0.99, "text_tokens": 99999,
                            "image_calls": 3})
        self.assertEqual(rc, 1)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 3)
        self.assertFalse(meta.get("ladder_used"))
        self.assertIn("ladder_cost_cap", meta.get("ladder_reason", ""))
        self.assertEqual(len(prompts), 3)
        for p in prompts:
            self.assertNotIn("STRATEGY-CHANGE", p)

    def test_signal_less_RED_stops_at_3(self):
        # Same shape as every existing mocked fixture (constant checksum
        # == no behavioral signal): the ladder must NOT fire, so the
        # existing suite passes unmodified.
        diffs = [DIFF_L1, DIFF_L2, DIFF_L3]
        effects = [_resp(d, i + 1, REAL_USAGE)
                   for i, d in enumerate(diffs)]
        resim_fn = _resim_red_sequence(["deadbeef"] * 3)
        rc, run_dir, prompts = self._start(
            "test-ladder-base", effects, resim_fn)
        self.assertEqual(rc, 1)
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        self.assertEqual(meta.get("attempts_consumed"), 3)
        self.assertFalse(meta.get("ladder_used"))
        self.assertIn("behavioral-signal", meta.get("ladder_reason", ""))
        for p in prompts:
            self.assertNotIn("STRATEGY-CHANGE", p)


class LadderPredicates(unittest.TestCase):
    def _row(self, n, resim="RED (tohost=FAIL 0xdead)", changed="yes",
             direction=None):
        return {"attempt": n, "patch": "one-liner-%d" % n,
                "direction": direction or ("file%d:1:replace" % n),
                "resim": resim, "checksum_changed": changed}

    def test_eligible_on_3_signal_RED(self):
        ok, why = loop.ladder_eligible(
            [self._row(1), self._row(2), self._row(3)], 3, 0)
        self.assertTrue(ok)
        self.assertIn("unlocked-3-signal-RED", why)

    def test_flakes_never_unlock(self):
        ok, why = loop.ladder_eligible(
            [self._row(1), self._row(2), self._row(3)], 3, 1)
        self.assertFalse(ok)
        self.assertIn("transport-failures", why)

    def test_non_RED_rows_dont_unlock(self):
        for bad in ("not-run (guard-rejected; bootstrap refused)",
                    "not-run (unparseable model output)"):
            rows = [self._row(1), self._row(2),
                    self._row(3, resim=bad, changed="n/a")]
            ok, _ = loop.ladder_eligible(rows, 3, 0)
            self.assertFalse(ok, msg=bad)

    def test_unchanged_checksum_dont_unlock(self):
        rows = [self._row(1), self._row(2),
                self._row(3, changed="no")]
        ok, why = loop.ladder_eligible(rows, 3, 0)
        self.assertFalse(ok)
        self.assertIn("behavioral-signal", why)

    def test_repeated_direction_dont_unlock(self):
        rows = [self._row(1, direction="same:1:replace"),
                self._row(2, direction="same:1:replace"),
                self._row(3, direction="same:1:replace")]
        ok, why = loop.ladder_eligible(rows, 3, 0)
        self.assertFalse(ok)
        self.assertIn("diversity", why)

    def test_base_budget_remaining(self):
        ok, _ = loop.ladder_eligible([self._row(1)], 1, 0)
        self.assertFalse(ok)

    def test_cost_ok_ledger_over_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "cost.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({"est_usd": 0.99}) + "\n")
            ok, est, src = loop.ladder_cost_ok(tmp, 0.01, 3)
            self.assertFalse(ok)
            self.assertEqual(src, "ledger")
            self.assertAlmostEqual(est, 0.99)

    def test_cost_ok_ledger_under_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "cost.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({"est_usd": 0.03}) + "\n")
            ok, est, src = loop.ladder_cost_ok(tmp, 0.01, 3)
            self.assertTrue(ok)
            self.assertEqual(src, "ledger")

    def test_cost_ok_tracked_and_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, est, src = loop.ladder_cost_ok(tmp, 0.12, 3)
            self.assertTrue(ok)
            self.assertEqual(src, "tracked")
            self.assertAlmostEqual(est, 0.12)
            ok, est, src = loop.ladder_cost_ok(tmp, 0.0, 3)
            self.assertTrue(ok)
            self.assertEqual(src, "proxy")
            self.assertAlmostEqual(
                est, 3 * loop.LADDER_PROXY_USD_PER_ATTEMPT)

    def test_text_usd_math(self):
        self.assertAlmostEqual(
            loop.ladder_text_usd(dict(REAL_USAGE), loop.TEXT_MODEL),
            800 / 1000 * 0.002 + 1200 / 1000 * 0.008)
        self.assertEqual(loop.ladder_text_usd({}, loop.TEXT_MODEL), 0.0)

    def test_directive_verbatim(self):
        d = loop.STRATEGY_CHANGE_DIRECTIVE
        self.assertIn("STRATEGY-CHANGE", d)
        self.assertIn("two/three distinct directions failed", d)
        self.assertIn("qualitatively different repair class", d)
        self.assertIn("combinational logic", d)
        self.assertIn("sequential/reset/enable logic", d)
        self.assertIn("vice versa", d)


if __name__ == "__main__":
    unittest.main()
