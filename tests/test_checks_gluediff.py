"""Task F5: Glue-logic vs RTL diff (plan item 65).

diff(glue_lines, rtl_lines) -> {glue_only, rtl_only, common}: line-set
comparison ignoring whitespace-only differences and blank lines.

Fixture-driven:
  glue adds a wrapper line -> flagged glue_only.
  identical modulo whitespace -> all buckets empty except common.

Run: python3 -m unittest tests.test_checks_gluediff -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from checks import gluediff  # noqa: E402  (RED before implementation: ModuleNotFoundError)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
RTL = [
    "module top;",
    "wire a;",
    "assign a = 1'b0;",
    "endmodule",
]

GLUE_WITH_WRAPPER = [
    "module top;",
    "wire a;",
    "// glue wrapper: tie-off",
    "assign a = 1'b0;",
    "endmodule",
]


class TestGlueOnly(unittest.TestCase):
    def test_glue_wrapper_line_flagged_glue_only(self):
        found = gluediff.diff(list(GLUE_WITH_WRAPPER), list(RTL))
        self.assertEqual(found["glue_only"], ["// glue wrapper: tie-off"])
        self.assertEqual(found["rtl_only"], [])
        self.assertEqual(len(found["common"]), len(RTL))

    def test_rtl_only_line_flagged(self):
        glue = ["module top;", "wire a;", "endmodule"]
        found = gluediff.diff(glue, list(RTL))
        self.assertEqual(found["glue_only"], [])
        self.assertEqual(found["rtl_only"], ["assign a = 1'b0;"])

    def test_result_has_exactly_the_three_keys(self):
        found = gluediff.diff([], [])
        self.assertEqual(sorted(found.keys()), ["common", "glue_only", "rtl_only"])


class TestWhitespaceInsensitive(unittest.TestCase):
    def test_identical_modulo_whitespace_passes_empty(self):
        glue = [
            "   module top;  ",
            "\twire a;",
            "",
            "   ",
            "assign a = 1'b0;",
            "endmodule",
        ]
        found = gluediff.diff(glue, list(RTL))
        self.assertEqual(found["glue_only"], [])
        self.assertEqual(found["rtl_only"], [])
        self.assertEqual(len(found["common"]), len(RTL))

    def test_internal_whitespace_only_difference_ignored(self):
        glue = ["module   top;", "wire    a;"]
        rtl = ["module top;", "wire a;"]
        found = gluediff.diff(glue, rtl)
        self.assertEqual(found["glue_only"], [])
        self.assertEqual(found["rtl_only"], [])
        self.assertEqual(len(found["common"]), 2)

    def test_blank_lines_never_appear_in_any_bucket(self):
        found = gluediff.diff(["", "   ", "\t"], ["", "  "])
        self.assertEqual(found, {"glue_only": [], "rtl_only": [], "common": []})


class TestDegenerate(unittest.TestCase):
    def test_empty_inputs_all_empty(self):
        self.assertEqual(gluediff.diff([], []),
                         {"glue_only": [], "rtl_only": [], "common": []})

    def test_both_empty_modulo_blanks(self):
        found = gluediff.diff(["  "], ["", "\t"])
        self.assertEqual(found["glue_only"], [])
        self.assertEqual(found["rtl_only"], [])
        self.assertEqual(found["common"], [])

    def test_diff_does_not_mutate_input(self):
        glue = list(GLUE_WITH_WRAPPER)
        rtl = list(RTL)
        snapshot = (list(glue), list(rtl))
        gluediff.diff(glue, rtl)
        self.assertEqual((glue, rtl), snapshot)

    def test_rejects_bad_inputs(self):
        with self.assertRaises((TypeError, ValueError, AttributeError)):
            gluediff.diff("not-a-list", list(RTL))
        with self.assertRaises((TypeError, ValueError, AttributeError)):
            gluediff.diff(None, list(RTL))
        with self.assertRaises((TypeError, ValueError)):
            gluediff.diff(["ok", 42], list(RTL))
        with self.assertRaises((TypeError, ValueError)):
            gluediff.diff(list(GLUE_WITH_WRAPPER), [None])


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "gluediff", text,
                "%s must not reference the gluediff module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # gluediff is in-memory only; it must never write files.
        with open(os.path.join(ROOT, "agent", "checks",
                               "gluediff.py"),
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
                         "gluediff must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "checks",
                               "gluediff.py"),
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
