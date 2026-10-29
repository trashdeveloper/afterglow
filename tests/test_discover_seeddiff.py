"""Task A4 (Cross-seed differ) red-first tests: agent/discover/seeddiff.py.

- Runs the same DUT+TB over N seeds (build once, run per seed via
  sim/vbackend.py primitives), collects per-seed checksums/traces, diffs
  them: identical seeds -> no diff; injected nondeterminism -> flagged
  with the divergent seed pair + first-differing artifact.
- Sim errors raise loudly, never reported as clean agreement.

Run: python3 tests/test_discover_seeddiff.py -v   (from repo root)
"""
import hashlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import seeddiff  # noqa: E402  (RED before implementation: ModuleNotFoundError)


def _tiny_case():
    return {
        "name": "seeddiff-tiny",
        "project": "picorv32",
        "files": ["picorv32.v"],
        "top": "tb_picorv32_fetch",
        "tb": "picorv32-fetch_tb.sv",
        "oracle_pass": "tohost: PASS",
    }


def _det_checksum(log):
    det = [ln for ln in log.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def _counting_build(calls, fail=False):
    def _build(tree, case):
        calls.append((tree, dict(case)))
        if fail:
            raise RuntimeError("verilator build failed (exit 1): boom")
        return "/fake/binary"
    return _build


def _seed_keyed_run(log_by_seed, calls=None):
    def _run(binary, seed):
        if calls is not None:
            calls.append((binary, int(seed)))
        if int(seed) not in log_by_seed:
            raise RuntimeError("sim error: seed %r failed" % (seed,))
        return log_by_seed[int(seed)]
    return _run


class TestSameSeedTwiceNoDiff(unittest.TestCase):
    def test_same_seed_twice_yields_pinned_no_diff_report(self):
        log = "tohost: PASS\nCYCLES=10\n- Verilated model\n"
        calls = []
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [42, 42],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({42: log}, calls))
        # Pinned no-diff shape.
        self.assertEqual(report["case"], "seeddiff-tiny")
        self.assertEqual(report["seeds"], [42, 42])
        self.assertEqual(report["n"], 2)
        self.assertTrue(report["agree"])
        self.assertEqual(report["divergences"], [])
        self.assertEqual(len(report["records"]), 2)
        for rec in report["records"]:
            self.assertEqual(rec["checksum"], _det_checksum(log))
            self.assertEqual(rec["trace"], log)
        # Ran once per seed entry.
        self.assertEqual([s for _, s in calls], [42, 42])

    def test_build_runs_once_for_n_seeds(self):
        builds = []
        log = "tohost: PASS\nCYCLES=10\n"
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [1, 2, 3],
            build_fn=_counting_build(builds),
            run_fn=_seed_keyed_run({1: log, 2: log, 3: log}))
        self.assertEqual(len(builds), 1)  # build once, run per seed
        self.assertTrue(report["agree"])

    def test_distinct_seeds_identical_logs_agree(self):
        log = "tohost: PASS\nCYCLES=10\n"
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [7, 8, 9],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({7: log, 8: log, 9: log}))
        self.assertTrue(report["agree"])
        self.assertEqual(report["divergences"], [])


class TestInjectedNondeterminismFlagged(unittest.TestCase):
    def test_divergent_pair_flagged_with_first_diff(self):
        log_a = "tohost: PASS\nCYCLES=10\nREG x5=123\n"
        log_b = "tohost: PASS\nCYCLES=10\nREG x5=124\n"
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [11, 22],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({11: log_a, 22: log_b}))
        self.assertFalse(report["agree"])
        self.assertTrue(report["divergences"])
        # The exact divergent pair must be named.
        pairs = {(d["seed_a"], d["seed_b"]) for d in report["divergences"]}
        self.assertIn((11, 22), pairs)
        div = next(d for d in report["divergences"]
                   if (d["seed_a"], d["seed_b"]) == (11, 22))
        # First-differing artifact: line 3 (1-based), values pinned.
        fd = div["first_diff"]
        self.assertEqual(fd["line"], 3)
        self.assertEqual(fd["seed_a_line"], "REG x5=123")
        self.assertEqual(fd["seed_b_line"], "REG x5=124")
        # Checksums must differ too (checksum divergence, not just trace).
        self.assertNotEqual(_det_checksum(log_a), _det_checksum(log_b))
        self.assertFalse(div["checksum_match"])

    def test_trace_only_divergence_flagged(self):        # "- " prefixed lines are excluded from the checksum (vbackend
        # run_case convention) but are still trace: a difference there
        # is a trace mismatch with matching checksums.
        log_a = "tohost: PASS\nCYCLES=10\n- Verilated model A\n"
        log_b = "tohost: PASS\nCYCLES=10\n- Verilated model B\n"
        self.assertEqual(_det_checksum(log_a), _det_checksum(log_b))
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [11, 22],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({11: log_a, 22: log_b}))
        self.assertFalse(report["agree"])
        self.assertTrue(report["divergences"])
        div = report["divergences"][0]
        self.assertTrue(div["checksum_match"])
        self.assertFalse(div["trace_match"])

    def test_three_seeds_only_divergent_pair_flagged(self):
        log = "tohost: PASS\nCYCLES=10\n"
        odd = "tohost: PASS\nCYCLES=99\n"
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [1, 2, 3],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({1: log, 2: odd, 3: log}))
        self.assertFalse(report["agree"])
        pairs = {(d["seed_a"], d["seed_b"]) for d in report["divergences"]}
        self.assertIn((1, 2), pairs)
        self.assertIn((2, 3), pairs)
        self.assertNotIn((1, 3), pairs)  # agreeing pair stays silent

    def test_walltime_chatter_only_difference_agrees(self):
        # Verilator $finish walltime/speed noise varies run-to-run for
        # the SAME seed (observed: 31.422 vs 31.359 ms/s): normalizing
        # it is required for same-seed-twice to yield no diff.
        log_a = ("tohost: PASS\nCYCLES=10\n"
                 "- Verilator: $finish at 20us; walltime 0.001 s; "
                 "speed 31.422 ms/s\n")
        log_b = ("tohost: PASS\nCYCLES=10\n"
                 "- Verilator: $finish at 20us; walltime 0.002 s; "
                 "speed 29.305 ms/s\n")
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [42, 43],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({42: log_a, 43: log_b}))
        self.assertTrue(report["agree"])
        self.assertEqual(report["divergences"], [])
        # Raw traces preserved verbatim (normalization is compare-only).
        self.assertEqual(report["records"][0]["trace"], log_a)
        self.assertEqual(report["records"][1]["trace"], log_b)

    def test_seed_echo_chatter_still_flagged(self):
        # Any OTHER chatter difference (here the TB seed echo) is a
        # genuine trace mismatch and stays flagged -- normalization
        # covers walltime/speed tokens only, never seed identity.
        log_a = "tohost: PASS\nCYCLES=10\n- tb seed 42\n"
        log_b = "tohost: PASS\nCYCLES=10\n- tb seed 43\n"
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [42, 43],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({42: log_a, 43: log_b}))
        self.assertFalse(report["agree"])
        div = report["divergences"][0]
        self.assertTrue(div["checksum_match"])
        self.assertFalse(div["trace_match"])
        self.assertEqual(div["first_diff"]["line"], 3)
        self.assertEqual(div["first_diff"]["seed_a_line"], "- tb seed 42")

    def test_dut_line_walltime_like_text_never_normalized(self):
        # Normalization applies to "- " chatter lines ONLY: the same
        # tokens on a DUT line compare byte-exactly.
        log_a = "tohost: PASS\nwalltime 0.001 s; speed 1\n"
        log_b = "tohost: PASS\nwalltime 0.002 s; speed 2\n"
        report = seeddiff.diff_seeds(
            "/fake/tree", _tiny_case(), [42, 43],
            build_fn=_counting_build([]),
            run_fn=_seed_keyed_run({42: log_a, 43: log_b}))
        self.assertFalse(report["agree"])


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_build_failure_raises(self):
        with self.assertRaises(RuntimeError):
            seeddiff.diff_seeds(
                "/fake/tree", _tiny_case(), [42, 42],
                build_fn=_counting_build([], fail=True),
                run_fn=_seed_keyed_run({42: "tohost: PASS\n"}))

    def test_run_error_raises_never_clean_agreement(self):
        def _boom(binary, seed):
            raise RuntimeError("sim error: binary crashed")
        with self.assertRaises(RuntimeError):
            seeddiff.diff_seeds(
                "/fake/tree", _tiny_case(), [42, 43],
                build_fn=_counting_build([]),
                run_fn=_boom)

    def test_empty_log_raises(self):
        with self.assertRaises(RuntimeError):
            seeddiff.diff_seeds(
                "/fake/tree", _tiny_case(), [42, 43],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({42: "tohost: PASS\n", 43: ""}))

    def test_too_few_seeds_raises(self):
        with self.assertRaises(ValueError):
            seeddiff.diff_seeds(
                "/fake/tree", _tiny_case(), [42],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({42: "tohost: PASS\n"}))
        with self.assertRaises(ValueError):
            seeddiff.diff_seeds(
                "/fake/tree", _tiny_case(), [],
                build_fn=_counting_build([]),
                run_fn=_seed_keyed_run({}))

    def test_non_string_log_raises(self):
        with self.assertRaises(TypeError):
            seeddiff.diff_seeds(
                "/fake/tree", _tiny_case(), [42, 43],
                build_fn=_counting_build([]),
                run_fn=lambda binary, seed: 12345)


if __name__ == "__main__":
    unittest.main()
