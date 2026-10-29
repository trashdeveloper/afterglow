"""Task C8 (negative learning, plan item 28, Wave C FIX BETTER) tests:
agent/repair/neglearn.py.

Records failed fix shapes per signal into eval/fix_blacklist.md; the
fix prompt consults the blacklist so a blacklisted shape is absent
from the next prompt's candidates.

  record_failure(signal, shape, blacklist_path) -> int (new count)
  load_blacklist(blacklist_path) -> {signal: [shapes]}
  shapes_for(signal, source) -> [shapes]  (source: path | dict | None)
  filter_candidates(candidates, signal, source=None) -> [allowed]
  render_blacklist_section(signal, source=None, mention_shapes=True) -> str
  build_fix_prompt(base, signal, candidates=None, source=None,
                   mention_shapes=True) -> str

Contract:
- record persists (signal, shape) rows; repeat records bump "(xN)".
- load of a missing file is {} (no crash, no file created).
- filter drops blacklisted shapes, keeps order of the rest.
- default prompt names the avoided shapes (prompt includes blacklist)
  and its "Allowed fix shapes:" line omits the blacklisted shape.
- mention_shapes=False: blacklisted shape absent from the WHOLE prompt
  string (strict negative priming); the blacklist is still referenced.
- per-signal isolation: sigA's blacklist never filters sigB's prompt.
- Pure stdlib; no API, no keys, no network. Default-off: explicit call
  only; agent/loop.py, agent/guard.py, eval/score.py, scripts/ci.sh
  untouched (no neglearn wiring).
- File scope: tests write ONLY to scratch dirs (tempfile, the unittest
  equivalent of pytest tmp_path) and NEVER touch the real eval/ dir;
  a guard test asserts eval/fix_blacklist.md does not exist.

RED-first TDD: this file was written BEFORE agent/repair/neglearn.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_neglearn.py -v   (from repo root)
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import neglearn  # noqa: E402  (RED before implementation)


SIG_A = "uart-tx-div"
SIG_B = "picosoc-uart-div"
SHAPE_R = "reset-missing"
SHAPE_O = "off-by-one"
SHAPE_W = "width-mismatch"
ALL_SHAPES = [SHAPE_R, SHAPE_O, SHAPE_W]


def _allowed_line(prompt):
    """Return the 'Allowed fix shapes:' line of a built prompt."""
    for ln in prompt.splitlines():
        if ln.startswith("Allowed fix shapes:"):
            return ln
    raise AssertionError("no Allowed line in prompt:\n%s" % prompt)


class ScratchMixin(unittest.TestCase):
    """Scratch blacklist file under a temp dir (never the real eval/)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bl = os.path.join(self._tmp.name, "fix_blacklist.md")


class TestRecordAndLoad(ScratchMixin):
    def test_record_returns_one_and_file_holds_row(self):
        n = neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        self.assertEqual(n, 1)
        with open(self.bl) as fh:
            text = fh.read()
        self.assertIn(SIG_A, text)
        self.assertIn(SHAPE_R, text)

    def test_load_round_trips_recorded_shape(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        self.assertEqual(neglearn.load_blacklist(self.bl),
                         {SIG_A: [SHAPE_R]})

    def test_repeat_record_bumps_count(self):
        self.assertEqual(neglearn.record_failure(SIG_A, SHAPE_R, self.bl), 1)
        self.assertEqual(neglearn.record_failure(SIG_A, SHAPE_R, self.bl), 2)
        self.assertEqual(neglearn.record_failure(SIG_A, SHAPE_R, self.bl), 3)
        with open(self.bl) as fh:
            text = fh.read()
        self.assertIn("(x3)", text)
        # loaded shape list still holds the shape exactly once
        self.assertEqual(neglearn.load_blacklist(self.bl),
                         {SIG_A: [SHAPE_R]})

    def test_shapes_sorted_per_signal(self):
        for shape in (SHAPE_W, SHAPE_R, SHAPE_O):
            neglearn.record_failure(SIG_A, shape, self.bl)
        self.assertEqual(neglearn.load_blacklist(self.bl),
                         {SIG_A: [SHAPE_O, SHAPE_R, SHAPE_W]})

    def test_per_signal_isolation_in_file(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        neglearn.record_failure(SIG_B, SHAPE_O, self.bl)
        self.assertEqual(neglearn.load_blacklist(self.bl),
                         {SIG_A: [SHAPE_R], SIG_B: [SHAPE_O]})

    def test_names_are_normalized(self):
        neglearn.record_failure("  UART-TX-DIV ", " Reset-Missing\t",
                                self.bl)
        self.assertEqual(neglearn.load_blacklist(self.bl),
                         {SIG_A: [SHAPE_R]})

    def test_missing_file_loads_empty_and_creates_nothing(self):
        self.assertFalse(os.path.exists(self.bl))
        self.assertEqual(neglearn.load_blacklist(self.bl), {})
        self.assertFalse(os.path.exists(self.bl))

    def test_load_ignores_junk_lines(self):
        with open(self.bl, "w") as fh:
            fh.write("random junk\n\n## %s\n- %s\n\nnot a bullet\n"
                     % (SIG_A, SHAPE_R))
        self.assertEqual(neglearn.load_blacklist(self.bl),
                         {SIG_A: [SHAPE_R]})

    def test_record_creates_missing_parent_dirs(self):
        deep = os.path.join(self._tmp.name, "sub", "dir", "bl.md")
        self.assertEqual(neglearn.record_failure(SIG_A, SHAPE_R, deep), 1)
        self.assertEqual(neglearn.load_blacklist(deep), {SIG_A: [SHAPE_R]})

    def test_bad_signal_or_shape_raises(self):
        for bad in ("", "   \n  ", None, 42, ["x"]):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    neglearn.record_failure(bad, SHAPE_R, self.bl)
                with self.assertRaises((TypeError, ValueError)):
                    neglearn.record_failure(SIG_A, bad, self.bl)

    def test_bad_path_raises(self):
        for bad in (None, 42, ["x"]):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    neglearn.record_failure(SIG_A, SHAPE_R, bad)

    def test_real_eval_dir_untouched(self):
        real = os.path.join(ROOT, "eval", "fix_blacklist.md")
        self.assertFalse(os.path.exists(real),
                         "tests must never create the real %s" % real)
        self.assertEqual(neglearn.DEFAULT_BLACKLIST_REL,
                         os.path.join("eval", "fix_blacklist.md"))


class TestShapesForAndFilter(ScratchMixin):
    def test_shapes_for_none_source_is_empty(self):
        self.assertEqual(neglearn.shapes_for(SIG_A, None), [])

    def test_shapes_for_dict_source(self):
        self.assertEqual(neglearn.shapes_for(SIG_A, {SIG_A: [SHAPE_R]}),
                         [SHAPE_R])
        self.assertEqual(neglearn.shapes_for(SIG_B, {SIG_A: [SHAPE_R]}), [])

    def test_shapes_for_path_source(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        self.assertEqual(neglearn.shapes_for(SIG_A, self.bl), [SHAPE_R])
        self.assertEqual(neglearn.shapes_for(SIG_B, self.bl), [])

    def test_filter_drops_blacklisted_keeps_order(self):
        neglearn.record_failure(SIG_A, SHAPE_O, self.bl)
        got = neglearn.filter_candidates(ALL_SHAPES, SIG_A, self.bl)
        self.assertEqual(got, [SHAPE_R, SHAPE_W])

    def test_filter_with_no_blacklist_passes_all_through(self):
        self.assertEqual(neglearn.filter_candidates(ALL_SHAPES, SIG_A,
                                                    self.bl),
                         ALL_SHAPES)
        self.assertEqual(neglearn.filter_candidates(ALL_SHAPES, SIG_A,
                                                    None),
                         ALL_SHAPES)

    def test_filter_is_per_signal(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        self.assertEqual(neglearn.filter_candidates(ALL_SHAPES, SIG_B,
                                                    self.bl),
                         ALL_SHAPES)

    def test_filter_accepts_loaded_dict(self):
        black = {SIG_A: [SHAPE_R, SHAPE_O]}
        self.assertEqual(neglearn.filter_candidates(ALL_SHAPES, SIG_A,
                                                    black),
                         [SHAPE_W])

    def test_filter_empty_candidates_is_empty(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        self.assertEqual(neglearn.filter_candidates([], SIG_A, self.bl), [])

    def test_filter_bad_inputs_raise(self):
        with self.assertRaises((TypeError, ValueError)):
            neglearn.filter_candidates("not-a-list", SIG_A, None)
        with self.assertRaises((TypeError, ValueError)):
            neglearn.filter_candidates([SHAPE_R, ""], SIG_A, None)
        with self.assertRaises((TypeError, ValueError)):
            neglearn.filter_candidates([SHAPE_R, None], SIG_A, None)
        with self.assertRaises((TypeError, ValueError)):
            neglearn.filter_candidates(ALL_SHAPES, "", None)


class TestBuildFixPrompt(ScratchMixin):
    BASE = "Fix the failing uart-tx-div trace window below."

    def test_prompt_includes_blacklist_section(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        prompt = neglearn.build_fix_prompt(self.BASE, SIG_A, ALL_SHAPES,
                                           self.bl)
        self.assertIn(self.BASE, prompt)
        self.assertIn(SHAPE_R, prompt)  # blacklist content quoted ...
        self.assertIn("o NOT retry", prompt)  # ... in an avoidance section

    def test_blacklisted_shape_absent_from_allowed_line(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        prompt = neglearn.build_fix_prompt(self.BASE, SIG_A, ALL_SHAPES,
                                           self.bl)
        allowed = _allowed_line(prompt)
        self.assertNotIn(SHAPE_R, allowed)  # THE negative-learning pin
        self.assertIn(SHAPE_O, allowed)
        self.assertIn(SHAPE_W, allowed)

    def test_strict_mode_shape_absent_from_whole_prompt(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        prompt = neglearn.build_fix_prompt(self.BASE, SIG_A, ALL_SHAPES,
                                           self.bl, mention_shapes=False)
        self.assertNotIn(SHAPE_R, prompt)
        self.assertIn(SHAPE_O, prompt)
        self.assertIn("lacklist", prompt)  # blacklist still referenced

    def test_no_blacklist_no_avoidance_section(self):
        prompt = neglearn.build_fix_prompt(self.BASE, SIG_A, ALL_SHAPES,
                                           self.bl)
        self.assertIn(self.BASE, prompt)
        self.assertNotIn("o NOT retry", prompt)
        allowed = _allowed_line(prompt)
        self.assertIn(SHAPE_R, allowed)

    def test_other_signal_prompt_unaffected(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        prompt = neglearn.build_fix_prompt(self.BASE, SIG_B, ALL_SHAPES,
                                           self.bl)
        allowed = _allowed_line(prompt)
        self.assertIn(SHAPE_R, allowed)

    def test_all_blacklisted_allowed_line_says_so(self):
        for shape in ALL_SHAPES:
            neglearn.record_failure(SIG_A, shape, self.bl)
        prompt = neglearn.build_fix_prompt(self.BASE, SIG_A, ALL_SHAPES,
                                           self.bl)
        allowed = _allowed_line(prompt)
        for shape in ALL_SHAPES:
            self.assertNotIn(shape, allowed)

    def test_candidates_none_gives_base_plus_section(self):
        neglearn.record_failure(SIG_A, SHAPE_R, self.bl)
        prompt = neglearn.build_fix_prompt(self.BASE, SIG_A,
                                           candidates=None,
                                           source=self.bl)
        self.assertIn(self.BASE, prompt)
        self.assertIn(SHAPE_R, prompt)
        with self.assertRaises(AssertionError):
            _allowed_line(prompt)

    def test_bad_base_or_candidates_raise(self):
        with self.assertRaises((TypeError, ValueError)):
            neglearn.build_fix_prompt("", SIG_A, ALL_SHAPES, None)
        with self.assertRaises((TypeError, ValueError)):
            neglearn.build_fix_prompt(self.BASE, SIG_A, "shapes", None)
        with self.assertRaises((TypeError, ValueError)):
            neglearn.build_fix_prompt(self.BASE, "", ALL_SHAPES, None)


class DefaultOffNoWiring(unittest.TestCase):
    def _src(self, rel):
        with open(os.path.join(ROOT, rel)) as fh:
            return fh.read()

    def test_neglearn_imports_nothing_from_loop_or_guard(self):
        src = self._src("agent/repair/neglearn.py")
        for banned in ("from loop import", "from guard import",
                       "import loop", "import guard",
                       "try_apply_diff"):
            self.assertNotIn(banned, src,
                             "neglearn must stay default-off "
                             "(no loop/guard wiring): %r" % banned)

    def test_loop_guard_score_ci_have_no_neglearn_wiring(self):
        for rel in ("agent/loop.py", "agent/guard.py", "eval/score.py",
                    "scripts/ci.sh"):
            src = self._src(rel)
            with self.subTest(file=rel):
                self.assertNotIn("neglearn", src,
                                 "%s untouched: no neglearn wiring" % rel)

    def test_no_api_keys_or_network_in_module(self):
        src = self._src("agent/repair/neglearn.py")
        for banned in ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY",
                       "urllib", "requests", "socket", "os.environ",
                       "subprocess", "http.client"):
            self.assertNotIn(banned, src,
                             "neglearn is pure stdlib "
                             "(no API/keys/network): %r" % banned)


if __name__ == "__main__":
    unittest.main()
