"""Task C4 (fix ranking, plan item 24, Wave C FIX BETTER) tests:
agent/repair/rankfix.py.

Collect up to 3 candidate patches across attempts, resim each, ship
the smallest passing patch.

  diff_size(patch) -> int
  collect_candidates(attempts, limit=3) -> list[str]
  rank_candidates(candidates, resim_fn, limit=3)
    -> {"winner": patch|None, "results": [...]}

``collect_candidates`` pulls patch texts out of loop-style attempt
records (``{"attempt": N, "patch": <diff>}``, see
``loop.make_stack_entry``) in encounter order, skipping attempts with
no usable patch, capped at ``limit`` (default 3).

``rank_candidates`` resims EVERY considered candidate via
``resim_fn(patch)`` (bool or ``{"passed": bool}``; an exception is
recorded as a fail, never a pass) and ships the smallest passing
patch by ``diff_size`` (ties -> earliest attempt order). No passing
candidate -> ``winner`` None. Only the first ``limit`` candidates are
considered/resimmed.

Contract:
- 3 passing candidates of different sizes -> winner is the smallest
  (resim called 3 times: resim EACH, no first-win short-circuit).
- Order independence: [large, small, med] all passing -> small wins.
- Single pass among fails -> that patch wins regardless of size.
- All fail -> winner None.
- resim_fn raising on one candidate -> that candidate fails, the rest
  still rank (no loud abort).
- Empty candidates / non-string patch / non-callable resim_fn ->
  ValueError/TypeError (refuse to fabricate a ranking).
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no rankfix wiring).

RED-first TDD: this file was written BEFORE agent/repair/rankfix.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_rankfix.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import rankfix  # noqa: E402  (RED before implementation)


def _diff(n_minus, n_plus, tag="picorv32.v"):
    """Build a unified diff with n_minus removals + n_plus additions."""
    lines = ["diff --git a/%s b/%s" % (tag, tag),
             "--- a/%s" % tag,
             "+++ b/%s" % tag,
             "@@ -1,%d +1,%d @@" % (n_minus, n_plus)]
    for i in range(n_minus):
        lines.append("-wire old_%d = resetn && go;" % i)
    for i in range(n_plus):
        lines.append("+wire new_%d = resetn || go;" % i)
    return "\n".join(lines) + "\n"


SMALL_DIFF = _diff(1, 1)    # size 2
MED_DIFF = _diff(2, 4)      # size 6
LARGE_DIFF = _diff(4, 8)    # size 12


def _always(pass_set):
    """resim_fn fixture: passes iff the patch is in pass_set."""
    calls = []

    def _resim(patch):
        calls.append(patch)
        return patch in pass_set

    _resim.calls = calls
    return _resim


class TestDiffSize(unittest.TestCase):
    def test_counts_plus_minus_lines(self):
        self.assertEqual(rankfix.diff_size(SMALL_DIFF), 2)
        self.assertEqual(rankfix.diff_size(MED_DIFF), 6)
        self.assertEqual(rankfix.diff_size(LARGE_DIFF), 12)

    def test_ignores_plusplus_minusminus_headers(self):
        self.assertEqual(rankfix.diff_size(SMALL_DIFF), 2)  # has +++/--- lines

    def test_empty_or_non_string_raises(self):
        for bad in ("", "   \n  ", None, 42, ["+x"]):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    rankfix.diff_size(bad)

    def test_max_constant_is_three(self):
        self.assertEqual(rankfix.MAX_CANDIDATES, 3)


class TestCollectCandidates(unittest.TestCase):
    def test_pulls_patches_in_order(self):
        attempts = [
            {"attempt": 1, "patch": SMALL_DIFF},
            {"attempt": 2, "patch": MED_DIFF},
        ]
        self.assertEqual(rankfix.collect_candidates(attempts),
                         [SMALL_DIFF, MED_DIFF])

    def test_skips_attempts_without_usable_patch(self):
        attempts = [
            {"attempt": 1, "patch": ""},
            {"attempt": 2},  # no patch key at all
            {"attempt": 3, "patch": SMALL_DIFF},
        ]
        self.assertEqual(rankfix.collect_candidates(attempts), [SMALL_DIFF])

    def test_caps_at_three(self):
        attempts = [{"attempt": i, "patch": _diff(1, i)} for i in (1, 2, 3, 4, 5)]
        got = rankfix.collect_candidates(attempts)
        self.assertEqual(len(got), 3)
        self.assertEqual(got, [a["patch"] for a in attempts[:3]])

    def test_limit_param_collects_fewer(self):
        attempts = [{"attempt": i, "patch": _diff(1, i)} for i in (1, 2, 3)]
        self.assertEqual(len(rankfix.collect_candidates(attempts, limit=2)), 2)

    def test_bad_limit_raises(self):
        attempts = [{"attempt": 1, "patch": SMALL_DIFF}]
        for bad in (0, -1, 4, "3"):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    rankfix.collect_candidates(attempts, limit=bad)

    def test_non_list_attempts_raises(self):
        for bad in (None, "patch", {"attempt": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    rankfix.collect_candidates(bad)

    def test_accepts_raw_diff_strings(self):
        self.assertEqual(rankfix.collect_candidates([SMALL_DIFF, MED_DIFF]),
                         [SMALL_DIFF, MED_DIFF])

    def test_no_usable_patch_returns_empty(self):
        self.assertEqual(
            rankfix.collect_candidates([{"attempt": 1, "patch": ""}]), [])


class TestRankCandidates(unittest.TestCase):
    def test_picks_smallest_of_three_passing(self):
        resim = _always({SMALL_DIFF, MED_DIFF, LARGE_DIFF})
        out = rankfix.rank_candidates([LARGE_DIFF, MED_DIFF, SMALL_DIFF],
                                      resim)
        self.assertEqual(out["winner"], SMALL_DIFF)
        # resim EACH: no first-win short-circuit, all 3 resimmed.
        self.assertEqual(len(resim.calls), 3)
        self.assertEqual(len(out["results"]), 3)
        for row in out["results"]:
            self.assertTrue(row["passed"])

    def test_results_carry_sizes(self):
        resim = _always({SMALL_DIFF, MED_DIFF, LARGE_DIFF})
        out = rankfix.rank_candidates([LARGE_DIFF, MED_DIFF, SMALL_DIFF],
                                      resim)
        by_patch = {r["patch"]: r["size"] for r in out["results"]}
        self.assertEqual(by_patch, {LARGE_DIFF: 12, MED_DIFF: 6, SMALL_DIFF: 2})

    def test_single_pass_wins_regardless_of_size(self):
        resim = _always({LARGE_DIFF})
        out = rankfix.rank_candidates([SMALL_DIFF, MED_DIFF, LARGE_DIFF],
                                      resim)
        self.assertEqual(out["winner"], LARGE_DIFF)

    def test_all_fail_gives_no_winner(self):
        resim = _always(set())
        out = rankfix.rank_candidates([SMALL_DIFF, MED_DIFF, LARGE_DIFF],
                                      resim)
        self.assertIsNone(out["winner"])
        self.assertEqual(len(resim.calls), 3)

    def test_resim_exception_is_a_fail_not_abort(self):
        def _flaky(patch):
            if patch == MED_DIFF:
                raise RuntimeError("sim error: build failed")
            return patch == SMALL_DIFF

        out = rankfix.rank_candidates([MED_DIFF, SMALL_DIFF], _flaky)
        self.assertEqual(out["winner"], SMALL_DIFF)
        med_row = [r for r in out["results"] if r["patch"] == MED_DIFF][0]
        self.assertFalse(med_row["passed"])
        self.assertIn("error", med_row)

    def test_tie_break_is_earliest_attempt(self):
        twin_a = _diff(1, 1, tag="a.v")
        twin_b = _diff(1, 1, tag="b.v")
        self.assertEqual(rankfix.diff_size(twin_a), rankfix.diff_size(twin_b))
        resim = _always({twin_a, twin_b})
        out = rankfix.rank_candidates([twin_a, twin_b], resim)
        self.assertEqual(out["winner"], twin_a)

    def test_only_first_three_considered(self):
        fourth = _diff(1, 0)  # size 1: smallest, but 4th -> ignored
        resim = _always({SMALL_DIFF, MED_DIFF, LARGE_DIFF, fourth})
        out = rankfix.rank_candidates(
            [LARGE_DIFF, MED_DIFF, SMALL_DIFF, fourth], resim)
        self.assertEqual(out["winner"], SMALL_DIFF)
        self.assertEqual(len(out["results"]), 3)
        self.assertEqual(len(resim.calls), 3)
        self.assertNotIn(fourth, resim.calls)

    def test_dict_style_resim_results_accepted(self):
        def _resim(patch):
            return {"passed": patch == MED_DIFF, "cycles": 100}

        out = rankfix.rank_candidates([SMALL_DIFF, MED_DIFF], _resim)
        self.assertEqual(out["winner"], MED_DIFF)

    def test_empty_candidates_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            rankfix.rank_candidates([], _always(set()))

    def test_non_string_candidate_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            rankfix.rank_candidates([SMALL_DIFF, None], _always({SMALL_DIFF}))

    def test_non_callable_resim_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            rankfix.rank_candidates([SMALL_DIFF], "not-a-callable")

    def test_bad_limit_raises(self):
        for bad in (0, -1, 4):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    rankfix.rank_candidates([SMALL_DIFF], _always({SMALL_DIFF}),
                                            limit=bad)


class DefaultOffNoGuardWiring(unittest.TestCase):
    def test_rankfix_imports_nothing_from_loop_or_guard(self):
        path = os.path.join(ROOT, "agent", "repair", "rankfix.py")
        with open(path) as fh:
            src = fh.read()
        for banned in ("from loop import", "from guard import",
                       "import loop", "import guard",
                       "try_apply_diff"):
            self.assertNotIn(banned, src,
                             "rankfix must stay default-off "
                             "(no loop/guard wiring): %r" % banned)

    def test_loop_and_guard_have_no_rankfix_wiring(self):
        for rel in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, rel)
            with open(path) as fh:
                src = fh.read()
            for banned in ("rankfix", "rank_candidates", "collect_candidates",
                           "diff_size"):
                with self.subTest(file=rel, banned=banned):
                    self.assertNotIn(
                        banned, src,
                        "%s untouched: no rankfix wiring" % rel)

    def test_no_api_or_keys_in_module(self):
        path = os.path.join(ROOT, "agent", "repair", "rankfix.py")
        with open(path) as fh:
            src = fh.read()
        for banned in ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY",
                       "urllib", "requests", "socket", "os.environ"):
            self.assertNotIn(banned, src,
                             "rankfix is SIM-ONLY pure stdlib "
                             "(no API/keys): %r" % banned)


if __name__ == "__main__":
    unittest.main()
