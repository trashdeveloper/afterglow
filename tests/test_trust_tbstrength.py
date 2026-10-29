"""TB strength score tests (Task D3-TRUE, plan item 33, Wave D).

`agent/trust/tbstrength.py` scores a case TB by its kill-rate over a
standard mutant sample: each mutant counts as killed iff the C3
equivalence check (`agent/repair/equiv.py`) finds its trace divergent
from pristine under that TB; survivors are trace-equivalent. The score
is written to meta. Sim-only; stdlib + repo imports only.

RED-first TDD: this file was written BEFORE agent/trust/tbstrength.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_tbstrength -v (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import tbstrength  # noqa: E402  (RED before implementation)

GOOD = "tohost: PASS\nCYCLES=10\nREG x5=123\n"
BAD = "tohost: PASS\nCYCLES=10\nREG x5=124\n"

PRISTINE = "/fake/pristine"
MUTANTS = ["/fake/mut-a", "/fake/mut-b"]


def _strong_case():
    return {"name": "strong-tb", "files": ["tiny.v"], "tb": "strong_tb"}


def _weak_case():
    return {"name": "weak-tb", "files": ["tiny.v"], "tb": "weak_tb"}


def _build(tree, case):
    return "/fake/bin-" + str(tree).split("/")[-1]


def _strong_run(binary, seed):
    # Strong fixture TB observes the fault: every mutant diverges.
    if binary.endswith("pristine"):
        return GOOD
    return BAD


def _weak_run(binary, seed):
    # Weak fixture TB misses the fault: mutant traces match pristine.
    return GOOD


def _partial_run(binary, seed):
    # Kills mut-a only; mut-b survives.
    if binary.endswith("mut-a"):
        return BAD
    return GOOD


class TestStrengthOrdering(unittest.TestCase):
    def test_strong_tb_scores_higher_than_weak_tb_on_fixture(self):
        strong = tbstrength.score_tb(
            PRISTINE, MUTANTS, _strong_case(),
            master_seed=7, n=2, build_fn=_build, run_fn=_strong_run)
        weak = tbstrength.score_tb(
            PRISTINE, MUTANTS, _weak_case(),
            master_seed=7, n=2, build_fn=_build, run_fn=_weak_run)
        self.assertGreater(strong["kill_rate"], weak["kill_rate"])
        self.assertEqual(strong["kill_rate"], 1.0)
        self.assertEqual(weak["kill_rate"], 0.0)

    def test_kill_rate_is_killed_over_total(self):
        report = tbstrength.score_tb(
            PRISTINE, MUTANTS, _strong_case(),
            master_seed=7, n=2, build_fn=_build, run_fn=_partial_run)
        self.assertEqual(report["killed"], 1)
        self.assertEqual(report["total"], 2)
        self.assertAlmostEqual(report["kill_rate"], 0.5)
        self.assertEqual(report["killed_ids"], ["/fake/mut-a"])
        self.assertEqual(report["survived_ids"], ["/fake/mut-b"])

    def test_result_shape(self):
        report = tbstrength.score_tb(
            PRISTINE, MUTANTS, _strong_case(),
            master_seed=7, n=2, build_fn=_build, run_fn=_strong_run)
        self.assertEqual(set(report),
                         {"tb", "kill_rate", "killed", "total",
                          "killed_ids", "survived_ids", "evidence"})
        self.assertEqual(report["tb"], "strong-tb")
        ev = report["evidence"]
        self.assertEqual(ev["case"], "strong-tb")
        self.assertEqual(ev["master_seed"], 7)
        self.assertEqual(ev["n"], 2)
        self.assertEqual(len(ev["mutants"]), 2)

    def test_tb_override_names_report(self):
        report = tbstrength.score_tb(
            PRISTINE, MUTANTS, _strong_case(), tb="my-tb",
            master_seed=7, n=1, build_fn=_build, run_fn=_strong_run)
        self.assertEqual(report["tb"], "my-tb")
        self.assertEqual(report["evidence"]["case"], "strong-tb")


class TestMetaWrite(unittest.TestCase):
    def test_record_writes_score_to_meta(self):
        meta = {}
        report = tbstrength.record_tb_strength(
            meta, PRISTINE, MUTANTS, _strong_case(),
            master_seed=7, n=2, build_fn=_build, run_fn=_strong_run)
        self.assertIn("tb_strength", meta)
        record = meta["tb_strength"]
        self.assertEqual(record["kill_rate"], report["kill_rate"])
        self.assertEqual(record["kill_rate"], 1.0)
        self.assertEqual(record["killed"], 2)
        self.assertEqual(record["total"], 2)
        self.assertEqual(record["tb"], "strong-tb")
        self.assertEqual(record["master_seed"], 7)
        self.assertEqual(record["n"], 2)

    def test_record_tb_override_reaches_meta(self):
        meta = {}
        tbstrength.record_tb_strength(
            meta, PRISTINE, MUTANTS, _weak_case(), tb="weak-tb-v2",
            master_seed=7, n=1, build_fn=_build, run_fn=_weak_run)
        self.assertEqual(meta["tb_strength"]["tb"], "weak-tb-v2")
        self.assertEqual(meta["tb_strength"]["kill_rate"], 0.0)

    def test_record_returns_same_numbers_as_meta(self):
        meta = {}
        report = tbstrength.record_tb_strength(
            meta, PRISTINE, MUTANTS, _strong_case(),
            master_seed=7, n=2, build_fn=_build, run_fn=_partial_run)
        for key in ("tb", "kill_rate", "killed", "total",
                    "killed_ids", "survived_ids"):
            self.assertEqual(meta["tb_strength"][key], report[key], key)

    def test_meta_must_be_dict(self):
        with self.assertRaises(ValueError):
            tbstrength.record_tb_strength(
                None, PRISTINE, MUTANTS, _strong_case(),
                build_fn=_build, run_fn=_strong_run)
        with self.assertRaises(ValueError):
            tbstrength.record_tb_strength(
                [], PRISTINE, MUTANTS, _strong_case(),
                build_fn=_build, run_fn=_strong_run)


class TestRefusals(unittest.TestCase):
    def test_empty_mutant_sample_raises(self):
        with self.assertRaises(ValueError):
            tbstrength.score_tb(
                PRISTINE, [], _strong_case(),
                build_fn=_build, run_fn=_strong_run)

    def test_single_string_is_not_a_sample(self):
        with self.assertRaises(ValueError):
            tbstrength.score_tb(
                PRISTINE, "/fake/mut-a", _strong_case(),
                build_fn=_build, run_fn=_strong_run)

    def test_non_list_sample_raises(self):
        with self.assertRaises(ValueError):
            tbstrength.score_tb(
                PRISTINE, None, _strong_case(),
                build_fn=_build, run_fn=_strong_run)

    def test_blank_pristine_raises(self):
        with self.assertRaises(ValueError):
            tbstrength.score_tb(
                "  ", MUTANTS, _strong_case(),
                build_fn=_build, run_fn=_strong_run)

    def test_blank_mutant_raises(self):
        with self.assertRaises(ValueError):
            tbstrength.score_tb(
                PRISTINE, ["/fake/mut-a", " "], _strong_case(),
                build_fn=_build, run_fn=_strong_run)

    def test_bad_case_raises(self):
        with self.assertRaises(ValueError):
            tbstrength.score_tb(
                PRISTINE, MUTANTS, ["not-a-case"],
                build_fn=_build, run_fn=_strong_run)

    def test_bad_n_raises(self):
        with self.assertRaises(ValueError):
            tbstrength.score_tb(
                PRISTINE, MUTANTS, _strong_case(), n=0,
                build_fn=_build, run_fn=_strong_run)

    def test_build_errors_propagate(self):
        def _boom(tree, case):
            raise RuntimeError("build exploded")

        with self.assertRaises(RuntimeError):
            tbstrength.score_tb(
                PRISTINE, MUTANTS, _strong_case(),
                build_fn=_boom, run_fn=_strong_run)

    def test_run_errors_propagate(self):
        def _boom(binary, seed):
            raise RuntimeError("sim exploded")

        with self.assertRaises(RuntimeError):
            tbstrength.score_tb(
                PRISTINE, MUTANTS, _strong_case(),
                build_fn=_build, run_fn=_boom)


class TestEquivReuse(unittest.TestCase):
    def test_killed_mutant_shows_equiv_divergence(self):
        report = tbstrength.score_tb(
            PRISTINE, ["/fake/mut-a"], _strong_case(),
            master_seed=7, n=2, build_fn=_build, run_fn=_strong_run)
        entry = report["evidence"]["mutants"][0]
        self.assertEqual(entry["mutant"], "/fake/mut-a")
        self.assertTrue(entry["killed"])
        self.assertFalse(entry["equivalence"]["equivalent"])
        divs = entry["equivalence"]["evidence"]["divergences"]
        self.assertTrue(divs)
        self.assertEqual(divs[0]["first_diff"]["line"], 3)
        self.assertEqual(divs[0]["first_diff"]["tree_a_line"], "REG x5=123")
        self.assertEqual(divs[0]["first_diff"]["tree_b_line"], "REG x5=124")

    def test_surviving_mutant_is_equivalent(self):
        report = tbstrength.score_tb(
            PRISTINE, ["/fake/mut-a"], _weak_case(),
            master_seed=7, n=2, build_fn=_build, run_fn=_weak_run)
        entry = report["evidence"]["mutants"][0]
        self.assertFalse(entry["killed"])
        self.assertTrue(entry["equivalence"]["equivalent"])


class TestDefaultOff(unittest.TestCase):
    def test_no_loop_guard_score_wiring(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("agent/loop.py", "agent/guard.py", "eval/score.py"):
            with open(os.path.join(repo, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("tbstrength", text,
                             "%s must not wire the TB strength module "
                             "(default-off: explicit call only)" % rel)

    def test_stdlib_and_repo_only(self):
        import ast
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(
                repo, "agent/trust/tbstrength.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertEqual(alias.name.split(".")[0], "agent",
                                     node)
            elif isinstance(node, ast.ImportFrom):
                self.assertTrue(
                    (node.module or "").split(".")[0] in ("agent",)
                    or node.level > 0,
                    node)
        with open(os.path.join(
                repo, "agent/trust/tbstrength.py"), encoding="utf-8") as handle:
            text = handle.read()
        for marker in ("agent.loop", "agent.guard", "eval.score",
                       "from agent import loop", "import loop"):
            self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
