"""Task E9 (Trace compressor, plan item 59) red-first tests.

- compress(trace, budget=2500) -> {"text", "lines", "tokens", "checksum"}
  (alias spellings: trace_lines=[...], keep_window=N).
- Fixture: a long trace with a seeded failing pattern (idle-cycle runs +
  a TRAP/tohost failing tail). Compression must:
  (a) preserve the original checksum via
      agent/repair/equiv.py::checksum_log (result["checksum"] identical),
  (b) keep estimated tokens <= budget (default 2500),
  (c) keep the failing window verbatim (first N + last M lines exact),
  (d) run-length-collapse the middle (size shrinks),
  (e) still contain the oracle marker line.
- Plan gate: compressed trace replays to the identical checksum and
  estimated tokens <= 2500.

Run: python3 -m unittest tests.test_discover_tracecompress -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import tracecompress  # noqa: E402  (RED before implementation: ModuleNotFoundError)
from repair.equiv import checksum_log  # noqa: E402


ORACLE_MARKER = "TRAP: mstatus.MPP fault"
IDLE_LINE = "CYCLES idle fetch ok instr=0x00000013 nop"


def _fixture_trace():
    """Long failing trace: distinct head, idle runs, transitions, fail tail."""
    lines = []
    lines.append("BOOT ok seed=42 model=picosoc-uart-div")
    for i in range(1, 11):
        lines.append("CYCLES=%d fetch ok pc=0x%08x" % (i, 0x80000000 + 4 * i))
    lines.extend([IDLE_LINE] * 1500)
    lines.append("CYCLES=1511 irq pending uart_rx=0x00000041")
    lines.extend([IDLE_LINE] * 1200)
    lines.append("CYCLES=2712 divisor latch wdata=0x0000001b")
    lines.append("MISCLASS priv bit wrong at cycle 2719")
    lines.append(ORACLE_MARKER)
    lines.append("CYCLES=2720 tohost: FAIL")
    lines.append("CYCLES=2721 halt")
    return lines


def _distinct_trace(n=3000):
    """Many DISTINCT lines: RLE cannot help; budget fit needs truncation."""
    lines = ["BOOT ok seed=7"]
    for i in range(n):
        lines.append("CYCLES=%d fetch ok pc=0x%08x data=0x%08x"
                     % (i, 0x80000000 + 4 * i, i * 2654435761 % 2**32))
    lines.append("MISCLASS priv bit wrong at cycle %d" % n)
    lines.append(ORACLE_MARKER)
    lines.append("CYCLES=%d tohost: FAIL" % (n + 1))
    return lines


class TestPlanGate(unittest.TestCase):
    def test_compressed_replays_identical_checksum_within_budget(self):
        lines = _fixture_trace()
        original = "\n".join(lines)
        result = tracecompress.compress(original)
        self.assertEqual(result["checksum"], checksum_log(original),
                         "compressed trace must replay to the identical "
                         "checksum (via repair.equiv.checksum_log)")
        self.assertLessEqual(result["tokens"], 2500,
                             "estimated tokens must fit the 2500 budget")

    def test_fixture_pattern_survives_and_size_shrinks(self):
        lines = _fixture_trace()
        result = tracecompress.compress("\n".join(lines))
        self.assertIn(ORACLE_MARKER, result["text"],
                      "seeded failing pattern must survive compression")
        self.assertIn("tohost: FAIL", result["text"])
        self.assertLess(len(result["lines"]), len(lines),
                        "compressed trace must be smaller than the input")
        self.assertLess(result["tokens"], len("\n".join(lines).split()),
                        "compressed tokens must shrink vs the input")

    def test_tokens_match_collect_convention(self):
        sys.path.insert(0, os.path.join(ROOT, "agent"))
        import collect
        lines = _fixture_trace()
        result = tracecompress.compress("\n".join(lines))
        self.assertEqual(result["tokens"],
                         collect.approx_tokens(result["text"]))


class TestWindowedCompression(unittest.TestCase):
    def test_first_N_last_M_kept_verbatim(self):
        lines = _fixture_trace()
        keep = 7
        result = tracecompress.compress(list(lines), keep_window=keep)
        self.assertEqual(list(result["lines"][:keep]), lines[:keep],
                         "first N lines must survive byte-exact")
        self.assertEqual(list(result["lines"][-keep:]), lines[-keep:],
                         "last M (failing window) lines must survive "
                         "byte-exact")

    def test_head_duplicates_kept_verbatim_not_collapsed(self):
        lines = ([IDLE_LINE] * 4 + ["CYCLES=5 transition"]
                 + [IDLE_LINE] * 50 + [ORACLE_MARKER, "tohost: FAIL"])
        result = tracecompress.compress(list(lines), keep_window=4)
        self.assertEqual(list(result["lines"][:4]), [IDLE_LINE] * 4,
                         "duplicates inside the keep window stay verbatim")

    def test_middle_run_collapses_with_count(self):
        lines = (["BOOT ok"]
                 + [IDLE_LINE] * 100
                 + ["CYCLES=101 transition"]
                 + [ORACLE_MARKER, "tohost: FAIL"])
        result = tracecompress.compress(list(lines), keep_window=1)
        kept = [ln for ln in result["lines"] if ln == IDLE_LINE]
        self.assertEqual(len(kept), 1,
                         "middle run must collapse to a single kept line")
        markers = [ln for ln in result["lines"]
                   if "identical" in ln and "collapsed" in ln]
        self.assertEqual(len(markers), 1)
        self.assertIn("x100", markers[0],
                      "collapse marker must carry the run length")

    def test_transitions_kept_in_order(self):
        lines = (["BOOT ok"]
                 + [IDLE_LINE] * 50
                 + ["CYCLES=51 irq pending", "CYCLES=52 divisor latch"]
                 + [IDLE_LINE] * 50
                 + [ORACLE_MARKER, "tohost: FAIL"])
        result = tracecompress.compress(list(lines), keep_window=2)
        text_lines = list(result["lines"])
        self.assertIn("CYCLES=51 irq pending", text_lines)
        self.assertIn("CYCLES=52 divisor latch", text_lines)
        self.assertLess(text_lines.index("CYCLES=51 irq pending"),
                        text_lines.index("CYCLES=52 divisor latch"))

    def test_oracle_marker_present_in_lines_and_text(self):
        lines = _fixture_trace()
        result = tracecompress.compress(list(lines), keep_window=5)
        self.assertIn(ORACLE_MARKER, result["lines"])
        self.assertIn(ORACLE_MARKER, result["text"])


class TestSpellingsAndPurity(unittest.TestCase):
    def test_positional_and_keyword_spellings_agree(self):
        lines = _fixture_trace()
        by_str = tracecompress.compress("\n".join(lines))
        by_list = tracecompress.compress(list(lines))
        by_kw = tracecompress.compress(trace_lines=list(lines),
                                       keep_window=5)
        by_plan_kw = tracecompress.compress(trace="\n".join(lines),
                                            budget=2500)
        self.assertEqual(by_str, by_list)
        self.assertEqual(by_str["checksum"], by_kw["checksum"])
        self.assertEqual(by_str["checksum"], by_plan_kw["checksum"])
        self.assertIn(ORACLE_MARKER, by_kw["text"])

    def test_small_trace_passthrough_keeps_checksum(self):
        lines = ["BOOT ok", ORACLE_MARKER, "tohost: FAIL"]
        result = tracecompress.compress(list(lines))
        self.assertEqual(list(result["lines"]), lines)
        self.assertEqual(result["checksum"], checksum_log("\n".join(lines)))

    def test_does_not_mutate_input(self):
        lines = _fixture_trace()
        snapshot = list(lines)
        tracecompress.compress(lines)
        self.assertEqual(lines, snapshot)

    def test_deterministic_across_runs(self):
        lines = _fixture_trace()
        first = tracecompress.compress(list(lines))
        second = tracecompress.compress(list(lines))
        self.assertEqual(first, second)

    def test_truncation_path_fits_budget_keeps_tail_and_checksum(self):
        lines = _distinct_trace(3000)
        original = "\n".join(lines)
        result = tracecompress.compress(original, budget=200)
        self.assertLessEqual(result["tokens"], 200)
        self.assertEqual(list(result["lines"][-3:]), lines[-3:],
                         "failing tail must survive budget truncation")
        self.assertIn(ORACLE_MARKER, result["text"])
        self.assertEqual(result["checksum"], checksum_log(original))


class TestErrorsRaiseLoudly(unittest.TestCase):
    def test_missing_input_raises(self):
        with self.assertRaises(TypeError):
            tracecompress.compress()

    def test_both_spellings_raise(self):
        with self.assertRaises(TypeError):
            tracecompress.compress("BOOT ok",
                                   trace_lines=["BOOT ok"])

    def test_empty_trace_raises(self):
        with self.assertRaises(ValueError):
            tracecompress.compress("")
        with self.assertRaises(ValueError):
            tracecompress.compress([])

    def test_non_string_lines_raise(self):
        with self.assertRaises(TypeError):
            tracecompress.compress(["BOOT ok", 42, ORACLE_MARKER])

    def test_bad_budget_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            tracecompress.compress("BOOT ok", budget=0)
        with self.assertRaises((TypeError, ValueError)):
            tracecompress.compress("BOOT ok", budget="2500")

    def test_bad_keep_window_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            tracecompress.compress("BOOT ok", keep_window=-1)

    def test_unfittable_budget_raises_loudly(self):
        # Head + tail windows alone exceed the budget: refuse, never
        # silently breach the cap.
        lines = ["word " * 50] * 20
        with self.assertRaises(ValueError):
            tracecompress.compress(list(lines), keep_window=10, budget=10)


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "tracecompress", text,
                "%s must not reference the tracecompress module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "tracecompress.py"),
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
                         "tracecompress must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "tracecompress.py"),
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
