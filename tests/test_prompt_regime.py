#!/usr/bin/env python3
"""Strategy-regime pivot for agent/prompts/v3.txt (TDD, no API calls).

Contract under test (prompt text in agent/prompts/v3.txt):
  (1) DEFAULT path unchanged: strict-verbatim regime is the backstop.
      The original strict rules (verbatim unified diff, <=40 lines,
      never force a pass, trace wins) must still be present word-for-word
      in spirit, plus an explicit DEFAULT=strict marker.
  (2) DIVERGENCE path requires an explicit opt-in flag: non-verbatim
      exploration is allowed ONLY when the operator passes the explicit
      token ALLOW_DIVERGENCE=1. Any other value (missing/empty/0/no)
      stays strict.
  (3) VERIFY gate in BOTH regimes: no patch lands without a resim
      VERIFY checksum MATCH. Unverified patches never apply, whether
      strict or divergence-allowed.

The two pure helpers below (resolve_regime / may_apply_patch) are the
executable specification of the prompt's regime rule: they encode
exactly what v3.txt documents. The prompt-text tests pin the wording;
the helper tests prove the three gate properties logically.

RED-first TDD: this file was written BEFORE the v3.txt regime block
existed, so the first run MUST fail on the missing regime markers
(proving the tests exercise the prompt, not a stub).

Run: python3 tests/test_prompt_regime.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

EXPLICIT_FLAG = "ALLOW_DIVERGENCE=1"


def _read_v3():
    with open(os.path.join(ROOT, "agent", "prompts", "v3.txt"),
              encoding="utf-8") as fh:
        return fh.read()


def resolve_regime(opt_in):
    """Executable spec of the prompt's regime pivot.

    Returns "divergence" ONLY for the explicit opt-in token
    ALLOW_DIVERGENCE=1 (or boolean True as its programmatic form);
    every other input resolves to the "strict" default backstop.
    """
    if opt_in is True:
        return "divergence"
    if isinstance(opt_in, str) and opt_in.strip() == EXPLICIT_FLAG:
        return "divergence"
    return "strict"


def may_apply_patch(verify_match):
    """Executable spec of the resim VERIFY gate (both regimes).

    A patch may land ONLY when the resim reports checksum MATCH
    (verify_match is True). Anything else -- False, None, missing,
    "MATCH" strings, truthy non-True values -- never applies.
    """
    return verify_match is True


class TestDefaultIsStrictBackstop(unittest.TestCase):
    def test_default_regime_marked_strict_verbatim(self):
        v3 = _read_v3()
        self.assertIn("STRICT-VERBATIM", v3)
        self.assertIn("DEFAULT", v3)

    def test_original_strict_rules_still_present(self):
        v3 = _read_v3()
        # Verbatim-diff rule, 40-line cap, never-force-pass, trace wins.
        self.assertIn("VERBATIM unified diff", v3)
        self.assertIn("40", v3)
        self.assertIn("NEVER force a pass", v3)
        self.assertIn("the trace wins", v3)

    def test_default_path_resolves_strict(self):
        for missing in (None, "", "0", "no", False, "ALLOW_DIVERGENCE=0",
                        "allow_divergence=1", "  "):
            with self.subTest(opt_in=missing):
                self.assertEqual(resolve_regime(missing), "strict")

    def test_original_slots_survive_regime_addition(self):
        v3 = _read_v3()
        for slot in ("{{CASE}}", "{{RTL_FILE}}", "{{RTL_EXCERPT}}",
                     "{{HARVEST}}", "{{TRACE}}", "{{VERDICT}}"):
            self.assertIn(slot, v3)


class TestDivergenceRequiresExplicitFlag(unittest.TestCase):
    def test_prompt_names_explicit_opt_in_flag(self):
        v3 = _read_v3()
        self.assertIn(EXPLICIT_FLAG, v3)

    def test_prompt_gates_divergence_on_flag_only(self):
        v3 = _read_v3()
        lowered = v3.lower()
        self.assertIn("divergence", lowered)
        # The prompt must say divergence happens ONLY with the flag.
        self.assertRegex(v3, r"[Oo][Nn][Ll][Yy].*ALLOW_DIVERGENCE=1")

    def test_explicit_flag_resolves_divergence(self):
        self.assertEqual(resolve_regime(EXPLICIT_FLAG), "divergence")
        self.assertEqual(resolve_regime(True), "divergence")
        self.assertEqual(
            resolve_regime("  ALLOW_DIVERGENCE=1  "), "divergence")

    def test_near_miss_flags_stay_strict(self):
        for near in ("ALLOW_DIVERGENCE", "ALLOW_DIVERGENCE=0",
                     "ALLOW_DIVERGENCE = 1", "allow_divergence=1",
                     "ALLOW-DIVERGENCE=1", "1", 1, 0):
            with self.subTest(opt_in=near):
                self.assertEqual(resolve_regime(near), "strict")


class TestVerifyGateBothRegimes(unittest.TestCase):
    def test_prompt_requires_verify_match_in_both_regimes(self):
        v3 = _read_v3()
        self.assertIn("VERIFY", v3)
        self.assertIn("MATCH", v3)
        lowered = v3.lower()
        self.assertIn("both regime", lowered)
        self.assertIn("never apply", lowered)

    def test_unverified_patches_never_apply_strict(self):
        self.assertEqual(resolve_regime(None), "strict")
        for bad in (False, None, 0, "", "MATCH", 1, "match"):
            with self.subTest(verify=bad):
                self.assertFalse(may_apply_patch(bad))

    def test_unverified_patches_never_apply_divergence(self):
        self.assertEqual(resolve_regime(EXPLICIT_FLAG), "divergence")
        for bad in (False, None, 0, "", "MATCH", 1, "match"):
            with self.subTest(verify=bad):
                self.assertFalse(may_apply_patch(bad))

    def test_verified_match_applies_in_either_regime(self):
        for regime_flag in (None, EXPLICIT_FLAG):
            with self.subTest(flag=regime_flag):
                regime = resolve_regime(regime_flag)
                self.assertIn(regime, ("strict", "divergence"))
                self.assertTrue(may_apply_patch(True))


class TestScopeGuard(unittest.TestCase):
    def test_loop_and_guard_unwired(self):
        import re
        for rel in (os.path.join("agent", "loop.py"),
                    os.path.join("agent", "guard.py")):
            path = os.path.join(ROOT, rel)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(
                re.search(r"ALLOW_DIVERGENCE|STRATEGY.?REGIME", src),
                "%s must not wire the prompt regime" % rel)


if __name__ == "__main__":
    unittest.main()
