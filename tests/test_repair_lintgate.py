"""Task C9 (lint-before-resim, plan item 29, Wave C) tests:
agent/repair/lintgate.py.

Runs Verilator --lint-only on the patched tree BEFORE any full rebuild:
a lint failure skips the resim (saves the build+run time) and is NOT
counted as an attempt; a lint pass runs the resim exactly once (counted).

  lint_command(tree, case) -> list[str]
  lint_tree(tree, case, run_fn=None, timeout=...) -> {"lint_ok", "rc",
      "log", "command"}
  gate_resim(tree, case, resim_fn, run_fn=None)
      -> {"lint_ok", "skipped_resim", "counts_as_attempt",
          "resim_called", "verdict", "lint"}

Contract:
- Lint pass -> resim_fn called EXACTLY once, skipped_resim False,
  counts_as_attempt True, verdict forwarded verbatim.
- Lint fail (incl. broken syntax) -> resim_fn NEVER called,
  skipped_resim True, counts_as_attempt False, verdict None.
- REFUSE (raise, never fabricate): blank tree, non-dict case, missing
  top/files, missing RTL source on disk, resim_fn None, or the lint
  runner itself raising -> propagates loudly, never a fake lint_ok.
- Real-Verilator proof (sim-only, no API): a good .v file lints rc 0,
  a broken-syntax .v file lints rc != 0 and skips the resim.
- Default-off: explicit call only; agent/loop.py and agent/guard.py
  untouched (no lintgate wiring).

RED-first TDD: this file was written BEFORE agent/repair/lintgate.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_lintgate.py -v   (from repo root)
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import lintgate  # noqa: E402  (RED before implementation)


GOOD_V = (
    "module lintgood(input wire clk, output reg q);\n"
    "always @(posedge clk) q <= ~q;\n"
    "endmodule\n"
)

# Missing semicolons: module header + always body unterminated.
BAD_V = (
    "module lintbad(input wire clk, output reg q)\n"
    "always @(posedge clk) q <= ~q\n"
    "endmodule\n"
)


def _tiny_case(top="lintgood", files=("lintgood.v",)):
    return {"name": "lint-tiny", "top": top, "files": list(files),
            "oracle_pass": "tohost: PASS"}


def _write_tree(files):
    tmp = tempfile.mkdtemp(prefix="lintgate-")
    for name, body in files.items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    return tmp


def _ok_run(cmd, cwd=None, timeout=300):
    return 0, "- Verilator: lint ok\n"


def _fail_run(cmd, cwd=None, timeout=300):
    return 1, "%Error: lintbad.v:2:1: syntax error, unexpected always\n"


class TestLintCommand(unittest.TestCase):
    def test_command_is_lint_only_with_top_and_sources(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        cmd = lintgate.lint_command(tree, _tiny_case(
            top="lintgood", files=("a.v",)))
        self.assertEqual(cmd[0], "verilator")
        self.assertIn("--lint-only", cmd)
        self.assertIn("--top-module", cmd)
        self.assertIn("lintgood", cmd)
        self.assertTrue(any(str(c).endswith("a.v") for c in cmd),
                        "RTL source must be on the lint command: %r" % (cmd,))
        self.assertNotIn("--binary", cmd,
                         "lint gate must not trigger a full build")

    def test_command_appends_case_vflags(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        case = _tiny_case(top="lintgood", files=("a.v",))
        case["vflags"] = ["+define+FOO=1"]
        cmd = lintgate.lint_command(tree, case)
        self.assertIn("+define+FOO=1", cmd)


class TestLintTree(unittest.TestCase):
    def test_pass_returns_ok_with_rc_and_log(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        out = lintgate.lint_tree(
            tree, _tiny_case(top="lintgood", files=("a.v",)),
            run_fn=_ok_run)
        self.assertTrue(out["lint_ok"])
        self.assertEqual(out["rc"], 0)
        self.assertIn("lint ok", out["log"])
        self.assertEqual(out["command"][0], "verilator")

    def test_fail_returns_not_ok_never_raises(self):
        tree = _write_tree({"a.v": BAD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        out = lintgate.lint_tree(
            tree, _tiny_case(top="lintbad", files=("a.v",)),
            run_fn=_fail_run)
        self.assertFalse(out["lint_ok"])
        self.assertNotEqual(out["rc"], 0)
        self.assertIn("syntax error", out["log"])

    def test_run_fn_receives_verilator_lint_command(self):
        seen = []

        def _spy(cmd, cwd=None, timeout=300):
            seen.append((list(cmd), cwd))
            return 0, "ok\n"

        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        lintgate.lint_tree(
            tree, _tiny_case(top="lintgood", files=("a.v",)), run_fn=_spy)
        self.assertEqual(len(seen), 1)
        cmd, _cwd = seen[0]
        self.assertEqual(cmd[0], "verilator")
        self.assertIn("--lint-only", cmd)

    def test_blank_tree_refuses(self):
        with self.assertRaises(ValueError):
            lintgate.lint_tree("", _tiny_case(), run_fn=_ok_run)
        with self.assertRaises(ValueError):
            lintgate.lint_tree("   ", _tiny_case(), run_fn=_ok_run)

    def test_bad_case_refuses(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        with self.assertRaises(ValueError):
            lintgate.lint_tree(tree, "not-a-dict", run_fn=_ok_run)
        with self.assertRaises(ValueError):
            lintgate.lint_tree(tree, {"name": "x"}, run_fn=_ok_run)

    def test_missing_source_refuses_loudly(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        with self.assertRaises(FileNotFoundError):
            lintgate.lint_tree(
                tree, _tiny_case(top="lintgood", files=("ghost.v",)),
                run_fn=_ok_run)

    def test_runner_error_propagates_never_fabricates(self):
        def _boom(cmd, cwd=None, timeout=300):
            raise RuntimeError("verilator missing")

        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        with self.assertRaises(RuntimeError):
            lintgate.lint_tree(
                tree, _tiny_case(top="lintgood", files=("a.v",)),
                run_fn=_boom)


class TestGateResim(unittest.TestCase):
    def test_lint_pass_runs_resim_once_and_counts_attempt(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        calls = []
        verdict = {"pass": False, "checksum": "abc"}

        def _fake_resim(t, c):
            calls.append((t, c))
            return verdict

        out = lintgate.gate_resim(
            tree, _tiny_case(top="lintgood", files=("a.v",)),
            _fake_resim, run_fn=_ok_run)
        self.assertTrue(out["lint_ok"])
        self.assertFalse(out["skipped_resim"])
        self.assertTrue(out["counts_as_attempt"])
        self.assertTrue(out["resim_called"])
        self.assertEqual(len(calls), 1)
        self.assertIs(out["verdict"], verdict)

    def test_lint_fail_skips_resim_not_counted_as_attempt(self):
        tree = _write_tree({"a.v": BAD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        calls = []

        def _fake_resim(t, c):
            calls.append(True)
            return {"pass": True}

        out = lintgate.gate_resim(
            tree, _tiny_case(top="lintbad", files=("a.v",)),
            _fake_resim, run_fn=_fail_run)
        self.assertFalse(out["lint_ok"])
        self.assertTrue(out["skipped_resim"])
        self.assertFalse(out["counts_as_attempt"])
        self.assertFalse(out["resim_called"])
        self.assertEqual(calls, [],
                         "lint fail must never reach the full rebuild")
        self.assertIsNone(out["verdict"])
        self.assertIn("syntax error", out["lint"]["log"])

    def test_broken_syntax_fixture_skips_resim(self):
        # The BAD_V fixture is genuinely broken Verilog (missing
        # semicolons): the gate must treat it as lint-fail -> skip.
        tree = _write_tree({"badv.v": BAD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        calls = []

        def _fake_resim(t, c):
            calls.append(True)
            return {"pass": True}

        out = lintgate.gate_resim(
            tree, _tiny_case(top="lintbad", files=("badv.v",)),
            _fake_resim, run_fn=_fail_run)
        self.assertTrue(out["skipped_resim"])
        self.assertFalse(out["counts_as_attempt"])
        self.assertEqual(calls, [])

    def test_resim_error_propagates_after_lint_pass(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)

        def _boom(t, c):
            raise RuntimeError("build exploded")

        with self.assertRaises(RuntimeError):
            lintgate.gate_resim(
                tree, _tiny_case(top="lintgood", files=("a.v",)),
                _boom, run_fn=_ok_run)

    def test_missing_resim_fn_refuses(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        with self.assertRaises(ValueError):
            lintgate.gate_resim(
                tree, _tiny_case(top="lintgood", files=("a.v",)),
                None, run_fn=_ok_run)


class TestRealVerilator(unittest.TestCase):
    def test_good_file_lints_clean(self):
        tree = _write_tree({"lintgood.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        out = lintgate.lint_tree(
            tree, _tiny_case(top="lintgood", files=("lintgood.v",)))
        self.assertTrue(out["lint_ok"], "real verilator log:\n%s"
                        % out["log"])
        self.assertEqual(out["rc"], 0)

    def test_broken_syntax_lints_dirty_and_skips_resim(self):
        tree = _write_tree({"lintbad.v": BAD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        out = lintgate.lint_tree(
            tree, _tiny_case(top="lintbad", files=("lintbad.v",)))
        self.assertFalse(out["lint_ok"])
        self.assertNotEqual(out["rc"], 0)
        calls = []

        def _fake_resim(t, c):
            calls.append(True)
            return {"pass": True}

        # Default run_fn (real verilator) through the full gate: broken
        # syntax must skip the resim without any injected fakes.
        gate = lintgate.gate_resim(
            tree, _tiny_case(top="lintbad", files=("lintbad.v",)),
            _fake_resim)
        self.assertFalse(gate["lint_ok"])
        self.assertTrue(gate["skipped_resim"])
        self.assertFalse(gate["counts_as_attempt"])
        self.assertEqual(calls, [])

    def test_good_file_gate_runs_resim(self):
        tree = _write_tree({"lintgood.v": GOOD_V})
        self.addCleanup(__import__("shutil").rmtree, tree, True)
        calls = []

        def _fake_resim(t, c):
            calls.append(True)
            return {"pass": False, "checksum": "real-lint-pass"}

        gate = lintgate.gate_resim(
            tree, _tiny_case(top="lintgood", files=("lintgood.v",)),
            _fake_resim)
        self.assertTrue(gate["lint_ok"])
        self.assertFalse(gate["skipped_resim"])
        self.assertTrue(gate["counts_as_attempt"])
        self.assertEqual(len(calls), 1)


class TestDefaultOffNoWiring(unittest.TestCase):
    # Precise import/wiring markers only: a bare "lintgate" substring
    # check would false-positive on this file's own docstring markers.
    WIRING_MARKERS = ("repair/lintgate", "repair.lintgate",
                      "from repair import lintgate", "import lintgate")

    def test_loop_and_guard_do_not_wire_lintgate(self):
        for path in ("agent/loop.py", "agent/guard.py"):
            full = os.path.join(ROOT, path)
            with open(full, encoding="utf-8") as fh:
                src = fh.read()
            hits = [m for m in self.WIRING_MARKERS if m in src]
            self.assertFalse(
                hits,
                "%s must not wire agent/repair/lintgate.py "
                "(default-off: explicit call only); found %r"
                % (path, hits))


if __name__ == "__main__":
    unittest.main()
