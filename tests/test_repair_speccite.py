"""Task C6 (spec-grounded fix, plan item 26, Wave C FIX BETTER) tests:
agent/repair/speccite.py.

A patch must cite a spec chunk id (from B2 evidence/spec_rag); a
mechanical check rejects uncited patches in strict mode.

  extract_citations(text) -> [chunk ids in encounter order]
  check(patch_text, valid_ids, strict=True)
    -> {"ok": bool, "cited": [...], "unknown": [...], "reason": str}

``valid_ids`` is the B2 corpus id set: an iterable of chunk-id
strings, or of chunk dicts with an ``"id"`` key (the shape returned
by ``spec_rag.retrieve``), so the check builds directly on B2 output.
A citation is ``[<id>]`` where ``<id>`` has the B2 chunk-id shape
``*.md#cNN`` (with optional ``~N`` dedup suffix). Verdicts:

- cited (every citation in the corpus) -> ok True, both modes.
- unknown citation (id outside the corpus) -> ok False, BOTH modes
  (an invented citation never passes).
- no citation + strict=True -> ok False (rejected).
- no citation + strict=False -> ok True with warning (lenient pass).

Contract:
- Refusals: empty/non-string patch -> ValueError; empty valid_ids ->
  ValueError (refusing to check against nothing); non-bool strict ->
  TypeError.
- Pure stdlib; no API, no keys. Default-off: explicit call only;
  agent/loop.py and agent/guard.py untouched (no speccite wiring).

RED-first TDD: this file was written BEFORE agent/repair/speccite.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_speccite.py -v   (from repo root)
"""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import speccite  # noqa: E402  (RED before implementation)
from evidence import spec_rag  # noqa: E402  (B2 ground truth for ids)


# A verbatim anchor line from the design doc (same anchor as the B2
# test: docs/superpowers/specs/2026-10-26-rtl-debug-framework-design.md).
ANCHOR_LINE = ("AG1 (failure even if it works): only runs on our 8 cases "
               "\u2014 must prove on stranger bugs.")

SYN_IDS = ["design-notes.md#c03", "AGENTS.md#c00"]


def _diff_with_trailer(trailer):
    """Build a small unified diff carrying a free-text trailer line."""
    return "\n".join([
        "diff --git a/picorv32.v b/picorv32.v",
        "--- a/picorv32.v",
        "+++ b/picorv32.v",
        "@@ -1446,3 +1446,3 @@",
        " wire stall = 0;",
        "-assign out = a & b;",
        "+assign out = a | b; // %s" % trailer,
        " ",
    ]) + "\n"


def _real_id():
    """One genuine B2 corpus id, retrieved via the B2 anchor query."""
    chunks = spec_rag.load_default_corpus(ROOT)
    query = spec_rag.build_query(
        fault_file="picorv32.v", fault_line=1446,
        context_tokens=[ANCHOR_LINE])
    hits = spec_rag.retrieve(query, chunks, top_k=1)
    assert hits, "B2 anchor query returned no hits"
    return hits[0]["id"], [c["id"] for c in chunks]


class TestExtract(unittest.TestCase):
    def test_finds_single_citation(self):
        text = _diff_with_trailer("Spec: [design-notes.md#c03]")
        self.assertEqual(speccite.extract_citations(text),
                         ["design-notes.md#c03"])

    def test_finds_multiple_in_encounter_order(self):
        text = ("see [AGENTS.md#c00] and [design-notes.md#c03] "
                "also [AGENTS.md#c00] again")
        self.assertEqual(speccite.extract_citations(text),
                         ["AGENTS.md#c00", "design-notes.md#c03"])

    def test_dedup_suffix_shape_counts(self):
        text = "cite [design-notes.md#c03~1] here"
        self.assertEqual(speccite.extract_citations(text),
                         ["design-notes.md#c03~1"])

    def test_no_citation_returns_empty(self):
        self.assertEqual(speccite.extract_citations(
            _diff_with_trailer("no citation here")), [])
        self.assertEqual(speccite.extract_citations(""), [])

    def test_non_citation_brackets_ignored(self):
        # Not the B2 *.md#cNN shape -> not a spec citation.
        text = "see [NOTE] and [foo#c01] and [design-notes.md] here"
        self.assertEqual(speccite.extract_citations(text), [])

    def test_non_string_raises(self):
        for bad in (None, 42, ["[AGENTS.md#c00]"]):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    speccite.extract_citations(bad)


class TestCitedPasses(unittest.TestCase):
    def test_cited_passes_strict_with_synthetic_ids(self):
        patch = _diff_with_trailer("Spec: [%s]" % SYN_IDS[0])
        verdict = speccite.check(patch, SYN_IDS, strict=True)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["cited"], [SYN_IDS[0]])
        self.assertEqual(verdict["unknown"], [])

    def test_cited_passes_lenient_with_synthetic_ids(self):
        patch = _diff_with_trailer("Spec: [%s]" % SYN_IDS[1])
        verdict = speccite.check(patch, SYN_IDS, strict=False)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["cited"], [SYN_IDS[1]])

    def test_cited_passes_strict_with_real_b2_corpus(self):
        # Builds on B2: id retrieved by spec_rag from the real corpus
        # must satisfy the mechanical check against that corpus.
        real_id, corpus_ids = _real_id()
        patch = _diff_with_trailer("Spec: [%s]" % real_id)
        verdict = speccite.check(patch, corpus_ids, strict=True)
        self.assertTrue(verdict["ok"])
        self.assertIn(real_id, verdict["cited"])

    def test_chunk_dicts_accepted_as_valid_ids(self):
        # check() takes B2 retrieve() rows directly (dicts with "id").
        chunks = spec_rag.load_default_corpus(ROOT)
        query = spec_rag.build_query(
            fault_file="picorv32.v", fault_line=1446,
            context_tokens=[ANCHOR_LINE])
        hits = spec_rag.retrieve(query, chunks, top_k=1)
        patch = _diff_with_trailer("Spec: [%s]" % hits[0]["id"])
        verdict = speccite.check(patch, hits, strict=True)
        self.assertTrue(verdict["ok"])

    def test_reason_names_the_cited_id(self):
        patch = _diff_with_trailer("Spec: [%s]" % SYN_IDS[0])
        verdict = speccite.check(patch, SYN_IDS, strict=True)
        self.assertIn(SYN_IDS[0], verdict["reason"])


class TestUncitedRejected(unittest.TestCase):
    def test_uncited_rejected_in_strict_mode(self):
        patch = _diff_with_trailer("no citation here")
        verdict = speccite.check(patch, SYN_IDS, strict=True)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["cited"], [])
        self.assertEqual(verdict["reason"], speccite.UNCITED_STRICT)

    def test_uncited_passes_with_warning_in_lenient_mode(self):
        patch = _diff_with_trailer("no citation here")
        verdict = speccite.check(patch, SYN_IDS, strict=False)
        self.assertTrue(verdict["ok"])
        self.assertEqual(verdict["reason"], speccite.UNCITED_LENIENT)

    def test_strict_is_the_default(self):
        self.assertTrue(speccite.STRICT_DEFAULT)
        patch = _diff_with_trailer("no citation here")
        self.assertFalse(speccite.check(patch, SYN_IDS)["ok"])

    def test_bracket_noise_is_still_uncited(self):
        patch = _diff_with_trailer("see [NOTE] only")
        self.assertFalse(speccite.check(patch, SYN_IDS,
                                        strict=True)["ok"])


class TestInventedRejected(unittest.TestCase):
    def test_unknown_id_rejected_even_in_strict(self):
        patch = _diff_with_trailer("Spec: [invented.md#c99]")
        verdict = speccite.check(patch, SYN_IDS, strict=True)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["unknown"], ["invented.md#c99"])
        self.assertEqual(verdict["cited"], [])

    def test_unknown_id_rejected_even_when_lenient(self):
        # An invented citation never passes: leniency covers MISSING
        # citations, not fabricated ones.
        patch = _diff_with_trailer("Spec: [invented.md#c99]")
        verdict = speccite.check(patch, SYN_IDS, strict=False)
        self.assertFalse(verdict["ok"])

    def test_mixed_known_and_unknown_rejected(self):
        patch = _diff_with_trailer(
            "Spec: [%s] plus [invented.md#c99]" % SYN_IDS[0])
        verdict = speccite.check(patch, SYN_IDS, strict=True)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["unknown"], ["invented.md#c99"])
        self.assertEqual(verdict["cited"], [SYN_IDS[0]])


class TestRefusals(unittest.TestCase):
    def test_empty_or_non_string_patch_raises(self):
        for bad in ("", "   \n  ", None, 42, ["+x"]):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    speccite.check(bad, SYN_IDS, strict=True)

    def test_empty_valid_ids_raises(self):
        patch = _diff_with_trailer("Spec: [%s]" % SYN_IDS[0])
        for bad in (None, [], set()):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    speccite.check(patch, bad, strict=True)

    def test_non_bool_strict_raises(self):
        patch = _diff_with_trailer("no citation here")
        for bad in ("yes", 1, 0, None):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    speccite.check(patch, SYN_IDS, strict=bad)


class TestUntouchedLoopGuard(unittest.TestCase):
    def test_loop_and_guard_do_not_wire_speccite(self):
        # C6 is additive + default-off: loop.py / guard.py must not
        # import the repair package (existing behavior byte-identical).
        for rel in (os.path.join("agent", "loop.py"),
                    os.path.join("agent", "guard.py")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(
                re.search(r"^\s*(import|from)\s+\S*(repair|speccite)",
                          src, re.M),
                "%s wires speccite" % rel)


class TestStdlibOnly(unittest.TestCase):
    def test_module_is_stdlib_only(self):
        # No sockets, no HTTP, no API clients: pure mechanical check.
        with open(os.path.join(ROOT, "agent", "repair", "speccite.py"),
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


if __name__ == "__main__":
    unittest.main()
