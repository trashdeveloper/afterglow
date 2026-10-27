"""RTL-aware seeding unit tests (wave-4, no API, no sim).

Seeding fix: trace tokens (max_addr/progress/asserted) never intersect
RTL signal names, so the slicer abstained. The fix filters trace tokens
through the RTL vocab (driven names via parse_drivers) and prefers
spec-anchor signals (mutant-line context known via the spec).

- vocab filter drops progress/asserted but keeps mem_done
- spec-anchor seed preferred over trace tokens
- abstain only when nothing intersects (empty candidates -> empty cone)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import slicer


class RtlVocabFilter(unittest.TestCase):
    def test_vocab_filter_drops_progress_asserted_keeps_mem_done(self):
        tree = slicer.resolve_pinned_tree("picorv32")
        vocab = slicer.rtl_signal_vocab(tree, ["picorv32.v"])
        self.assertIn("mem_done", {v.lower() for v in vocab})
        kept, dropped = slicer.filter_tokens_to_rtl(
            ["max_addr", "progress", "asserted", "mem_done"], vocab)
        self.assertIn("mem_done", [k.lower() for k in kept])
        self.assertNotIn("progress", [k.lower() for k in kept])
        self.assertNotIn("asserted", [k.lower() for k in kept])
        self.assertNotIn("max_addr", [k.lower() for k in kept])
        self.assertEqual(dropped, 3)

    def test_spec_anchor_seed_preferred(self):
        tree = slicer.resolve_pinned_tree("picorv32")
        vocab = slicer.rtl_signal_vocab(tree, ["picorv32.v"])
        trace_sigs = ["max_addr", "progress"]
        anchor_sigs = slicer.spec_anchor_signals(
            "wire mem_done = resetn && ((mem_xfer && |mem_state)) &&")
        # Anchor carries the RTL signal; trace tokens do not.
        self.assertIn("mem_done", [a.lower() for a in anchor_sigs])
        ranked = slicer.rank_seed_candidates(trace_sigs, anchor_sigs, vocab)
        self.assertTrue(ranked, "no candidates ranked")
        seed, source = ranked[0]
        self.assertEqual(seed.lower(), "mem_done")
        self.assertEqual(source, "spec-anchor")

    def test_abstain_only_when_empty(self):
        tree = slicer.resolve_pinned_tree("picorv32")
        vocab = slicer.rtl_signal_vocab(tree, ["picorv32.v"])
        # Nothing intersects: trivial anchor + non-RTL trace -> raw
        # fallback only; empty inputs -> no candidates (abstain).
        ranked = slicer.rank_seed_candidates(
            ["max_addr"], slicer.spec_anchor_signals("0: begin"), vocab)
        self.assertTrue(ranked, "expected raw-fallback, not abstain")
        self.assertEqual(ranked[0][1], "raw-fallback")
        self.assertEqual(
            slicer.rank_seed_candidates([], [], vocab), [])
        # Empty cone degrades to abstention (never a guess).
        lines, _rep = slicer.backward_slice(
            tree, "nosuchsignal_xyz_abc", ["picorv32.v"])
        self.assertEqual(lines, [])
        self.assertEqual(
            slicer.top1_localization(lines, "TRAP: x\n", ["x"]), "")


if __name__ == "__main__":
    unittest.main()
