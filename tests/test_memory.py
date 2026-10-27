"""Context-pack v2: fix-memory stub tests (fixtures, no network).

Run: python3 tests/test_memory.py -v   (from repo root)

Read-only retrieval over runs/*/fingerprint.json + a write helper the
loop will call later (wiring is a later task; loop.py untouched here).

- test_jaccard_rank: 3 indexed greens, query closest to #2 -> #2 first.
- test_missing_file_skipped: bundles without fingerprint.json are
  skipped, never crash, never written.
- test_write_helper: write_fingerprint writes a valid fingerprint.json
  into a NEW dir only; old bundles untouched (read-only rule).
- test_signature_index: fingerprint -> green run-ids mapping honors
  status green + diagnosed only.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afterglow_core import memory


def _bundle(runs, name, record=None, status="green", diagnosed=True):
    d = os.path.join(runs, name)
    os.makedirs(d, exist_ok=True)
    meta = {"run_id": name, "status": status, "diagnosed": diagnosed}
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    if record is not None:
        with open(os.path.join(d, "fingerprint.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(record, fh)
    return d


def _rec(fphex, signals):
    return {"fingerprint": fphex, "trap_line": "TRAP: x",
            "oracle_sig": "FAIL 0xdead", "signals": list(signals)}


class Jaccard(unittest.TestCase):
    def test_jaccard_values(self):
        self.assertAlmostEqual(memory.jaccard({"a", "b"}, {"a", "b"}), 1.0)
        self.assertAlmostEqual(memory.jaccard({"a"}, {"b"}), 0.0)
        self.assertAlmostEqual(memory.jaccard({"a", "b"}, {"b", "c"}), 1.0 / 3.0)
        self.assertAlmostEqual(memory.jaccard(set(), set()), 1.0)
        self.assertAlmostEqual(memory.jaccard(set(), {"a"}), 0.0)

    def test_jaccard_rank(self):
        with tempfile.TemporaryDirectory() as runs:
            _bundle(runs, "run-01", _rec("aa" * 32, ["pc", "mem_valid"]))
            _bundle(runs, "run-02", _rec("bb" * 32, ["recv_divcnt", "rdata"]))
            _bundle(runs, "run-03", _rec("cc" * 32, ["trap", "clk"]))
            ranked = memory.recall_similar(["recv_divcnt", "rdata", "baud"],
                                           runs, k=3)
            self.assertEqual(ranked[0][0], "run-02")
            self.assertGreater(ranked[0][1], ranked[1][1])
            self.assertLessEqual(len(ranked), 3)

    def test_missing_file_skipped(self):
        with tempfile.TemporaryDirectory() as runs:
            _bundle(runs, "no-fp")  # meta only, no fingerprint.json
            _bundle(runs, "has-fp", _rec("dd" * 32, ["rdata"]))
            before = sorted(os.listdir(os.path.join(runs, "no-fp")))
            ranked = memory.recall_similar(["rdata"], runs, k=5)
            self.assertEqual([r[0] for r in ranked], ["has-fp"])
            after = sorted(os.listdir(os.path.join(runs, "no-fp")))
            self.assertEqual(before, after)  # read-only: nothing written

    def test_write_helper(self):
        with tempfile.TemporaryDirectory() as runs:
            old = _bundle(runs, "old-green", _rec("ee" * 32, ["a"]))
            with open(os.path.join(old, "fingerprint.json"),
                      encoding="utf-8") as fh:
                old_before = fh.read()
            new_dir = os.path.join(runs, "new-run")
            os.makedirs(new_dir, exist_ok=True)
            rec = _rec("ff" * 32, ["recv_divcnt", "rdata"])
            rec["trap_line"] = "TRAP: uart data mismatch"
            out = memory.write_fingerprint(new_dir, rec)
            self.assertTrue(os.path.isfile(out))
            with open(out, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["fingerprint"], "ff" * 32)
            self.assertIn("signals", saved)
            with open(os.path.join(old, "fingerprint.json"),
                      encoding="utf-8") as fh:
                self.assertEqual(fh.read(), old_before)  # old untouched

    def test_signature_index(self):
        with tempfile.TemporaryDirectory() as runs:
            _bundle(runs, "g1", _rec("11" * 32, ["a"]), status="green")
            _bundle(runs, "r1", _rec("11" * 32, ["a"]), status="red")
            _bundle(runs, "g2", _rec("22" * 32, ["b"]), status="green",
                    diagnosed=False)
            idx = memory.signature_index(runs)
            self.assertIn("11" * 32, idx)
            self.assertEqual(idx["11" * 32], ["g1"])
            self.assertNotIn("22" * 32, idx)


if __name__ == "__main__":
    unittest.main()
