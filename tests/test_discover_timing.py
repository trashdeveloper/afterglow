"""Task A9 (Timing stressor) red-first tests: agent/discover/timing.py.

- Varies `+seed` and clock-edge sampling parameters across runs of the
  same DUT+TB (build once via sim/vbackend.py primitives, run per seed),
  collects per-seed verdicts via the decide_verdict oracle marker, and
  flags order-dependent PASS/FAIL flips: stable (all agree) or flaky
  (divergent seeds with the exact flip pair + logs).
- Sim errors raise loudly, never counted as verdicts.

Run: python3 tests/test_discover_timing.py -v   (from repo root)
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import timing  # noqa: E402  (RED before implementation: ModuleNotFoundError)


def _tiny_case():
    return {
        "name": "timing-tiny",
        "project": "picorv32",
        "files": ["picorv32.v"],
        "top": "tb_picorv32_fetch",
        "tb": "picorv32-fetch_tb.sv",
        "oracle_pass": "tohost: PASS",
    }


PASS_LOG = "tohost: PASS\nCYCLES=2000\n- tb_picorv32_fetch seed 42\n"
FAIL_LOG = ("tohost: FAIL 0xdead\nTRAP: fetch misalign addr low bits "
            "nonzero\nCYCLES=2000\n")


def _counting_build(calls, fail=False):
    def _build(tree, case):
        calls.append((tree, dict(case)))
        if fail:
            raise RuntimeError("verilator build failed (exit 1): boom")
        return "/fake/binary"
    return _build


def _seed_keyed_run(log_by_seed, calls=None, fail_seeds=()):
    def _run(binary, seed, config):
        if calls is not None:
            calls.append((binary, int(seed), dict(config)))
        if int(seed) in tuple(fail_seeds):
            raise RuntimeError("sim error: seed %r crashed" % (seed,))
        if int(seed) not in log_by_seed:
            raise RuntimeError("sim error: seed %r failed" % (seed,))
        return log_by_seed[int(seed)]
    return _run


class TestStableDeterministic(unittest.TestCase):
    def test_all_pass_stub_reports_stable(self):
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [11, 22, 33],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({11: PASS_LOG, 22: PASS_LOG,
                                    33: PASS_LOG}))
        self.assertEqual(report["case"], "timing-tiny")
        self.assertEqual(report["seeds"], [11, 22, 33])
        self.assertEqual(report["n"], 3)
        self.assertTrue(report["stable"])
        self.assertEqual(report["flips"], [])
        self.assertEqual(report["n_pass"], 3)
        self.assertEqual(report["n_fail"], 0)
        self.assertEqual(len(report["records"]), 3)
        for rec, seed in zip(report["records"], [11, 22, 33]):
            self.assertEqual(rec["seed"], seed)
            self.assertTrue(rec["pass"])
            self.assertEqual(rec["log"], PASS_LOG)

    def test_all_fail_stub_reports_stable_not_flaky(self):
        # Agreement is agreement: deterministic RED on every seed is
        # stable (a real bug, not a flake) -- never reported as flaky.
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [11, 22],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({11: FAIL_LOG, 22: FAIL_LOG}))
        self.assertTrue(report["stable"])
        self.assertEqual(report["flips"], [])
        self.assertEqual(report["n_pass"], 0)
        self.assertEqual(report["n_fail"], 2)

    def test_build_runs_once_for_n_seeds(self):
        builds = []
        calls = []
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [1, 2, 3],
            build_fn=_counting_build(builds),
            run_fn=_seed_keyed_run({1: PASS_LOG, 2: PASS_LOG,
                                    3: PASS_LOG}, calls))
        self.assertEqual(len(builds), 1)  # build once, run per seed
        self.assertEqual([s for _, s, _ in calls], [1, 2, 3])
        self.assertTrue(report["stable"])

    def test_verdicts_come_from_oracle_marker(self):
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [1, 2],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({1: PASS_LOG, 2: FAIL_LOG}))
        by_seed = {r["seed"]: r for r in report["records"]}
        self.assertTrue(by_seed[1]["pass"])
        self.assertFalse(by_seed[2]["pass"])


class TestFlipFlagged(unittest.TestCase):
    def test_single_flipped_seed_flagged_with_exact_pair(self):
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [1, 2, 3],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({1: PASS_LOG, 2: FAIL_LOG,
                                    3: PASS_LOG}))
        self.assertFalse(report["stable"])
        self.assertEqual(report["n_pass"], 2)
        self.assertEqual(report["n_fail"], 1)
        pairs = {(f["seed_a"], f["seed_b"]) for f in report["flips"]}
        self.assertIn((1, 2), pairs)
        self.assertIn((2, 3), pairs)
        self.assertNotIn((1, 3), pairs)  # agreeing pair stays silent
        flip = next(f for f in report["flips"]
                    if (f["seed_a"], f["seed_b"]) == (1, 2))
        self.assertTrue(flip["pass_a"])
        self.assertFalse(flip["pass_b"])
        # The flip carries both logs verbatim.
        self.assertEqual(flip["log_a"], PASS_LOG)
        self.assertEqual(flip["log_b"], FAIL_LOG)

    def test_sampling_dimension_flip_names_configs(self):
        sampling = [
            {"name": "posedge-sample", "plusargs": []},
            {"name": "negedge-sample", "plusargs": ["+sample_edge=neg"]},
        ]

        def _run(binary, seed, config):
            if config["name"] == "negedge-sample":
                return FAIL_LOG
            return PASS_LOG

        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [42, 42],
            sampling=sampling,
            build_fn=_counting_build([]),
            run_fn=_run)
        self.assertEqual(report["n"], 4)  # 2 seeds x 2 configs
        self.assertFalse(report["stable"])
        self.assertTrue(report["flips"])
        for flip in report["flips"]:
            self.assertNotEqual(flip["sampling_a"], flip["sampling_b"])
            self.assertEqual(
                flip["pass_a"],
                flip["sampling_a"] == "posedge-sample")
            self.assertEqual(
                flip["pass_b"],
                flip["sampling_b"] == "posedge-sample")
        pairs = {(f["sampling_a"], f["sampling_b"])
                 for f in report["flips"]}
        self.assertIn(("posedge-sample", "negedge-sample"), pairs)

    def test_same_seed_twice_agrees(self):
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [7, 7],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({7: PASS_LOG}))
        self.assertTrue(report["stable"])
        self.assertEqual(report["flips"], [])

    def test_default_run_passes_seed_plus_sampling_plusargs(self):
        seen = []

        def _run(binary, seed, config):
            seen.append((binary, int(seed), list(config["plusargs"])))
            return PASS_LOG

        sampling = [{"name": "half-cycle", "plusargs": ["+sample_dly=1"]}]
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [5, 6], sampling=sampling,
            build_fn=_counting_build([]), run_fn=_run)
        self.assertTrue(report["stable"])
        self.assertEqual([s for _, s, _ in seen], [5, 6])
        for _, _, plus in seen:
            self.assertIn("+sample_dly=1", plus)


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_build_failure_raises(self):
        with self.assertRaises(RuntimeError):
            timing.stress_timing(
                "/fake/tree", _tiny_case(), [42, 43],
                build_fn=_counting_build([], fail=True),
                run_fn=_seed_keyed_run({42: PASS_LOG, 43: PASS_LOG}))

    def test_run_error_raises_never_counted_as_verdict(self):
        with self.assertRaises(RuntimeError):
            timing.stress_timing(
                "/fake/tree", _tiny_case(), [42, 43],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({42: PASS_LOG}, fail_seeds=(43,)))

    def test_non_string_log_raises(self):
        with self.assertRaises(TypeError):
            timing.stress_timing(
                "/fake/tree", _tiny_case(), [42, 43],
                build_fn=_counting_build([]),
                run_fn=lambda binary, seed, config: 12345)

    def test_empty_log_raises(self):
        with self.assertRaises(RuntimeError):
            timing.stress_timing(
                "/fake/tree", _tiny_case(), [42, 43],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({42: PASS_LOG, 43: ""}))

    def test_too_few_seeds_raises(self):
        with self.assertRaises(ValueError):
            timing.stress_timing(
                "/fake/tree", _tiny_case(), [42],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({42: PASS_LOG}))
        with self.assertRaises(ValueError):
            timing.stress_timing(
                "/fake/tree", _tiny_case(), [],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({}))

    def test_blank_oracle_raises(self):
        case = _tiny_case()
        case["oracle_pass"] = "   "
        with self.assertRaises(ValueError):
            timing.stress_timing(
                "/fake/tree", case, [42, 43],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({42: PASS_LOG, 43: PASS_LOG}))

    def test_bad_sampling_config_raises(self):
        with self.assertRaises(ValueError):
            timing.stress_timing(
                "/fake/tree", _tiny_case(), [42, 43], sampling=[],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({42: PASS_LOG, 43: PASS_LOG}))
        with self.assertRaises(ValueError):
            timing.stress_timing(
                "/fake/tree", _tiny_case(), [42, 43],
                sampling=[{"name": "  "}],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({42: PASS_LOG, 43: PASS_LOG}))


class TestDiscoveryReport(unittest.TestCase):
    def test_stable_report_says_stable(self):
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [11, 22],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({11: PASS_LOG, 22: PASS_LOG}))
        path = os.path.join(tempfile.mkdtemp(prefix="timing-stable-"),
                            "report.md")
        timing.write_discovery_report(path, "timing-tiny", report)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("STABLE", text)
        self.assertIn("timing-tiny", text)

    def test_flaky_report_names_flip_pair(self):
        report = timing.stress_timing(
            "/fake/tree", _tiny_case(), [1, 2],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({1: PASS_LOG, 2: FAIL_LOG}))
        path = os.path.join(tempfile.mkdtemp(prefix="timing-flaky-"),
                            "report.md")
        timing.write_discovery_report(path, "timing-tiny", report)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("FLAKY", text)
        self.assertIn("1", text)
        self.assertIn("2", text)


if __name__ == "__main__":
    unittest.main()
