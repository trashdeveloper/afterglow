"""Task B2 (Spec RAG, plan item 12) red-first tests:
agent/evidence/spec_rag.py.

- Chunks docs/ spec excerpts (design doc docs/superpowers/specs/*.md
  and/or AGENTS.md sections), Jaccard retrieval per fault, cites chunk
  ids in prompt output.
- Query = fault file + line + surrounding RTL tokens (built by caller
  via build_query, passed in).
- Anchor test: query built from a known anchor line returns the chunk
  containing that anchor line.
- Unknown query returns empty list (never invents chunks).
- Pure stdlib; no API, no keys.
- Default-off: retrieval runs only when caller requests it via
  enable_spec_rag=True; the existing loop behavior never changes
  (no loop edits).

Run: python3 tests/test_evidence_spec_rag.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import spec_rag  # noqa: E402  (RED before implementation)


# A verbatim anchor line from the design doc
# docs/superpowers/specs/2026-10-26-rtl-debug-framework-design.md:15.
# Distinctive tokens (ag1, stranger) pin it to one section.
ANCHOR_LINE = ("AG1 (failure even if it works): only runs on our 8 cases "
               "\u2014 must prove on stranger bugs.")

# Gibberish tokens verified absent from every doc in the default corpus
# (grep for xqztk / vbwjlm / zqxkqv under docs/ + AGENTS.md: no hits).
UNKNOWN_QUERY = "xqztk vbwjlm zqxkqv"


def _default_chunks():
    return spec_rag.load_default_corpus(ROOT)


class TestChunking(unittest.TestCase):
    def test_default_corpus_is_nonempty_with_ids(self):
        chunks = _default_chunks()
        self.assertGreater(len(chunks), 0)
        ids = [c["id"] for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))  # ids unique
        for c in chunks:
            self.assertTrue(c["id"])
            self.assertTrue(c["source"])
            self.assertTrue(c["text"].strip())

    def test_anchor_line_lives_in_exactly_one_chunk(self):
        chunks = _default_chunks()
        owners = [c for c in chunks if ANCHOR_LINE in c["text"]]
        self.assertEqual(len(owners), 1)


class TestJaccard(unittest.TestCase):
    def test_identical_sets_score_one(self):
        self.assertEqual(
            spec_rag.jaccard({"a", "b"}, {"a", "b"}), 1.0)

    def test_disjoint_sets_score_zero(self):
        self.assertEqual(
            spec_rag.jaccard({"a"}, {"b"}), 0.0)

    def test_empty_query_scores_zero(self):
        self.assertEqual(spec_rag.jaccard(set(), {"a"}), 0.0)


class TestRetrieve(unittest.TestCase):
    def test_anchor_query_returns_owning_chunk_first(self):
        chunks = _default_chunks()
        query = spec_rag.build_query(
            fault_file="picorv32.v", fault_line=1446,
            context_tokens=[ANCHOR_LINE])
        results = spec_rag.retrieve(query, chunks, top_k=3)
        self.assertGreater(len(results), 0)
        self.assertIn(ANCHOR_LINE, results[0]["text"])

    def test_unknown_query_returns_empty_never_invents(self):
        chunks = _default_chunks()
        results = spec_rag.retrieve(UNKNOWN_QUERY, chunks, top_k=3)
        self.assertEqual(results, [])

    def test_returned_ids_all_come_from_corpus(self):
        chunks = _default_chunks()
        corpus_ids = {c["id"] for c in chunks}
        query = spec_rag.build_query(
            fault_file="picorv32.v", fault_line=1450,
            context_tokens=["do_waitirq stall fetch TRAP"])
        for hit in spec_rag.retrieve(query, chunks, top_k=3):
            self.assertIn(hit["id"], corpus_ids)
            self.assertGreater(hit["score"], 0.0)

    def test_empty_query_and_empty_corpus_return_empty(self):
        chunks = _default_chunks()
        self.assertEqual(spec_rag.retrieve("", chunks), [])
        self.assertEqual(spec_rag.retrieve("decoder trigger", []), [])

    def test_build_query_carries_file_line_and_tokens(self):
        q = spec_rag.build_query(
            fault_file="picorv32.v", fault_line=1446,
            context_tokens=["decoder_trigger", "mem_do_rinst"])
        self.assertIn("picorv32.v", q)
        self.assertIn("1446", q)
        self.assertIn("decoder_trigger", q)
        self.assertIn("mem_do_rinst", q)


class TestPromptSection(unittest.TestCase):
    def test_hits_render_with_cited_chunk_ids(self):
        chunks = _default_chunks()
        query = spec_rag.build_query(
            fault_file="picorv32.v", fault_line=1446,
            context_tokens=[ANCHOR_LINE])
        results = spec_rag.retrieve(query, chunks, top_k=1)
        section = spec_rag.format_prompt_section(results)
        self.assertTrue(section)
        for hit in results:
            self.assertIn(hit["id"], section)

    def test_empty_results_render_as_empty_string(self):
        # No hits -> no section, never an invented citation.
        self.assertEqual(spec_rag.format_prompt_section([]), "")


class TestDefaultOff(unittest.TestCase):
    def test_pack_defaults_to_disabled_without_touching_disk(self):
        pack = spec_rag.retrieve_for_prompt(
            query_text="anything",
            repo_root="/nonexistent-root",
            chunks=None)
        self.assertFalse(pack["spec_enabled"])
        self.assertEqual(pack["spec_chunks"], [])
        self.assertEqual(pack["prompt_section"], "")

    def test_enabled_path_needs_query_loudly(self):
        with self.assertRaises(ValueError):
            spec_rag.retrieve_for_prompt(
                query_text="", repo_root=ROOT,
                enable_spec_rag=True)

    def test_enabled_path_retrieves_fixture_chunks(self):
        chunks = _default_chunks()
        pack = spec_rag.retrieve_for_prompt(
            query_text=spec_rag.build_query(
                fault_file="picorv32.v", fault_line=1446,
                context_tokens=[ANCHOR_LINE]),
            chunks=chunks, enable_spec_rag=True, top_k=1)
        self.assertTrue(pack["spec_enabled"])
        self.assertEqual(len(pack["spec_chunks"]), 1)
        self.assertIn(ANCHOR_LINE, pack["spec_chunks"][0]["text"])
        self.assertIn(pack["spec_chunks"][0]["id"],
                      pack["prompt_section"])

    def test_loop_untouched_by_evidence_wiring(self):
        # B2 is additive + default-off: loop.py must not import the
        # evidence package (existing loop behavior byte-identical).
        import re
        with open(os.path.join(ROOT, "agent", "loop.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIsNone(re.search(r"^\s*(import|from)\s+\S*evidence",
                                    src, re.M))

    def test_module_is_stdlib_only(self):
        # No sockets, no HTTP, no API clients: pure stdlib retrieval.
        import ast
        with open(os.path.join(ROOT, "agent", "evidence",
                               "spec_rag.py"),
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
