"""Task E1 (Differential DUT) red-first tests: agent/discover/diffdut.py.

- run_pair(tree_a, tree_b, stimulus) builds both trees once (via
  sim/vbackend.py primitives by default), runs the SAME stimulus on
  both, byte-compares tohost + trace: identical runs -> match True;
  any divergence -> match False with the differing line pinned.
- divergence_report(result) -> str in a pinned format naming the
  first-differing line on divergence.

Sim runs are fixture-simulated via injected build_fn/run_fn (repo
convention: deterministic, no network, no Verilator in unit tests).
The default path wires vbackend.build + vbackend.sh +seed runs; the
caller obtains pristine trees via vbackend.ensure_tree.

Run: python3 tests/test_discover_diffdut.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import diffdut  # noqa: E402  (RED before implementation: ModuleNotFoundError)


NOP_SEED = 42


def _tiny_case():
    return {
        "name": "diffdut-tiny",
        "project": "picorv32",
        "files": ["picorv32.v"],
        "top": "tb_picorv32_fetch",
        "tb": "picorv32-fetch_tb.sv",
        "oracle_pass": "tohost: PASS",
    }


def _counting_build(calls, fail=False):
    def _build(tree, case):
        calls.append((tree, dict(case)))
        if fail:
            raise RuntimeError("verilator build failed (exit 1): boom")
        return "/fake/obj_%s/Vbinary" % os.path.basename(str(tree))
    return _build


def _log_keyed_run(log_by_tree, calls=None):
    def _run(binary, seed):
        if calls is not None:
            calls.append((binary, int(seed)))
        for tree, log in log_by_tree.items():
            if os.path.basename(str(tree)) in str(binary):
                return log() if callable(log) else log
        raise RuntimeError("sim error: unknown binary %r" % (binary,))
    return _run


PRISTINE_LOG = (
    "tohost: PASS\n"
    "CYCLES=10\n"
    "REG x5=123\n"
    "- Verilator: $finish at 20us\n"
)


class TestSameDesignMatches(unittest.TestCase):
    def test_same_design_matches(self):
        # Two pristine picorv32 trees, identical NOP stimulus -> match.
        builds, runs = [], []
        result = diffdut.run_pair(
            "/fake/pristine-a", "/fake/pristine-b", NOP_SEED,
            case=_tiny_case(),
            build_fn=_counting_build(builds),
            run_fn=_log_keyed_run(
                {"/fake/pristine-a": PRISTINE_LOG,
                 "/fake/pristine-b": PRISTINE_LOG}, runs))
        self.assertTrue(result["match"])
        self.assertEqual(result["tohost_a"], "tohost: PASS")
        self.assertEqual(result["tohost_b"], "tohost: PASS")
        self.assertEqual(result["trace_a"], PRISTINE_LOG)
        self.assertEqual(result["trace_b"], PRISTINE_LOG)
        self.assertIsNone(result["divergence"])

    def test_build_runs_once_per_tree(self):
        builds = []
        diffdut.run_pair(
            "/fake/pristine-a", "/fake/pristine-b", NOP_SEED,
            case=_tiny_case(),
            build_fn=_counting_build(builds),
            run_fn=_log_keyed_run(
                {"/fake/pristine-a": PRISTINE_LOG,
                 "/fake/pristine-b": PRISTINE_LOG}))
        built_trees = sorted(t for t, _ in builds)
        self.assertEqual(built_trees, ["/fake/pristine-a", "/fake/pristine-b"])

    def test_same_stimulus_reaches_both_sides(self):
        runs = []
        diffdut.run_pair(
            "/fake/pristine-a", "/fake/pristine-b", NOP_SEED,
            case=_tiny_case(),
            build_fn=_counting_build([]),
            run_fn=_log_keyed_run(
                {"/fake/pristine-a": PRISTINE_LOG,
                 "/fake/pristine-b": PRISTINE_LOG}, runs))
        self.assertEqual(len(runs), 2)
        seeds = [seed for _, seed in runs]
        self.assertEqual(seeds, [NOP_SEED, NOP_SEED])

    def test_walltime_chatter_only_difference_still_matches(self):
        log_a = ("tohost: PASS\nCYCLES=10\n"
                 "- Verilator: $finish at 20us; walltime 0.001 s; "
                 "speed 31.422 ms/s\n")
        log_b = ("tohost: PASS\nCYCLES=10\n"
                 "- Verilator: $finish at 20us; walltime 0.002 s; "
                 "speed 29.305 ms/s\n")
        result = diffdut.run_pair(
            "/fake/pristine-a", "/fake/pristine-b", NOP_SEED,
            case=_tiny_case(),
            build_fn=_counting_build([]),
            run_fn=_log_keyed_run(
                {"/fake/pristine-a": log_a,
                 "/fake/pristine-b": log_b}))
        self.assertTrue(result["match"])
        self.assertIsNone(result["divergence"])
        # Raw traces preserved verbatim (normalization is compare-only).
        self.assertEqual(result["trace_a"], log_a)
        self.assertEqual(result["trace_b"], log_b)


class TestTamperedTreeDiverges(unittest.TestCase):
    TAMPERED_LOG = (
        "tohost: PASS\n"
        "CYCLES=10\n"
        "REG x5=124\n"
        "- Verilator: $finish at 20us\n"
    )

    def test_tampered_tree_diverges(self):
        result = diffdut.run_pair(
            "/fake/pristine-a", "/fake/tampered-b", NOP_SEED,
            case=_tiny_case(),
            build_fn=_counting_build([]),
            run_fn=_log_keyed_run(
                {"/fake/pristine-a": PRISTINE_LOG,
                 "/fake/tampered-b": self.TAMPERED_LOG}))
        self.assertFalse(result["match"])
        div = result["divergence"]
        self.assertIsNotNone(div)
        # The differing line is named: 1-based line 3, both values.
        self.assertEqual(div["first_diff"]["line"], 3)
        self.assertEqual(div["first_diff"]["tree_a_line"], "REG x5=123")
        self.assertEqual(div["first_diff"]["tree_b_line"], "REG x5=124")
        self.assertFalse(div["checksum_match"])

    def test_tohost_mismatch_diverges(self):
        log_b = PRISTINE_LOG.replace("tohost: PASS", "tohost: FAIL 0xdead")
        result = diffdut.run_pair(
            "/fake/pristine-a", "/fake/tampered-b", NOP_SEED,
            case=_tiny_case(),
            build_fn=_counting_build([]),
            run_fn=_log_keyed_run(
                {"/fake/pristine-a": PRISTINE_LOG,
                 "/fake/tampered-b": log_b}))
        self.assertFalse(result["match"])
        self.assertEqual(result["tohost_a"], "tohost: PASS")
        self.assertEqual(result["tohost_b"], "tohost: FAIL 0xdead")
        self.assertFalse(result["divergence"]["tohost_match"])

    def test_divergence_report_names_differing_line(self):
        result = diffdut.run_pair(
            "/fake/pristine-a", "/fake/tampered-b", NOP_SEED,
            case=_tiny_case(),
            build_fn=_counting_build([]),
            run_fn=_log_keyed_run(
                {"/fake/pristine-a": PRISTINE_LOG,
                 "/fake/tampered-b": self.TAMPERED_LOG}))
        report = diffdut.divergence_report(result)
        self.assertIsInstance(report, str)
        self.assertIn("DIVERGENCE", report)
        self.assertIn("line 3", report)
        self.assertIn("REG x5=123", report)
        self.assertIn("REG x5=124", report)

    def test_match_report_pinned_format(self):
        result = diffdut.run_pair(
            "/fake/pristine-a", "/fake/pristine-b", NOP_SEED,
            case=_tiny_case(),
            build_fn=_counting_build([]),
            run_fn=_log_keyed_run(
                {"/fake/pristine-a": PRISTINE_LOG,
                 "/fake/pristine-b": PRISTINE_LOG}))
        report = diffdut.divergence_report(result)
        self.assertIn("MATCH", report)
        self.assertNotIn("DIVERGENCE", report)
        self.assertIn("seed %d" % NOP_SEED, report)


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_build_failure_raises(self):
        with self.assertRaises(RuntimeError):
            diffdut.run_pair(
                "/fake/tree-a", "/fake/tree-b", NOP_SEED,
                case=_tiny_case(),
                build_fn=_counting_build([], fail=True),
                run_fn=_log_keyed_run({}))

    def test_run_error_raises_never_match(self):
        def _boom(binary, seed):
            raise RuntimeError("sim error: binary crashed")
        with self.assertRaises(RuntimeError):
            diffdut.run_pair(
                "/fake/tree-a", "/fake/tree-b", NOP_SEED,
                case=_tiny_case(),
                build_fn=_counting_build([]),
                run_fn=_boom)

    def test_empty_log_raises(self):
        with self.assertRaises(RuntimeError):
            diffdut.run_pair(
                "/fake/tree-a", "/fake/tree-b", NOP_SEED,
                case=_tiny_case(),
                build_fn=_counting_build([]),
                run_fn=_log_keyed_run(
                    {"/fake/tree-a": PRISTINE_LOG, "/fake/tree-b": ""}))

    def test_non_string_log_raises(self):
        with self.assertRaises(TypeError):
            diffdut.run_pair(
                "/fake/tree-a", "/fake/tree-b", NOP_SEED,
                case=_tiny_case(),
                build_fn=_counting_build([]),
                run_fn=lambda binary, seed: 12345)

    def test_blank_tree_raises(self):
        with self.assertRaises(ValueError):
            diffdut.run_pair(
                "  ", "/fake/tree-b", NOP_SEED,
                case=_tiny_case(),
                build_fn=_counting_build([]),
                run_fn=_log_keyed_run({}))

    def test_non_int_stimulus_raises(self):
        with self.assertRaises(TypeError):
            diffdut.run_pair(
                "/fake/tree-a", "/fake/tree-b", "nop",
                case=_tiny_case(),
                build_fn=_counting_build([]),
                run_fn=_log_keyed_run({}))


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "diffdut", text,
                "%s must not reference the diffdut module (default-off: "
                "no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # diffdut returns dicts/strings; it must never write repo files:
        # no file-writing calls in the module source.
        with open(os.path.join(ROOT, "agent", "discover", "diffdut.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        writes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in ("write_text", "dump", "dumpf", "copyfile",
                            "copy", "move", "rename", "makedirs", "mkdir"):
                    writes.add(name)
                if name == "open":
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            if any(c in str(kw.value.value) for c in "wax+"):
                                writes.add("open(%r)" % kw.value.value)
                    if (len(node.args) >= 2
                            and isinstance(node.args[1], ast.Constant)
                            and any(c in str(node.args[1].value)
                                    for c in "wax+")):
                        writes.add("open(%r)" % node.args[1].value)
        self.assertEqual(writes, set(),
                         "diffdut must not write files (tmp-only gate): %r"
                         % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover", "diffdut.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint({"requests", "urllib"}),
                        "no live HTTP imports allowed ($0 gate): %r"
                        % (imported,))
        for token in ("MODEL_API_KEY", "urllib", "requests"):
            self.assertNotIn(token, src,
                             "no live-API usage allowed ($0 gate): %r" % token)


if __name__ == "__main__":
    unittest.main()
