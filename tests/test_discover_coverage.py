"""Task A3 (Coverage-driven explorer) red-first tests: agent/discover/coverage.py.

- Parses Verilator --coverage line data, ranks uncovered lines in DUT
  files (excluding TB files), emits stimulus goals (file:line + reason)
  for top-N uncovered lines.
- On a small fixture, the top-ranked goal line is actually uncovered
  (verified against the RAW coverage text independently in the test,
  not via the module under test).
- Empty/missing coverage raises loudly, never returns zero goals silently.
- Reuses sim/vbackend.py build primitives with coverage flags.

Run: python3 tests/test_discover_coverage.py -v   (from repo root)
"""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import coverage  # noqa: E402  (RED before implementation: ModuleNotFoundError)

# Verilator coverage.dat field separators: \x01 before key, \x02 before value.
SEP1 = "\x01"
SEP2 = "\x02"


def _cov_line(filename, lineno, covtype, count, extra=""):
    fields = (SEP1 + "f" + SEP2 + filename + SEP1 + "l" + SEP2 + str(lineno)
              + SEP1 + "n" + SEP2 + "1" + SEP1 + "t" + SEP2 + covtype
              + SEP1 + "page" + SEP2 + "v_line/dut"
              + SEP1 + "o" + SEP2 + "block" + SEP1 + "S" + SEP2 + str(lineno)
              + SEP1 + "h" + SEP2 + "TOP" + extra)
    return "C '%s' %d" % (fields, count)


def _fixture_coverage_text():
    """Small fixture: dut.v lines 10 (covered), 11-12 (uncovered);
    tb.v line 5 uncovered but must be excluded; one toggle entry ignored."""
    lines = [
        "# SystemC::Coverage-3",
        _cov_line("dut.v", 10, "line", 5),
        _cov_line("dut.v", 11, "line", 0),
        _cov_line("dut.v", 12, "line", 0),
        _cov_line("tb.v", 5, "line", 0),
        _cov_line("dut.v", 1, "toggle", 0),
    ]
    return "\n".join(lines) + "\n"


def _tiny_case():
    return {
        "name": "coverage-tiny",
        "project": "picorv32",
        "files": ["dut.v"],
        "top": "tb_dut",
        "tb": "tb.v",
        "oracle_pass": "tohost: PASS",
    }


def _raw_hits_for(text, filename, lineno):
    """INDEPENDENT re-check: scan raw coverage text without using the
    coverage module -- extracts (file, line, type, count) via regex and
    returns the max line-type count for (filename, lineno), or None."""
    hits = None
    for m in re.finditer(r"^C '(.*)' (\d+)\s*$", text, re.M):
        fields, count = m.group(1), int(m.group(2))
        kv = {}
        for chunk in fields.split(SEP1):
            if not chunk:
                continue
            if SEP2 not in chunk:
                continue
            k, v = chunk.split(SEP2, 1)
            kv[k] = v
        if kv.get("f") and os.path.basename(kv["f"]) == filename \
                and kv.get("l") == str(lineno) and kv.get("t") == "line":
            hits = count if hits is None else max(hits, count)
    return hits


def _fake_build_factory(seen):
    def _fake_build(tree, case):
        seen.append(dict(case))
        return "/fake/binary"
    return _fake_build


def _fake_run_factory(text):
    def _fake_run(binary, seed, outdir):
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "coverage.dat"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return "tohost: PASS\nCYCLES=20\n"
    return _fake_run


class TestTopGoalGenuinelyUncovered(unittest.TestCase):
    def test_top_goal_line_is_actually_uncovered_in_raw_data(self):
        raw = _fixture_coverage_text()
        with tempfile.TemporaryDirectory() as tmp:
            report = coverage.explore(
                "/fake/tree", _tiny_case(), top_n=5, seed=42,
                build_fn=_fake_build_factory([]),
                run_fn=_fake_run_factory(raw),
                workdir=os.path.join(tmp, "covrun"))
        goals = report["goals"]
        self.assertTrue(len(goals) >= 1)
        top = goals[0]
        # Goal shape: file:line + reason.
        self.assertIn("file", top)
        self.assertIn("line", top)
        self.assertIn("reason", top)
        self.assertTrue(str(top["reason"]).strip())
        # INDEPENDENT verification: raw text says 0 hits for this line.
        raw_hits = _raw_hits_for(raw, os.path.basename(top["file"]),
                                 int(top["line"]))
        self.assertIsNotNone(raw_hits,
                             "top goal line not present in raw coverage data")
        self.assertEqual(raw_hits, 0,
                         "top goal %s:%s has %r hits in raw data (want 0)"
                         % (top["file"], top["line"], raw_hits))

    def test_tb_uncovered_lines_never_ranked(self):
        raw = _fixture_coverage_text()
        with tempfile.TemporaryDirectory() as tmp:
            report = coverage.explore(
                "/fake/tree", _tiny_case(), top_n=10, seed=42,
                build_fn=_fake_build_factory([]),
                run_fn=_fake_run_factory(raw),
                workdir=os.path.join(tmp, "covrun"))
        files = [os.path.basename(g["file"]) for g in report["goals"]]
        self.assertNotIn("tb.v", files)

    def test_toggle_entries_ignored(self):
        raw = _fixture_coverage_text()
        hits = coverage.parse_coverage_text(raw)
        # dut.v line 1 appears only as toggle -> must not be in line hits.
        self.assertNotIn(("dut.v", 1), hits)

    def test_top_n_respected_and_sorted(self):
        raw = _fixture_coverage_text()
        with tempfile.TemporaryDirectory() as tmp:
            report = coverage.explore(
                "/fake/tree", _tiny_case(), top_n=1, seed=42,
                build_fn=_fake_build_factory([]),
                run_fn=_fake_run_factory(raw),
                workdir=os.path.join(tmp, "covrun"))
        self.assertEqual(len(report["goals"]), 1)
        # Two uncovered DUT lines (11, 12): top-1 must be the first, 11.
        self.assertEqual(int(report["goals"][0]["line"]), 11)


class TestEmptyAndMissingRaise(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            coverage.parse_coverage_dat("/nonexistent/coverage.dat")

    def test_empty_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "coverage.dat")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("")
            with self.assertRaises(ValueError):
                coverage.parse_coverage_dat(p)

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            coverage.parse_coverage_text("")

    def test_header_only_raises_never_silent_zero(self):
        with self.assertRaises(ValueError):
            coverage.parse_coverage_text("# SystemC::Coverage-3\n")

    def test_no_line_entries_raises(self):
        text = ("# SystemC::Coverage-3\n"
                + _cov_line("dut.v", 1, "toggle", 3) + "\n")
        with self.assertRaises(ValueError):
            coverage.parse_coverage_text(text)

    def test_malformed_line_raises_loudly(self):
        bad = ("# SystemC::Coverage-3\n"
               "C 'garbage-without-count'\n")
        with self.assertRaises(ValueError):
            coverage.parse_coverage_text(bad)
        bad2 = ("# SystemC::Coverage-3\n"
                "C 'not-a-coverage-line' notanint\n")
        with self.assertRaises(ValueError):
            coverage.parse_coverage_text(bad2)

    def test_all_covered_raises_never_returns_silent_zero(self):
        text = ("# SystemC::Coverage-3\n"
                + _cov_line("dut.v", 10, "line", 5) + "\n"
                + _cov_line("dut.v", 11, "line", 2) + "\n")
        hits = coverage.parse_coverage_text(text)
        with self.assertRaises(ValueError):
            coverage.rank_uncovered(hits, ["dut.v"], tb_file="tb.v",
                                    top_n=5)

    def test_rank_empty_dict_raises(self):
        with self.assertRaises(ValueError):
            coverage.rank_uncovered({}, ["dut.v"], tb_file="tb.v", top_n=5)


class TestBuildReusesVbackendWithCoverage(unittest.TestCase):
    def test_build_appends_coverage_flag(self):
        seen = []
        case = _tiny_case()
        before = list(case.get("vflags", []))
        binary = coverage.build_with_coverage(
            "/fake/tree", case,
            build_fn=_fake_build_factory(seen))
        self.assertEqual(binary, "/fake/binary")
        self.assertEqual(len(seen), 1)
        self.assertIn("--coverage", seen[0].get("vflags", []))
        # Caller's case dict must not be mutated: assert against the
        # SAME dict that was passed, not a fresh fixture.
        self.assertEqual(list(case.get("vflags", [])), before)
        self.assertNotIn("--coverage", case.get("vflags", []))

    def test_explore_missing_coverage_dat_raises(self):
        def _no_cov_run(binary, seed, outdir):
            os.makedirs(outdir, exist_ok=True)
            return "tohost: PASS\n"

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises((FileNotFoundError, ValueError)):
                coverage.explore(
                    "/fake/tree", _tiny_case(), top_n=5, seed=42,
                    build_fn=_fake_build_factory([]),
                    run_fn=_no_cov_run,
                    workdir=os.path.join(tmp, "covrun"))


if __name__ == "__main__":
    unittest.main()
