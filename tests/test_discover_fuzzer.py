"""Task A1 (Input fuzzer) red-first tests: agent/discover/fuzzer.py.

- StimulusCorpus(seed) reproduces identically for the same seed.
- run_corpus(tree, case, corpus) NEVER invents a verdict: empty corpus
  raises loudly, and any sim/build error propagates (never a fake RED).
- Verdicts come from the oracle marker in sim output, never exit codes.

Run: python3 tests/test_discover_fuzzer.py -v   (from repo root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import fuzzer  # noqa: E402  (RED before implementation: ModuleNotFoundError)


def _tiny_case():
    return {
        "name": "fuzz-tiny", "project": "picorv32",
        "files": ["picorv32.v"],
        "top": "tb_picorv32_fetch", "tb": "picorv32-fetch_tb.sv",
        "oracle_pass": "tohost: PASS",
    }


class TestStimulusCorpus(unittest.TestCase):
    def test_same_seed_reproduces_identical_corpus(self):
        a = fuzzer.StimulusCorpus(1234)
        b = fuzzer.StimulusCorpus(1234)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 200)
        for stim in a:
            self.assertIsInstance(stim, bytes)
            self.assertTrue(len(stim) > 0)

    def test_different_seeds_differ(self):
        a = fuzzer.StimulusCorpus(1234)
        b = fuzzer.StimulusCorpus(5678)
        self.assertNotEqual(a, b)

    def test_structured_edge_vectors_present(self):
        corpus = fuzzer.StimulusCorpus(42)
        self.assertIn(b"\x00" * 16, corpus)
        self.assertIn(b"\xff" * 16, corpus)

    def test_custom_size_respected(self):
        self.assertEqual(len(fuzzer.StimulusCorpus(7, n=10)), 10)


class TestRunCorpus(unittest.TestCase):
    def test_empty_corpus_raises_never_fakes_verdict(self):
        with self.assertRaises(ValueError):
            fuzzer.run_corpus("/nonexistent/tree", _tiny_case(), [])

    def test_error_path_raises_never_fakes_red(self):
        corpus = fuzzer.StimulusCorpus(99, n=3)

        def _boom(binary, seed, stimulus):
            raise RuntimeError("sim transport died")

        with self.assertRaises(RuntimeError):
            fuzzer.run_corpus("/fake/tree", _tiny_case(), corpus,
                              build_fn=lambda t, c: "/fake/binary",
                              run_fn=_boom)

    def test_build_error_raises_never_fakes_red(self):
        corpus = fuzzer.StimulusCorpus(99, n=3)

        def _bad_build(tree, case):
            raise RuntimeError("verilator build failed")

        with self.assertRaises(RuntimeError):
            fuzzer.run_corpus("/fake/tree", _tiny_case(), corpus,
                              build_fn=_bad_build)

    def test_verdicts_come_from_oracle_marker(self):
        corpus = fuzzer.StimulusCorpus(11, n=4)
        calls = []

        def _fake_run(binary, seed, stimulus):
            calls.append((binary, seed, stimulus))
            if len(calls) % 2 == 0:
                return "tohost: PASS\nCYCLES=2000\n"
            return "tohost: FAIL 0xdead\nTRAP: fetch misalign\n"

        verdicts = fuzzer.run_corpus("/fake/tree", _tiny_case(), corpus,
                                     build_fn=lambda t, c: "/fake/binary",
                                     run_fn=_fake_run)
        self.assertEqual(len(verdicts), 4)
        self.assertEqual([getattr(v, "pass") for v in verdicts],
                         [False, True, False, True])
        self.assertEqual(len(calls), 4)
        # Every verdict carries its stimulus provenance.
        for v, stim in zip(verdicts, corpus):
            self.assertIn("stimulus_hex", v.evidence)
            self.assertEqual(v.evidence["stimulus_hex"], stim.hex())


if __name__ == "__main__":
    unittest.main()
