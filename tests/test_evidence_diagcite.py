"""Task F8 (Spec-cited diagnosis, plan item 68) red-first tests:
agent/evidence/diagcite.py.

A diagnosis must cite a spec chunk id (from B2 evidence/spec_rag),
checked with the SAME pass/reject contract as patches via
agent/repair/speccite.py:

  check_diagnosis(text, valid_ids, strict=True)
    -> {"ok": bool, "cited": [...], "unknown": [...], "reason": str}

``valid_ids`` is the B2 corpus id set (same shape as speccite.check
accepts: chunk-id strings or chunk dicts with an "id" key).
Verdicts (same contract as patches):

- cited (every citation in the corpus) -> ok True, both modes.
- unknown citation (id outside the corpus) -> ok False, BOTH modes
  (an invented citation never passes).
- no citation + strict=True -> ok False (rejected).
- no citation + strict=False -> ok True with warning (lenient pass).

Contract:
- Refusals: empty/non-string text -> ValueError; empty valid_ids ->
  ValueError; non-bool strict -> TypeError (all inherited from
  speccite.check).
- Consumes agent/repair/speccite.py (extract_citations, check).
- Pure stdlib + repo imports; no API, no keys. Default-off: explicit
  call only; agent/loop.py and agent/guard.py untouched.

RED-first TDD: this file was written BEFORE
agent/evidence/diagcite.py existed, so the first run must FAIL on
the missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_evidence_diagcite.py -v   (from repo root)
"""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import diagcite  # noqa: E402  (RED before implementation)
from repair import speccite  # noqa: E402  (contract reference)
from evidence import spec_rag  # noqa: E402  (B2 ground truth for ids)


# A verbatim anchor line from the design doc (same anchor as the B2
# test: docs/superpowers/specs/2026-10-26-rtl-debug-framework-design.md).
ANCHOR_LINE = ("AG1 (failure even if it works): only runs on our 8 cases "
               "\u2014 must prove on stranger bugs.")

SYN_IDS = ["design-notes.md#c03", "AGENTS.md#c00"]


def _diagnosis_with_trailer(trailer):
    """Build a free-text diagnosis carrying a citation trailer line."""
    return ("\n".join([
        "Diagnosis: picorv32.v line 1446 stalls the fetch stage;",
        "the divisor constant disagrees with the baud-rate table.",
        "Spec: %s" % trailer,
    ])) + "\n"


def _real_id():
    """One genuine B2 corpus id, retrieved via the B2 anchor query."""
    chunks = spec_rag.load_default_corpus(ROOT)
    query = spec_rag.build_query(
        fault_file="picorv32.v", fault_line=1446,
        context_tokens=[ANCHOR_LINE])
    hits = spec_rag.retrieve(query, chunks, top_k=1)
    assert hits, "B2 anchor query returned no hits"
    return hits[0]["id"], [c["id"] for c in chunks]


class TestCitedPasses(unittest.TestCase):
    def test_cited_passes_strict_with_synthetic_ids(self):
        text = _diagnosis_with_trailer("See [%s]." % SYN_IDS[0])
        verdict = diagcite.check_diagnosis(text, SYN_IDS, strict=True)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["cited"], [SYN_IDS[0]])
        self.assertEqual(verdict["unknown"], [])

    def test_cited_passes_lenient_with_synthetic_ids(self):
        text = _diagnosis_with_trailer("See [%s]." % SYN_IDS[1])
        verdict = diagcite.check_diagnosis(text, SYN_IDS, strict=False)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["cited"], [SYN_IDS[1]])

    def test_cited_passes_strict_with_real_b2_corpus(self):
        # Builds on B2: id retrieved by spec_rag from the real corpus
        # must satisfy the diagnosis check against that corpus.
        real_id, corpus_ids = _real_id()
        text = _diagnosis_with_trailer("See [%s]." % real_id)
        verdict = diagcite.check_diagnosis(text, corpus_ids, strict=True)
        self.assertTrue(verdict["ok"])
        self.assertIn(real_id, verdict["cited"])

    def test_chunk_dicts_accepted_as_valid_ids(self):
        # check_diagnosis() takes B2 retrieve() rows directly (dicts
        # with "id"), same as speccite.check.
        chunks = spec_rag.load_default_corpus(ROOT)
        query = spec_rag.build_query(
            fault_file="picorv32.v", fault_line=1446,
            context_tokens=[ANCHOR_LINE])
        hits = spec_rag.retrieve(query, chunks, top_k=1)
        text = _diagnosis_with_trailer("See [%s]." % hits[0]["id"])
        verdict = diagcite.check_diagnosis(text, hits, strict=True)
        self.assertTrue(verdict["ok"])

    def test_reason_names_the_cited_id(self):
        text = _diagnosis_with_trailer("See [%s]." % SYN_IDS[0])
        verdict = diagcite.check_diagnosis(text, SYN_IDS, strict=True)
        self.assertIn(SYN_IDS[0], verdict["reason"])


class TestUncitedRejected(unittest.TestCase):
    def test_uncited_rejected_in_strict_mode(self):
        text = _diagnosis_with_trailer("no citation here")
        verdict = diagcite.check_diagnosis(text, SYN_IDS, strict=True)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["cited"], [])
        self.assertEqual(verdict["reason"], speccite.UNCITED_STRICT)

    def test_uncited_passes_with_warning_in_lenient_mode(self):
        text = _diagnosis_with_trailer("no citation here")
        verdict = diagcite.check_diagnosis(text, SYN_IDS, strict=False)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["reason"], speccite.UNCITED_LENIENT)

    def test_strict_is_the_default(self):
        self.assertTrue(diagcite.STRICT_DEFAULT)
        text = _diagnosis_with_trailer("no citation here")
        self.assertFalse(diagcite.check_diagnosis(text, SYN_IDS)["ok"])

    def test_bracket_noise_is_still_uncited(self):
        text = _diagnosis_with_trailer("see [NOTE] only")
        self.assertFalse(
            diagcite.check_diagnosis(text, SYN_IDS, strict=True)["ok"])


class TestInventedRejected(unittest.TestCase):
    def test_unknown_id_rejected_even_in_strict(self):
        text = _diagnosis_with_trailer("See [invented.md#c99].")
        verdict = diagcite.check_diagnosis(text, SYN_IDS, strict=True)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["unknown"], ["invented.md#c99"])
        self.assertEqual(verdict["cited"], [])

    def test_unknown_id_rejected_even_when_lenient(self):
        # An invented citation never passes: leniency covers MISSING
        # citations, not fabricated ones.
        text = _diagnosis_with_trailer("See [invented.md#c99].")
        verdict = diagcite.check_diagnosis(text, SYN_IDS, strict=False)
        self.assertFalse(verdict["ok"])

    def test_mixed_known_and_unknown_rejected(self):
        text = _diagnosis_with_trailer(
            "See [%s] plus [invented.md#c99]." % SYN_IDS[0])
        verdict = diagcite.check_diagnosis(text, SYN_IDS, strict=True)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["unknown"], ["invented.md#c99"])
        self.assertEqual(verdict["cited"], [SYN_IDS[0]])


class TestSameContractAsPatches(unittest.TestCase):
    def test_verdict_matches_speccite_check(self):
        # Same pass/reject contract: identical verdicts for cited,
        # uncited, and invented inputs in both modes.
        cases = [
            _diagnosis_with_trailer("See [%s]." % SYN_IDS[0]),
            _diagnosis_with_trailer("no citation here"),
            _diagnosis_with_trailer("See [invented.md#c99]."),
        ]
        for text in cases:
            for strict in (True, False):
                with self.subTest(text=text[:40], strict=strict):
                    self.assertEqual(
                        diagcite.check_diagnosis(
                            text, SYN_IDS, strict=strict),
                        speccite.check(text, SYN_IDS, strict=strict))

    def test_consumes_speccite_extract_citations(self):
        # diagcite re-uses speccite extraction (same ids, same order).
        text = ("see [AGENTS.md#c00] and [design-notes.md#c03] "
                "also [AGENTS.md#c00] again")
        self.assertEqual(diagcite.extract_citations(text),
                         speccite.extract_citations(text))


class TestRefusals(unittest.TestCase):
    def test_empty_or_non_string_text_raises(self):
        for bad in ("", "   \n  ", None, 42, ["+x"]):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    diagcite.check_diagnosis(bad, SYN_IDS, strict=True)

    def test_empty_valid_ids_raises(self):
        text = _diagnosis_with_trailer("See [%s]." % SYN_IDS[0])
        for bad in (None, [], set()):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    diagcite.check_diagnosis(text, bad, strict=True)

    def test_non_bool_strict_raises(self):
        text = _diagnosis_with_trailer("no citation here")
        for bad in ("yes", 1, 0, None):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    diagcite.check_diagnosis(text, SYN_IDS, strict=bad)


class TestUntouchedLoopGuard(unittest.TestCase):
    def test_loop_and_guard_do_not_wire_diagcite(self):
        # F8 is additive + default-off: loop.py / guard.py must not
        # import the evidence diagcite module.
        for rel in (os.path.join("agent", "loop.py"),
                    os.path.join("agent", "guard.py")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(
                re.search(r"^\s*(import|from)\s+\S*(evidence|diagcite)",
                          src, re.M),
                "%s wires diagcite" % rel)

    def test_diagcite_consumes_speccite(self):
        with open(os.path.join(ROOT, "agent", "evidence", "diagcite.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("speccite", src,
                      "diagcite.py must consume agent/repair/speccite.py")


class TestStdlibOnly(unittest.TestCase):
    def test_module_imports_are_stdlib_or_repo_only(self):
        # No sockets, no HTTP, no API clients: pure mechanical check
        # over speccite + stdlib.
        with open(os.path.join(ROOT, "agent", "evidence", "diagcite.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = {"requests", "urllib", "socket", "http",
                     "openai", "anthropic"}
        self.assertTrue(imported.isdisjoint(forbidden),
                        "non-stdlib/API imports: %s"
                        % (imported & forbidden))
        # Repo imports are limited to the speccite provider packages.
        allowed_repo = {"agent", "repair", "evidence", "speccite"}
        non_stdlib = {m for m in imported
                      if m not in allowed_repo and
                      m not in sys.stdlib_module_names}
        self.assertEqual(non_stdlib, set(),
                         "unexpected third-party imports: %s" % non_stdlib)


if __name__ == "__main__":
    unittest.main()
