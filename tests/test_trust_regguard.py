"""Regression guard tests (Task D6-TRUE, plan item 36, Wave D).

`agent/trust/regguard.py` turns fixed bugs into permanent specs: a
registry (`agent/trust/regressions.json`) records fixed case ids + their
green checksums, and a re-fire function re-runs the recorded specs
(REAL Verilator via vbackend by default, injected fake sim_fn in unit
tests) and reports any regression. CI calls it WITHOUT touching
`scripts/ci.sh`: either the standalone check
`python3 agent/trust/regguard.py --check` or the suite-callable
`check_regressions()`.

- Unit tests use an injected fake sim_fn: fast, offline, deterministic.
- TestLiveRefirePicosocUartDiv uses the REAL Verilator backend against
  the seeded entry (locked live bundle
  runs/live-20261026T174854Z-picosoc-uart-div) and asserts the re-fire
  stays green with an identical checksum (sim-only proof, no API key).

RED-first TDD: this file was written BEFORE agent/trust/regguard.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_regguard -v   (from repo root)
"""
import hashlib
import json
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import regguard  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_PATH = os.path.join(ROOT, "agent", "trust", "regressions.json")
LIVE_BUNDLE = os.path.join(ROOT, "runs", "live-20261026T174854Z-picosoc-uart-div")
LIVE_BUNDLE_REL = "runs/live-20261026T174854Z-picosoc-uart-div"
LIVE_CASE = "picosoc-uart-div"
LIVE_CHECKSUM = "5c8f58d0f0818c97d014d2f851a6140882bd1efc96a2537047806252c0c2caf9"
LIVE_SEED = 42
LIVE_TOHOST = "PASS"

FAKE_BOOT_PASS = (
    "- tb noise line (excluded from checksum)\n"
    "cycle 0: reset assert\n"
    "loopback send 0xa5 got 0xa5\n"
    "tohost: PASS\n"
    "CYCLES=2652\n"
)

FAKE_BOOT_OTHER = (
    "cycle 0: reset assert\n"
    "loopback send 0xa5 got 0xe0\n"
    "tohost: FAIL 0xdead\n"
    "CYCLES=2112\n"
)


def _det_checksum(boot_text):
    det = [ln for ln in boot_text.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def _fake_sim_factory(boot_text):
    def _sim(case, seed, patch_text, work_dir):
        assert patch_text.strip(), "sim must receive the bundle patch"
        verdict = {"case": case, "pass": "tohost: PASS" in boot_text,
                   "tohost": "PASS" if "tohost: PASS" in boot_text else "FAIL 0xdead",
                   "checksum": _det_checksum(boot_text),
                   "cycles": 2652 if "PASS" in boot_text else 2112,
                   "seed": seed}
        return boot_text, verdict
    return _sim


def _entry(case=LIVE_CASE, checksum=_det_checksum(FAKE_BOOT_PASS),
           tohost="PASS", seed=42, bundle=None):
    # bundle=None: checksum-replay mode through the injected sim_fn
    # (no bundle dir needed); real-bundle tests build their own tmp dir.
    return {"case": case, "checksum": checksum, "tohost": tohost,
            "seed": seed, "bundle": bundle}


class TestSeededRegistry(unittest.TestCase):
    def test_registry_file_exists_in_repo(self):
        self.assertTrue(os.path.isfile(REGISTRY_PATH),
                        "missing registry: %s" % REGISTRY_PATH)

    def test_registry_seeded_from_real_green_bundle(self):
        reg = regguard.load_registry()
        self.assertIn("cases", reg)
        by_case = {e["case"]: e for e in reg["cases"]}
        self.assertIn(LIVE_CASE, by_case, reg)
        entry = by_case[LIVE_CASE]
        self.assertEqual(entry["checksum"], LIVE_CHECKSUM)
        self.assertEqual(entry["tohost"], LIVE_TOHOST)
        self.assertEqual(int(entry["seed"]), LIVE_SEED)
        self.assertEqual(entry["bundle"], LIVE_BUNDLE_REL)

    def test_registry_lives_under_agent_trust_not_eval(self):
        self.assertTrue(REGISTRY_PATH.endswith("agent/trust/regressions.json"))
        self.assertFalse(os.path.isfile(
            os.path.join(ROOT, "eval", "regression_registry.md")))


class TestRecordFixed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trust-regguard-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg_path = os.path.join(self.tmp, "regressions.json")

    def test_record_then_reload_roundtrip(self):
        entry = regguard.record_fixed(
            "picosoc-uart-div", LIVE_CHECKSUM, tohost="PASS", seed=42,
            bundle=LIVE_BUNDLE_REL, path=self.reg_path)
        self.assertEqual(entry["case"], "picosoc-uart-div")
        reg = regguard.load_registry(self.reg_path)
        by_case = {e["case"]: e for e in reg["cases"]}
        self.assertEqual(by_case["picosoc-uart-div"]["checksum"], LIVE_CHECKSUM)

    def test_duplicate_case_updates_instead_of_duplicating(self):
        regguard.record_fixed("c1", "aa", path=self.reg_path)
        regguard.record_fixed("c1", "bb", path=self.reg_path)
        reg = regguard.load_registry(self.reg_path)
        matches = [e for e in reg["cases"] if e["case"] == "c1"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["checksum"], "bb")

    def test_record_from_live_green_bundle(self):
        entry = regguard.record_from_bundle(LIVE_BUNDLE, path=self.reg_path)
        self.assertEqual(entry["case"], LIVE_CASE)
        self.assertEqual(entry["checksum"], LIVE_CHECKSUM)
        self.assertEqual(entry["tohost"], LIVE_TOHOST)

    def test_record_from_red_bundle_refuses(self):
        red = os.path.join(self.tmp, "red-bundle")
        os.makedirs(os.path.join(red, "artifacts"))
        with open(os.path.join(red, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"case": "c-red", "seed": 42}, fh)
        with open(os.path.join(red, "patch.diff"), "w", encoding="utf-8") as fh:
            fh.write("diff --git a/x b/x\n")
        with open(os.path.join(red, "artifacts", "verdict_after.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"case": "c-red", "pass": False, "tohost": "FAIL 0xdead",
                       "checksum": "00", "seed": 42}, fh)
        with self.assertRaises(ValueError):
            regguard.record_from_bundle(red, path=self.reg_path)


class TestRefireEntry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trust-regguard-refire-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bundle = os.path.join(self.tmp, "bundle")
        os.makedirs(os.path.join(self.bundle, "artifacts"))
        with open(os.path.join(self.bundle, "meta.json"), "w",
                   encoding="utf-8") as fh:
            json.dump({"case": "c1", "seed": 7}, fh)
        with open(os.path.join(self.bundle, "patch.diff"), "w",
                   encoding="utf-8") as fh:
            fh.write("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@\n-x\n+y\n")
        with open(os.path.join(self.bundle, "artifacts", "verdict_after.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"case": "c1", "pass": True, "tohost": "PASS",
                       "checksum": _det_checksum(FAKE_BOOT_PASS),
                       "seed": 7}, fh)

    def _entry_for_bundle(self):
        return {"case": "c1", "checksum": _det_checksum(FAKE_BOOT_PASS),
                "tohost": "PASS", "seed": 7, "bundle": self.bundle}

    def test_matching_refire_stays_green(self):
        res = regguard.refire_entry(
            self._entry_for_bundle(),
            sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["ok"], res)
        self.assertIn("MATCH", res["reason"])

    def test_diverged_checksum_is_regression(self):
        res = regguard.refire_entry(
            self._entry_for_bundle(),
            sim_fn=_fake_sim_factory(FAKE_BOOT_OTHER))
        self.assertFalse(res["ok"], res)
        self.assertIn("MISMATCH", res["reason"])
        self.assertNotEqual(res["replayed_checksum"],
                            res["expected_checksum"])

    def test_missing_bundle_reports_not_ok(self):
        entry = self._entry_for_bundle()
        entry["bundle"] = os.path.join(self.tmp, "no-such-bundle")
        res = regguard.refire_entry(
            entry, sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertFalse(res["ok"], res)

    def test_tampered_expected_checksum_is_regression(self):
        entry = self._entry_for_bundle()
        bad = ("0" if entry["checksum"][0] != "0" else "1") + entry["checksum"][1:]
        entry["checksum"] = bad
        res = regguard.refire_entry(
            entry, sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertFalse(res["ok"], res)
        self.assertIn("MISMATCH", res["reason"])


class TestRefireAll(unittest.TestCase):
    def test_all_green_aggregates_ok(self):
        reg = {"version": 1, "cases": [
            _entry(case="c1"), _entry(case="c2")]}
        res = regguard.refire_all(
            reg, sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["passed"], 2)
        self.assertEqual(res["failed"], 0)

    def test_one_regression_fails_the_gate(self):
        reg = {"version": 1, "cases": [
            _entry(case="c1", checksum=_det_checksum(FAKE_BOOT_PASS)),
            _entry(case="c2", checksum="0" * 64)]}
        res = regguard.refire_all(
            reg, sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["failed"], 1)
        self.assertEqual(res["passed"], 1)

    def test_empty_registry_is_vacuously_green(self):
        res = regguard.refire_all({"version": 1, "cases": []},
                                  sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["total"], 0)

    def test_check_regressions_is_suite_callable(self):
        # CI hook without touching scripts/ci.sh: loads the default
        # registry path (overridable) and re-fires every entry.
        tmp_reg = os.path.join(tempfile.mkdtemp(prefix="trust-regguard-ci-"),
                               "regressions.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(tmp_reg), True)
        regguard.record_fixed("c1", _det_checksum(FAKE_BOOT_PASS),
                              bundle=None, path=tmp_reg)
        # bundle=None entries replay from recorded checksums via the
        # fake sim (no bundle dir needed when sim_fn ignores patch source).
        res = regguard.check_regressions(
            path=tmp_reg, sim_fn=_fake_sim_factory(FAKE_BOOT_PASS))
        self.assertTrue(res["ok"], res)


class TestLiveRefirePicosocUartDiv(unittest.TestCase):
    """Sim-only proof: re-fire of the fixed spec stays green (real Verilator)."""

    def test_live_seeded_spec_stays_green(self):
        self.assertTrue(os.path.isfile(REGISTRY_PATH),
                        "registry missing: %s" % REGISTRY_PATH)
        reg = regguard.load_registry()
        by_case = {e["case"]: e for e in reg["cases"]}
        self.assertIn(LIVE_CASE, by_case)
        res = regguard.refire_entry(by_case[LIVE_CASE])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["replayed_checksum"], LIVE_CHECKSUM)
        self.assertEqual(res["expected_checksum"], LIVE_CHECKSUM)


if __name__ == "__main__":
    unittest.main()
