"""Task C3 (equivalence check, plan item 23, Wave C FIX BETTER) tests:
agent/repair/equiv.py.

After GREEN, runs fixed vs pristine tree on random stimulus and demands
identical traces: an equivalent pair passes, a behavior-changed pair is
flagged. Sim-only; pure stdlib plus the existing sim/build path.

  generate_stimulus(master_seed, n=8) -> list[int]
  checksum_log(log) -> sha256 hex (vbackend.run_case convention)
  check_equivalence(tree_a, tree_b, case, master_seed=..., n=...,
                    build_fn=None, run_fn=None)
    -> {"equivalent": bool, "evidence": {...}}

Contract:
- Equivalent pair (pristine-vs-pristine sanity): identical logs on both
  sides for every stimulus seed -> equivalent True, divergences [].
- Behavior-changed pair: any per-seed trace/checksum mismatch ->
  equivalent False, divergences name the exact seed + first-differing
  line (pinned).
- REFUSE verdict (raise, never fabricate): either side fails to build
  or run, non-string log, or empty log -> raises loudly.
- Deterministic stimulus: same (master_seed, n) reproduces the exact
  same seed list; n < 1 raises.
- Build runs exactly ONCE per side; every stimulus runs on both sides.
- Default-off: explicit call only; agent/loop.py and agent/guard.py
  untouched (no equiv wiring).

RED-first TDD: this file was written BEFORE agent/repair/equiv.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_equiv.py -v   (from repo root)
"""
import hashlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from repair import equiv  # noqa: E402  (RED before implementation)


def _tiny_case():
    return {
        "name": "equiv-tiny",
        "project": "picorv32",
        "files": ["picorv32.v"],
        "top": "tb_picorv32_fetch",
        "tb": "picorv32-fetch_tb.sv",
        "oracle_pass": "tohost: PASS",
    }


def _det_checksum(log):
    det = [ln for ln in log.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def _counting_build(calls, fail_on=()):
    def _build(tree, case):
        calls.append((tree, dict(case)))
        if tree in fail_on:
            raise RuntimeError("verilator build failed (exit 1): boom")
        return "/fake/binary-%s" % os.path.basename(str(tree))
    return _build


def _tree_keyed_run(log_by_tree_seed, calls=None):
    """run_fn(binary, seed) keyed by (binary, seed); missing -> loud."""
    def _run(binary, seed):
        if calls is not None:
            calls.append((binary, int(seed)))
        key = (binary, int(seed))
        if key not in log_by_tree_seed:
            raise RuntimeError("sim error: no log for %r" % (key,))
        return log_by_tree_seed[key]
    return _run


def _uniform_logs(seeds, log, tag_a="treeA", tag_b="treeB"):
    logs = {}
    for s in seeds:
        logs[("/fake/binary-%s" % tag_a, s)] = log
        logs[("/fake/binary-%s" % tag_b, s)] = log
    return logs


class TestStimulusDeterministic(unittest.TestCase):
    def test_same_master_seed_reproduces_identical_list(self):
        a = equiv.generate_stimulus(1234, n=8)
        b = equiv.generate_stimulus(1234, n=8)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 8)
        for s in a:
            self.assertIsInstance(s, int)
            self.assertGreaterEqual(s, 0)

    def test_different_master_seed_gives_different_list(self):
        a = equiv.generate_stimulus(1, n=8)
        b = equiv.generate_stimulus(2, n=8)
        self.assertNotEqual(a, b)

    def test_default_n_is_eight(self):
        seeds = equiv.generate_stimulus(7)
        self.assertEqual(seeds, equiv.generate_stimulus(7, n=8))

    def test_n_less_than_one_raises(self):
        with self.assertRaises(ValueError):
            equiv.generate_stimulus(42, n=0)
        with self.assertRaises(ValueError):
            equiv.generate_stimulus(42, n=-3)

    def test_non_integer_master_seed_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            equiv.generate_stimulus("not-a-seed", n=4)
        with self.assertRaises((TypeError, ValueError)):
            equiv.generate_stimulus(None, n=4)


class TestChecksumConvention(unittest.TestCase):
    def test_matches_vbackend_deterministic_lines(self):
        log = "tohost: PASS\nCYCLES=10\n- Verilated chatter\n"
        self.assertEqual(equiv.checksum_log(log), _det_checksum(log))

    def test_non_string_log_raises(self):
        with self.assertRaises(TypeError):
            equiv.checksum_log(12345)
        with self.assertRaises(TypeError):
            equiv.checksum_log(None)


class TestEquivalentPairPasses(unittest.TestCase):
    def test_pristine_vs_pristine_sanity_passes(self):
        seeds = equiv.generate_stimulus(99, n=4)
        log = "tohost: PASS\nCYCLES=10\nREG x5=123\n"
        report = equiv.check_equivalence(
            "/fake/treeA", "/fake/treeB", _tiny_case(),
            master_seed=99, n=4,
            build_fn=_counting_build([]),
            run_fn=_tree_keyed_run(_uniform_logs(seeds, log)))
        self.assertTrue(report["equivalent"])
        self.assertEqual(report["evidence"]["divergences"], [])
        self.assertEqual(report["evidence"]["seeds"], seeds)
        self.assertEqual(report["evidence"]["master_seed"], 99)

    def test_build_runs_once_per_side(self):
        builds = []
        seeds = equiv.generate_stimulus(5, n=3)
        log = "tohost: PASS\nCYCLES=10\n"
        runs = []
        equiv.check_equivalence(
            "/fake/treeA", "/fake/treeB", _tiny_case(),
            master_seed=5, n=3,
            build_fn=_counting_build(builds),
            run_fn=_tree_keyed_run(_uniform_logs(seeds, log), runs))
        built_trees = sorted(t for t, _ in builds)
        self.assertEqual(built_trees, ["/fake/treeA", "/fake/treeB"])
        # Every stimulus ran on BOTH binaries.
        ran = sorted((b, s) for b, s in runs)
        want = sorted(
            [("/fake/binary-treeA", s) for s in seeds]
            + [("/fake/binary-treeB", s) for s in seeds])
        self.assertEqual(ran, want)


class TestBehaviorChangedPairFlagged(unittest.TestCase):
    def test_single_divergent_seed_flags_with_first_diff(self):
        seeds = equiv.generate_stimulus(11, n=4)
        good = "tohost: PASS\nCYCLES=10\nREG x5=123\n"
        bad = "tohost: PASS\nCYCLES=10\nREG x5=124\n"
        logs = {}
        for s in seeds:
            logs[("/fake/binary-treeA", s)] = good
            # Mutant differs only on the LAST stimulus seed.
            logs[("/fake/binary-treeB", s)] = (
                bad if s == seeds[-1] else good)
        report = equiv.check_equivalence(
            "/fake/treeA", "/fake/treeB", _tiny_case(),
            master_seed=11, n=4,
            build_fn=_counting_build([]),
            run_fn=_tree_keyed_run(logs))
        self.assertFalse(report["equivalent"])
        divs = report["evidence"]["divergences"]
        self.assertEqual(len(divs), 1)
        div = divs[0]
        self.assertEqual(div["seed"], seeds[-1])
        self.assertFalse(div["checksum_match"])
        self.assertEqual(div["kind"], "checksum")
        self.assertEqual(div["first_diff"]["line"], 3)
        self.assertEqual(div["first_diff"]["tree_a_line"], "REG x5=123")
        self.assertEqual(div["first_diff"]["tree_b_line"], "REG x5=124")

    def test_trace_only_chatter_difference_flagged(self):
        # "- " lines are excluded from the checksum (vbackend convention)
        # but are still trace: a difference there flags kind "trace".
        seeds = equiv.generate_stimulus(21, n=2)
        log_a = "tohost: PASS\nCYCLES=10\n- tb seed 21\n"
        log_b = "tohost: PASS\nCYCLES=10\n- tb seed 22\n"
        self.assertEqual(_det_checksum(log_a), _det_checksum(log_b))
        logs = {}
        for s in seeds:
            logs[("/fake/binary-treeA", s)] = log_a
            logs[("/fake/binary-treeB", s)] = log_b
        report = equiv.check_equivalence(
            "/fake/treeA", "/fake/treeB", _tiny_case(),
            master_seed=21, n=2,
            build_fn=_counting_build([]),
            run_fn=_tree_keyed_run(logs))
        self.assertFalse(report["equivalent"])
        for div in report["evidence"]["divergences"]:
            self.assertTrue(div["checksum_match"])
            self.assertFalse(div["trace_match"])
            self.assertEqual(div["kind"], "trace")

    def test_walltime_chatter_only_difference_still_equivalent(self):
        # Verilator $finish walltime/speed noise varies run-to-run for
        # the SAME seed: normalizing it is required for
        # pristine-vs-pristine to pass.
        seeds = equiv.generate_stimulus(42, n=2)
        log_a = ("tohost: PASS\nCYCLES=10\n"
                 "- Verilator: $finish at 20us; walltime 0.001 s; "
                 "speed 31.422 ms/s\n")
        log_b = ("tohost: PASS\nCYCLES=10\n"
                 "- Verilator: $finish at 20us; walltime 0.002 s; "
                 "speed 29.305 ms/s\n")
        logs = {}
        for s in seeds:
            logs[("/fake/binary-treeA", s)] = log_a
            logs[("/fake/binary-treeB", s)] = log_b
        report = equiv.check_equivalence(
            "/fake/treeA", "/fake/treeB", _tiny_case(),
            master_seed=42, n=2,
            build_fn=_counting_build([]),
            run_fn=_tree_keyed_run(logs))
        self.assertTrue(report["equivalent"])
        self.assertEqual(report["evidence"]["divergences"], [])


class TestRefuseVerdictOnFailure(unittest.TestCase):
    def test_build_failure_side_a_raises_never_verdict(self):
        with self.assertRaises(RuntimeError):
            equiv.check_equivalence(
                "/fake/treeA", "/fake/treeB", _tiny_case(),
                master_seed=1, n=2,
                build_fn=_counting_build([], fail_on=("/fake/treeA",)),
                run_fn=_tree_keyed_run({}))

    def test_build_failure_side_b_raises_never_verdict(self):
        with self.assertRaises(RuntimeError):
            equiv.check_equivalence(
                "/fake/treeA", "/fake/treeB", _tiny_case(),
                master_seed=1, n=2,
                build_fn=_counting_build([], fail_on=("/fake/treeB",)),
                run_fn=_tree_keyed_run({}))

    def test_run_error_raises_never_equivalent(self):
        def _boom(binary, seed):
            raise RuntimeError("sim error: binary crashed")
        with self.assertRaises(RuntimeError):
            equiv.check_equivalence(
                "/fake/treeA", "/fake/treeB", _tiny_case(),
                master_seed=1, n=2,
                build_fn=_counting_build([]),
                run_fn=_boom)

    def test_crashed_default_run_refuses_verdict(self):
        # A nonzero sim exit is not a comparable trace: the default
        # runner must raise rather than hand back a crash fragment
        # that two sides could "agree" on.
        import unittest.mock as mock
        with mock.patch.object(equiv.vbackend, "sh",
                               return_value=(1, "partial log\n")):
            with self.assertRaises(RuntimeError):
                equiv._default_run("/fake/binary", 42)
        with mock.patch.object(equiv.vbackend, "sh",
                               return_value=(0, "tohost: PASS\n")):
            self.assertEqual(equiv._default_run("/fake/binary", 42),
                             "tohost: PASS\n")

    def test_empty_log_raises(self):
        seeds = equiv.generate_stimulus(1, n=2)
        logs = {}
        for s in seeds:
            logs[("/fake/binary-treeA", s)] = "tohost: PASS\n"
            logs[("/fake/binary-treeB", s)] = ""
        with self.assertRaises(RuntimeError):
            equiv.check_equivalence(
                "/fake/treeA", "/fake/treeB", _tiny_case(),
                master_seed=1, n=2,
                build_fn=_counting_build([]),
                run_fn=_tree_keyed_run(logs))

    def test_non_string_log_raises(self):
        seeds = equiv.generate_stimulus(1, n=2)
        logs = {}
        for s in seeds:
            logs[("/fake/binary-treeA", s)] = "tohost: PASS\n"
            logs[("/fake/binary-treeB", s)] = 12345
        with self.assertRaises(TypeError):
            equiv.check_equivalence(
                "/fake/treeA", "/fake/treeB", _tiny_case(),
                master_seed=1, n=2,
                build_fn=_counting_build([]),
                run_fn=_tree_keyed_run(logs))

    def test_blank_tree_refuses(self):
        with self.assertRaises(ValueError):
            equiv.check_equivalence(
                "", "/fake/treeB", _tiny_case(),
                master_seed=1, n=2,
                build_fn=_counting_build([]),
                run_fn=_tree_keyed_run({}))
        with self.assertRaises(ValueError):
            equiv.check_equivalence(
                "/fake/treeA", "  ", _tiny_case(),
                master_seed=1, n=2,
                build_fn=_counting_build([]),
                run_fn=_tree_keyed_run({}))

    def test_pathlib_tree_accepted(self):
        # vbackend.ensure_tree returns pathlib.Path: the canonical
        # live call passes Path objects, so they must be accepted.
        import pathlib
        seen = []

        def _fake_build(tree, case):
            seen.append(tree)
            return "/fake/bin"

        def _fake_run(binary, seed):
            return "tohost: PASS\n"
        verdict = equiv.check_equivalence(
            pathlib.Path("/fake/treeA"), pathlib.Path("/fake/treeB"),
            _tiny_case(), master_seed=3, n=1,
            build_fn=_fake_build, run_fn=_fake_run)
        self.assertTrue(verdict["equivalent"])
        self.assertTrue(all(isinstance(t, str) for t in seen))


class TestDefaultOffNoWiring(unittest.TestCase):
    # Precise import/wiring markers only: a bare "equiv" substring
    # false-positives on the pre-existing loop.py:1255 comment
    # ("SAME equivalence gate"), which is not a wiring of this module.
    WIRING_MARKERS = ("repair/equiv", "repair.equiv",
                      "from repair import equiv", "import equiv")

    def test_loop_and_guard_do_not_wire_equiv(self):
        for path in ("agent/loop.py", "agent/guard.py"):
            full = os.path.join(ROOT, path)
            with open(full, encoding="utf-8") as fh:
                src = fh.read()
            hits = [m for m in self.WIRING_MARKERS if m in src]
            self.assertFalse(
                hits,
                "%s must not wire agent/repair/equiv.py "
                "(default-off: explicit call only); found %r"
                % (path, hits))


if __name__ == "__main__":
    unittest.main()
