"""PILOT mutation-fault campaign tests (TDD, static fixtures only).

Run: python3 tests/test_mutate.py -v   (from repo root)

Covers eval/mutate.py (the seeded single-point injector + spec emitter):
- test_locked_skip_list_*: the locked-case skip list is READ from
  eval/cases.yaml (tier: locked) + sim/targets/<id>.yaml anchor/bug
  lines -- never hardcoded -- and covers the 3 locked cases.
- test_constant_flip/cmp_swap/num_bump: each operator rewrites exactly
  one site on a fixture line and leaves the input untouched (pure).
- test_seed_reproducibility: same seed + same tree => identical record.
- test_tree_copy_*: the source tree is byte-identical after mutation;
  the dest differs by exactly one line; dests outside /tmp or
  runs/scratch are REFUSED (never into the repo).
- test_locked_lines_never_mutated: enumeration + end-to-end mutation
  over many seeds never touch a locked (anchor/bug) line.
- test_emit_spec_*: the per-mutant YAML carries every sim-format key
  (project/files/top/tb/anchor/bug/gate/oracle_pass/oracle_trap) so
  red = mutant fails oracle, green = patch restores oracle match.
- test_no_live_calls: static scan -- mutate.py must never touch the
  network or an API key (aiming, not firing).

No model calls, no Verilator builds, no /tmp tree fixtures beyond
small temp dirs -- the pilot RED runs use the real backend, not these.
"""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import mutate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_YAML = os.path.join(ROOT, "eval", "cases.yaml")
TARGETS_DIR = os.path.join(ROOT, "sim", "targets")

LOCKED_IDS = {"picosoc-uart-div", "picorv32-fetch-misalign", "uart-tx-div"}


def _tiny_tree(tmp):
    """Two-file RTL fixture with known mutation sites."""
    src = os.path.join(tmp, "src")
    os.makedirs(os.path.join(src, "picosoc"))
    with open(os.path.join(src, "picosoc", "simpleuart.v"), "w") as fh:
        fh.write(
            "module simpleuart(input clk);\n"
            "reg [31:0] recv_divcnt;\n"
            "if (recv_divcnt > cfg_divider) begin\n"
            "recv_divcnt <= 1'b0;\n"
            "recv_divcnt <= recv_divcnt + 1;\n"
            "cfg_limit <= 16'h8E06;\n"
            "if (a == b) begin\n"
            "endmodule\n"
        )
    with open(os.path.join(src, "picorv32.v"), "w") as fh:
        fh.write(
            "module picorv32(input clk);\n"
            "trap <= 1'b0;\n"
            "reg_next_pc <= current_pc + 4;\n"
            "endmodule\n"
        )
    return src


class TestLockedSkipList(unittest.TestCase):
    def test_locked_ids_read_from_cases_yaml(self):
        skips = mutate.load_locked_skips(CASES_YAML, TARGETS_DIR)
        self.assertEqual(set(skips["ids"]), LOCKED_IDS)

    def test_skip_files_cover_locked_case_files(self):
        skips = mutate.load_locked_skips(CASES_YAML, TARGETS_DIR)
        files = skips["files"]
        self.assertIn("picosoc/simpleuart.v", files)
        self.assertIn("picorv32.v", files)
        uart_lines = files["picosoc/simpleuart.v"]
        self.assertIn("if (recv_divcnt > cfg_divider) begin", uart_lines)
        self.assertIn("if (send_divcnt > cfg_divider && send_bitcnt) begin",
                      uart_lines)
        fetch_lines = files["picorv32.v"]
        self.assertTrue(any("2'b10" in ln for ln in fetch_lines))

    def test_skip_list_not_hardcoded(self):
        # The skip file set must derive from the targets dir: every
        # locked id's files entry appears, nothing invented.
        skips = mutate.load_locked_skips(CASES_YAML, TARGETS_DIR)
        self.assertEqual(set(skips["ids"]), LOCKED_IDS)
        for rel, lines in skips["files"].items():
            self.assertTrue(lines, rel)


class TestOperators(unittest.TestCase):
    def test_constant_flip(self):
        before = "recv_divcnt <= 1'b0;"
        after = mutate.apply_constant_flip(before, 0, 0)
        self.assertEqual(after, "recv_divcnt <= 1'b1;")
        self.assertEqual(before, "recv_divcnt <= 1'b0;")  # pure
        back = mutate.apply_constant_flip(after, 0, 0)
        self.assertEqual(back, before)

    def test_constant_flip_no_site_raises(self):
        with self.assertRaises(mutate.NoSiteError):
            mutate.apply_constant_flip("assign x = y + z;", 0, 0)

    def test_cmp_swap(self):
        self.assertEqual(
            mutate.apply_cmp_swap("if (a == b) begin", 0, 0),
            "if (a != b) begin")
        self.assertEqual(
            mutate.apply_cmp_swap("if (recv_divcnt > cfg_divider) begin", 0, 0),
            "if (recv_divcnt <= cfg_divider) begin")

    def test_cmp_swap_no_site_raises(self):
        with self.assertRaises(mutate.NoSiteError):
            mutate.apply_cmp_swap("assign x = 1'b0;", 0, 0)

    def test_num_bump(self):
        up = mutate.apply_num_bump("cfg_limit <= 16'h8E06;", 0, +1)
        self.assertEqual(up, "cfg_limit <= 16'h8E07;")
        down = mutate.apply_num_bump("cfg_limit <= 16'h8E06;", 0, -1)
        self.assertEqual(down, "cfg_limit <= 16'h8E05;")
        bare = mutate.apply_num_bump("reg_next_pc <= current_pc + 4;", 0, +1)
        self.assertEqual(bare, "reg_next_pc <= current_pc + 5;")

    def test_num_bump_no_site_raises(self):
        with self.assertRaises(mutate.NoSiteError):
            mutate.apply_num_bump("endmodule", 0, +1)


class TestTreeCopy(unittest.TestCase):
    def test_source_untouched_dest_single_line_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            before = {}
            for root, _d, fns in os.walk(src):
                for fn in fns:
                    p = os.path.join(root, fn)
                    with open(p, "rb") as fh:
                        before[os.path.relpath(p, src)] = fh.read()
            dest = os.path.join(tmp, "mut-0")
            record = mutate.mutate_tree(src, dest, seed=42)
            # source byte-identical
            for rel, content in before.items():
                with open(os.path.join(src, rel), "rb") as fh:
                    self.assertEqual(fh.read(), content, rel)
            # dest differs by exactly one line in exactly one file
            diffs = []
            for rel, content in before.items():
                with open(os.path.join(dest, rel), encoding="utf-8") as fh:
                    after_lines = fh.read().splitlines()
                before_lines = content.decode().splitlines()
                self.assertEqual(len(after_lines), len(before_lines), rel)
                for i, (a, b) in enumerate(zip(before_lines, after_lines)):
                    if a != b:
                        diffs.append((rel, i, a, b))
            self.assertEqual(len(diffs), 1)
            rel, _i, a, b = diffs[0]
            self.assertEqual(record["file"], rel)
            self.assertEqual(record["before"].strip(), a.strip())
            self.assertEqual(record["after"].strip(), b.strip())

    def test_dest_outside_tmp_or_scratch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            bad = os.path.join(ROOT, "eval", "scratch-evil")
            with self.assertRaises(mutate.DestRefused):
                mutate.mutate_tree(src, bad, seed=1)
            bad2 = os.path.join(ROOT, "sim", "mut-evil")
            with self.assertRaises(mutate.DestRefused):
                mutate.mutate_tree(src, bad2, seed=1)

    def test_seed_reproducibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            r1 = mutate.mutate_tree(src, os.path.join(tmp, "a"), seed=42)
            r2 = mutate.mutate_tree(src, os.path.join(tmp, "b"), seed=42)
            self.assertEqual(r1, r2)
            r3 = mutate.mutate_tree(src, os.path.join(tmp, "c"), seed=43)
            self.assertNotEqual((r3["file"], r3["line"], r3["after"]),
                                (r1["file"], r1["line"], r1["after"]))

    def test_locked_lines_never_mutated(self):
        skips = mutate.load_locked_skips(CASES_YAML, TARGETS_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            # plant every locked uart line into the fixture so any hit
            # would be observable
            with open(os.path.join(src, "picosoc", "simpleuart.v"), "a") as fh:
                for ln in skips["files"]["picosoc/simpleuart.v"]:
                    fh.write(ln + "\n")
            locked = {ln.strip() for lines in skips["files"].values()
                      for ln in lines}
            for seed in range(30):
                dest = os.path.join(tmp, "m%d" % seed)
                record = mutate.mutate_tree(
                    src, dest, seed=seed, skips=skips)
                self.assertNotIn(record["before"].strip(), locked)
                self.assertNotIn(record["after"].strip(), locked)


class TestSpecEmitter(unittest.TestCase):
    def test_emit_spec_has_sim_format_keys(self):
        record = {"file": "picosoc/simpleuart.v", "line": 4,
                  "op": "constant_flip",
                  "before": "recv_divcnt <= 1'b0;",
                  "after": "recv_divcnt <= 1'b1;",
                  "seed": 42}
        text = mutate.emit_mutant_spec(
            record, mutant_id="mut-pilot-01", project="picorv32",
            top="tb_picosoc_uart", tb="picosoc-uart-div_tb.sv",
            gate="mutant pilot", oracle_pass="tohost: PASS",
            oracle_trap="TRAP:", parent_case="picosoc-uart-div",
            tree="/tmp/mutpilot/mut-pilot-01", vflags=["-Wno-WIDTH"])
        keys = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, v = line.split(":", 1)
            keys[k.strip()] = v.strip()
        for req in ("project", "files", "top", "tb", "anchor", "bug",
                    "gate", "oracle_pass", "oracle_trap", "vflags"):
            self.assertIn(req, keys, req)
        self.assertIn("-Wno-WIDTH", keys["vflags"])
        self.assertIn("1'b1", keys["bug"])
        self.assertIn("1'b0", keys["anchor"])

    def test_files_filter_restricts_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            only = mutate.enumerate_candidates(
                src, mutate.load_locked_skips(CASES_YAML, TARGETS_DIR),
                files=["picorv32.v"])
            self.assertTrue(only)
            self.assertTrue(all(c["file"] == "picorv32.v" for c in only))
            record = mutate.mutate_tree(
                src, os.path.join(tmp, "f"), seed=42, files=["picorv32.v"])
            self.assertEqual(record["file"], "picorv32.v")

    def test_no_live_calls_in_mutator(self):
        path = os.path.join(ROOT, "eval", "mutate.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("urllib", "requests", "API_KEY", "http.client",
                       "socket"):
            self.assertIsNone(
                re.search(r"\b%s\b" % re.escape(banned), src),
                "mutate.py must not touch the network (%s)" % banned)


class TestMutationScoreStub(unittest.TestCase):
    """eval/mutation_score.py aggregates STATIC pilot artifacts only."""

    def test_aggregate_pilot_counts(self):
        sys.path.insert(0, os.path.join(ROOT, "eval"))
        import mutation_score  # noqa: E402
        agg = mutation_score.aggregate()
        self.assertEqual(agg["mutants"], 5)
        self.assertEqual(agg["red_confirmed"], 5)
        self.assertEqual(agg["green_fixed_unaided"], 0)
        self.assertEqual(agg["cost_usd"], "TBD (no live runs)")

    def test_board_lists_all_pilot_mutants(self):
        with open(os.path.join(ROOT, "eval", "mutation_board.md"),
                  encoding="utf-8") as fh:
            board = fh.read()
        for mid in ("mut-pilot-01", "mut-pilot-02", "mut-pilot-03",
                    "mut-pilot-04", "mut-pilot-05"):
            self.assertIn(mid, board)
        self.assertIn("red_confirmed=5", board)

    def test_stub_performs_no_live_actions(self):
        path = os.path.join(ROOT, "eval", "mutation_score.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("vbackend", "subprocess", "urllib", "requests",
                       "API_KEY", "socket", "loop"):
            self.assertIsNone(
                re.search(r"\b%s\b" % re.escape(banned), src),
                "mutation_score.py must aggregate, never run (%s)" % banned)


class TestCondGrow(unittest.TestCase):
    """cond_grow: grown conditions (> -> >=, < -> <=) that compile + change
    behavior. Shifts (>>) and non-blocking assigns (<=) are never sites."""

    def test_grows_strict_inequalities(self):
        self.assertEqual(
            mutate.apply_cond_grow("if (a > b) begin", 0, 0),
            "if (a >= b) begin")
        self.assertEqual(
            mutate.apply_cond_grow("if (a < b) begin", 0, 0),
            "if (a <= b) begin")
        self.assertEqual("if (a > b) begin",
                         "if (a > b) begin")  # pure: input untouched below
        before = "if (recv_divcnt > cfg_divider) begin"
        self.assertEqual(mutate.apply_cond_grow(before, 0, 0),
                         "if (recv_divcnt >= cfg_divider) begin")
        self.assertEqual(before, "if (recv_divcnt > cfg_divider) begin")

    def test_ignores_shifts_assigns_and_equality(self):
        for line in ("cfg_divider <= DEFAULT_DIV;",
                     "x <= y >> 2;",
                     "if (a == b) begin",
                     "if (a <= b) begin",
                     "endmodule"):
            with self.assertRaises(mutate.NoSiteError, msg=line):
                mutate.apply_cond_grow(line, 0, 0)

    def test_second_site_index(self):
        before = "if (a > b && c < d) begin"
        self.assertEqual(mutate.apply_cond_grow(before, 0, 0),
                         "if (a >= b && c < d) begin")
        self.assertEqual(mutate.apply_cond_grow(before, 1, 0),
                         "if (a > b && c <= d) begin")


class TestLogicSwap(unittest.TestCase):
    """logic_swap: && <-> || (expression-only, always compiles)."""

    def test_swaps_both_directions(self):
        self.assertEqual(
            mutate.apply_logic_swap("if (a && b) begin", 0, 0),
            "if (a || b) begin")
        self.assertEqual(
            mutate.apply_logic_swap("if (a || b) begin", 0, 0),
            "if (a && b) begin")

    def test_pure_and_no_site(self):
        before = "if (send_dummy && !send_bitcnt) begin"
        self.assertEqual(mutate.apply_logic_swap(before, 0, 0),
                         "if (send_dummy || !send_bitcnt) begin")
        self.assertEqual(before, "if (send_dummy && !send_bitcnt) begin")
        with self.assertRaises(mutate.NoSiteError):
            mutate.apply_logic_swap("recv_state <= 0;", 0, 0)
        with self.assertRaises(mutate.NoSiteError):
            mutate.apply_logic_swap("endmodule", 0, 0)


class TestCmpSwapStatementAware(unittest.TestCase):
    """cmp_swap is statement-aware: a depth-0 `<=` with no bare `=`/`?`
    before it is a non-blocking assignment, never a comparison site
    (those used to become `x > 0;` expression statements Verilator
    rejects -- build-breakers, never RED)."""

    def test_statement_assign_le_has_no_site(self):
        for line in ("recv_state <= 0;",
                     "send_pattern <= ~0;",
                     "cfg_divider[ 7: 0] <= reg_div_di[ 7: 0];",
                     "recv_divcnt <= recv_divcnt + 1;",
                     "if (x) y <= 0;"):
            with self.assertRaises(mutate.NoSiteError, msg=line):
                mutate.apply_cmp_swap(line, 0, 0)

    def test_true_comparisons_keep_sites(self):
        self.assertEqual(
            mutate.apply_cmp_swap("if (a <= b) begin", 0, 0),
            "if (a > b) begin")
        self.assertEqual(
            mutate.apply_cmp_swap("assign x = a <= b;", 0, 0),
            "assign x = a > b;")
        self.assertEqual(
            mutate.apply_cmp_swap("if (a == b) begin", 0, 0),
            "if (a != b) begin")

    def test_enumerate_skips_assignment_le_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _tiny_tree(tmp)
            skips = mutate.load_locked_skips(CASES_YAML, TARGETS_DIR)
            only = mutate.enumerate_candidates(src, skips)
            bad = [c for c in only
                   if c["file"] == "picosoc/simpleuart.v"
                   and c["op"] == "cmp_swap"
                   and "recv_divcnt <= 1'b0" in c.get("before", "")]
            self.assertEqual(bad, [])
            # the fixture's real conditions still enumerate
            cond = [c for c in only if c["op"] == "cmp_swap"]
            self.assertTrue(cond)


class TestNewOpDeterminism(unittest.TestCase):
    """Every new op is seeded-deterministic: same seed -> same record."""

    def _op_tree(self, tmp):
        src = os.path.join(tmp, "src")
        os.makedirs(os.path.join(src, "picosoc"))
        with open(os.path.join(src, "picosoc", "simpleuart.v"), "w") as fh:
            fh.write(
                "module m(input clk);\n"
                "wire w = a <= b;\n"
                "if (a > b && c != d) begin\n"
                "r <= 0;\n"
                "y <= a || b;\n"
                "endmodule\n"
            )
        return src

    def test_ops_registered(self):
        for op in ("cmp_swap", "cond_grow", "logic_swap"):
            self.assertIn(op, mutate.OPS)

    def test_same_seed_same_record_per_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._op_tree(tmp)
            skips = mutate.load_locked_skips(CASES_YAML, TARGETS_DIR)
            for op in ("cmp_swap", "cond_grow", "logic_swap", "num_bump"):
                r1 = mutate.mutate_tree(src, os.path.join(tmp, "a-%s" % op),
                                        seed=11, op=op, skips=skips)
                r2 = mutate.mutate_tree(src, os.path.join(tmp, "b-%s" % op),
                                        seed=11, op=op, skips=skips)
                self.assertEqual(r1, r2, op)
                self.assertEqual(r1["op"], op)

    def test_each_new_op_lands_a_site_somewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._op_tree(tmp)
            skips = mutate.load_locked_skips(CASES_YAML, TARGETS_DIR)
            pool = mutate.enumerate_candidates(src, skips)
            have = {c["op"] for c in pool}
            for op in ("cmp_swap", "cond_grow", "logic_swap", "num_bump"):
                self.assertIn(op, have, op)


class TestBlockCommentsIgnored(unittest.TestCase):
    """Block comments are not statements: no operator sites inside
    /* ... */ (e.g. the `<claire@...>` copyright email must not become
    a cond_grow/cmp_swap site)."""

    def test_comment_interior_lines_enumerate_no_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            with open(os.path.join(src, "m.v"), "w") as fh:
                fh.write(
                    "/*\n"
                    " *  Copyright (C) 2015  Claire Xenia Wolf <claire@yosyshq.com>\n"
                    " */\n"
                    "if (a > b) begin\n"
                    "/* verilator lint_off WIDTH */\n"
                )
            skips = mutate.load_locked_skips(CASES_YAML, TARGETS_DIR)
            pool = mutate.enumerate_candidates(src, skips)
            by_line = {}
            for c in pool:
                by_line.setdefault(c["line"], []).append(c["op"])
            self.assertNotIn(1, by_line)
            self.assertNotIn(2, by_line)
            self.assertNotIn(3, by_line)
            self.assertNotIn(5, by_line)
            self.assertIn(4, by_line)

    def test_same_line_block_span_blanked_not_shifted(self):
        before = "if (a < b) begin /* c > d */"
        self.assertEqual(mutate.apply_cond_grow(before, 0, 0),
                         "if (a <= b) begin /* c > d */")
        with self.assertRaises(mutate.NoSiteError):
            mutate.apply_cond_grow(before, 1, 0)


class TestApplyCandidate(unittest.TestCase):
    def test_wrapper_dispatches_and_rejects_unknown_op(self):
        self.assertEqual(
            mutate.apply_candidate("if (a > b) begin", "cond_grow", 0, 0),
            "if (a >= b) begin")
        with self.assertRaises(ValueError):
            mutate.apply_candidate("x;", "nope", 0, 0)
        with self.assertRaises(mutate.NoSiteError):
            mutate.apply_candidate("x;", "cond_grow", 0, 0)


if __name__ == "__main__":
    unittest.main()
