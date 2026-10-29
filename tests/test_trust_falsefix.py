"""False-fix alarm tests (Task D2-TRUE, plan item 32, Wave D).

`agent/trust/falsefix.py` runs the C3 equivalence check after GREEN:
fixed vs pristine tree on random stimulus. On divergence it sets an
alarm flag in meta (verdict stays GREEN but marked needs-review).

RED-first TDD: this file was written BEFORE agent/trust/falsefix.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_falsefix -v (from repo root)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import falsefix  # noqa: E402  (RED before implementation)


def _tiny_case():
    return {"name": "tiny-case", "files": ["tiny.v"], "tb": "tiny_tb"}


def _clean_run(binary, seed):
    return "tohost: PASS\n"


def _dirty_run(binary, seed):
    if "fixed" in binary:
        return "tohost: PASS but extra line\n"
    return "tohost: PASS\n"


class TestAlarmContract(unittest.TestCase):
    def test_equivalence_stays_clean(self):
        meta = {}
        res = falsefix.alarm_after_green(
            meta, "/fake/fixed", "/fake/pristine", _tiny_case(),
            master_seed=7, n=2,
            build_fn=lambda tree, case: "/fake/bin-" + tree.split("/")[-1],
            run_fn=_clean_run)
        self.assertFalse(res["alarm"])
        self.assertNotIn("needs_review", meta)
        self.assertNotIn("false_fix_alarm", meta)

    def test_fixture_divergence_alarms(self):
        meta = {}
        res = falsefix.alarm_after_green(
            meta, "/fake/fixed", "/fake/pristine", _tiny_case(),
            master_seed=7, n=2,
            build_fn=lambda tree, case: "/fake/bin-" + tree.split("/")[-1],
            run_fn=_dirty_run)
        self.assertTrue(res["alarm"])
        self.assertTrue(meta.get("needs_review"))
        self.assertIn("false_fix_alarm", meta)
        self.assertIn("diverged", res["reason"].lower())
        # The record must name the diverged seeds (regression: the
        # seeds live at evidence.divergences, not top-level).
        expected = sorted(
            d["seed"] for d in res["equivalence"]["evidence"]["divergences"])
        self.assertTrue(expected)
        self.assertEqual(
            sorted(meta["false_fix_alarm"]["diverged_seeds"]), expected)

    def test_verdict_stays_green_on_alarm(self):
        # The verdict is never flipped: alarm only marks needs-review.
        self.assertEqual(falsefix.alarm_after_green(
            {}, "/fake/f", "/fake/p", _tiny_case(), master_seed=1, n=1,
            build_fn=lambda t, c: "/fake/b-" + t.split("/")[-1],
            run_fn=_dirty_run)["verdict"], "GREEN")

    def test_result_shape(self):
        res = falsefix.alarm_after_green(
            {}, "/fake/f", "/fake/p", _tiny_case(), master_seed=1, n=1,
            build_fn=lambda t, c: "/fake/b",
            run_fn=_clean_run)
        self.assertEqual(set(res), {"alarm", "verdict", "reason",
                                    "equivalence"})
        self.assertEqual(res["verdict"], "GREEN")


class TestAlarmValidation(unittest.TestCase):
    def test_blank_tree_raises(self):
        with self.assertRaises(ValueError):
            falsefix.alarm_after_green(
                {}, "  ", "/fake/p", _tiny_case(),
                build_fn=lambda t, c: "/fake/b", run_fn=_clean_run)

    def test_bad_case_raises(self):
        # A registered case NAME string is valid (loaded via vbackend);
        # non-str non-dict cases are refused loudly.
        with self.assertRaises(ValueError):
            falsefix.alarm_after_green(
                {}, "/fake/f", "/fake/p", ["not-a-case"],
                build_fn=lambda t, c: "/fake/b", run_fn=_clean_run)

    def test_meta_must_be_dict(self):
        with self.assertRaises(ValueError):
            falsefix.alarm_after_green(
                None, "/fake/f", "/fake/p", _tiny_case(),
                build_fn=lambda t, c: "/fake/b", run_fn=_clean_run)

    def test_build_errors_propagate(self):
        def _boom(tree, case):
            raise RuntimeError("build exploded")

        with self.assertRaises(RuntimeError):
            falsefix.alarm_after_green(
                {}, "/fake/f", "/fake/p", _tiny_case(),
                build_fn=_boom, run_fn=_clean_run)


class TestDefaultOff(unittest.TestCase):
    def test_no_loop_guard_wiring(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("agent/loop.py", "agent/guard.py"):
            with open(os.path.join(repo, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn("falsefix", text)
            self.assertNotIn("alarm_after_green", text)

    def test_stdlib_and_repo_only(self):
        import ast
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(
                repo, "agent/trust/falsefix.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        allowed = {"hashlib", "json", "os", "re", "sys", "datetime",
                   "pathlib", "shutil", "subprocess", "tempfile"}
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


if __name__ == "__main__":
    unittest.main()
