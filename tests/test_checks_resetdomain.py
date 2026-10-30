"""Task F4: Reset-domain crossing check (plan item 64).

check(modules, reset_map) -> [violation...] flags registers whose reset
source differs from their clock domain's reset.

Fixture-driven:
  modules: list of {name, clk_domain, reset_source}
  reset_map: {domain: reset_net}
  violation = {register, expected, actual}

Run: python3 tests/test_checks_resetdomain.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from checks import resetdomain  # noqa: E402  (RED before implementation: ModuleNotFoundError)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
RESET_MAP = {"clk_a": "rst_a_n", "clk_b": "rst_b_n"}

CONSISTENT = [
    {"name": "u_cpu_pc", "clk_domain": "clk_a", "reset_source": "rst_a_n"},
    {"name": "u_uart_ctrl", "clk_domain": "clk_b", "reset_source": "rst_b_n"},
]

ONE_BAD = [
    {"name": "u_cpu_pc", "clk_domain": "clk_a", "reset_source": "rst_a_n"},
    {"name": "u_cdc_ghost", "clk_domain": "clk_a", "reset_source": "rst_b_n"},
]


class TestCheckConsistent(unittest.TestCase):
    def test_all_consistent_passes(self):
        self.assertEqual(resetdomain.check(list(CONSISTENT), dict(RESET_MAP)), [])

    def test_empty_modules_passes(self):
        self.assertEqual(resetdomain.check([], dict(RESET_MAP)), [])


class TestCheckMismatch(unittest.TestCase):
    def test_one_mismatched_flags_exactly_it(self):
        found = resetdomain.check([dict(m) for m in ONE_BAD], dict(RESET_MAP))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["register"], "u_cdc_ghost")
        self.assertEqual(found[0]["expected"], "rst_a_n")
        self.assertEqual(found[0]["actual"], "rst_b_n")

    def test_all_mismatched_all_flagged(self):
        mods = [
            {"name": "r1", "clk_domain": "clk_a", "reset_source": "rst_b_n"},
            {"name": "r2", "clk_domain": "clk_b", "reset_source": "rst_a_n"},
        ]
        found = resetdomain.check(mods, dict(RESET_MAP))
        self.assertEqual([v["register"] for v in found], ["r1", "r2"])

    def test_check_does_not_mutate_input(self):
        inbox = [dict(m) for m in ONE_BAD]
        snapshot = [dict(m) for m in inbox]
        resetmap = dict(RESET_MAP)
        resetdomain.check(inbox, resetmap)
        self.assertEqual(inbox, snapshot)
        self.assertEqual(resetmap, RESET_MAP)


class TestCheckLoudFailure(unittest.TestCase):
    def test_missing_domain_raises_loudly(self):
        mods = [{"name": "r1", "clk_domain": "clk_zzz", "reset_source": "rst_a_n"}]
        with self.assertRaises((KeyError, ValueError)):
            resetdomain.check(mods, dict(RESET_MAP))

    def test_rejects_bad_entries(self):
        with self.assertRaises((TypeError, ValueError, KeyError)):
            resetdomain.check("not-a-list", dict(RESET_MAP))
        with self.assertRaises((TypeError, ValueError, KeyError)):
            resetdomain.check([{"name": "x"}], dict(RESET_MAP))
        with self.assertRaises((TypeError, ValueError, KeyError, AttributeError)):
            resetdomain.check(None, dict(RESET_MAP))


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "resetdomain", text,
                "%s must not reference the resetdomain module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # resetdomain is in-memory only; it must never write files.
        with open(os.path.join(ROOT, "agent", "checks",
                               "resetdomain.py"),
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
                        if kw.arg == "mode" and isinstance(kw.value,
                                                           ast.Constant):
                            if any(c in str(kw.value.value) for c in "wax+"):
                                writes.add("open(%r)" % kw.value.value)
                    if (len(node.args) >= 2
                            and isinstance(node.args[1], ast.Constant)
                            and any(c in str(node.args[1].value)
                                    for c in "wax+")):
                        writes.add("open(%r)" % node.args[1].value)
        self.assertEqual(writes, set(),
                         "resetdomain must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "checks",
                               "resetdomain.py"),
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
                             "no live-API usage allowed ($0 gate): %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
