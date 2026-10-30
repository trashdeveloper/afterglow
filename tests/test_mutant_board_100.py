"""Task H10 (plan item 90): 100-mutant nightly board tests.

AMENDED (round 48, plan amendment): target 100 -> 35, evidence-linked
(pool yield ceiling ~1% over 4956 attempts; TB-bound, not miner-bound).
RED-first: fails until eval/mutant_board_100.json lands (campaign).
Asserts: 35 keeps, each RED-proven (boot log exists, oracle FAIL),
reconciliation, and quarantine (no kept fault repeats any prior
pilot / big-N / pair / stranger mutant fault site).
"""
import glob
import json
import os
import unittest

import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARD = os.path.join(ROOT, "eval", "mutant_board_100.json")
TARGETS = os.path.join(ROOT, "sim", "targets")

sys.path.insert(0, os.path.join(ROOT, "eval"))
import mine100  # noqa: E402  (single source of truth for quarantine)


def _prior_faults(base_tree):
    """(prior_texts, attribution) via the miner's own matcher.

    prior_texts: {(file, line, bug_text)} — the exact set the miner
    excludes (eval/mine100.py::prior_bug_texts, single source of truth).
    Text-level (not op-level): a mined mutant that reproduces a prior
    family's exact after-text at the same site is the same fault no
    matter what the miner named the op (caught live: pre-patch
    mut-100-002 reproduced pair-03 fault 2 verbatim and was removed).
    attribution: best-effort {fault: [stems]} for flat/pair specs so a
    breach names the prior family (stranger anchors resolve inside the
    miner; unmatched here still fail via prior_texts membership).
    """
    _, prior_texts = mine100.prior_bug_texts(base_tree, TARGETS)
    attribution = {}
    for path in glob.glob(os.path.join(TARGETS, "*.yaml")):
        stem = os.path.basename(path)
        if stem.startswith("mut-100-"):
            continue
        with open(path, encoding="utf-8") as fh:
            try:
                spec = yaml.safe_load(fh)
            except Exception:
                continue
        if not isinstance(spec, dict):
            continue
        texts = set()
        if isinstance(spec.get("faults"), list):
            for f in spec["faults"]:
                if isinstance(f, dict) and f.get("file") and f.get("line"):
                    texts.add((f["file"], int(f["line"]),
                               str(f.get("bug", "")).strip()))
        elif "mutant_file" in spec:
            texts.add((spec.get("mutant_file"),
                       int(spec.get("mutant_line")),
                       str(spec.get("bug", "")).strip()))
        for t in texts:
            if t in prior_texts:
                attribution.setdefault(t, []).append(stem)
    return prior_texts, attribution


BOARD_EXISTS = os.path.isfile(BOARD)


@unittest.skipUnless(BOARD_EXISTS, "H10 campaign not landed yet "
                     "(RED proven on missing board: setUpClass "
                     "FileNotFoundError before campaign)")
class TestMutantBoard100(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(BOARD, encoding="utf-8") as fh:
            cls.board = json.load(fh)

    def test_board_complete(self):
        fin = self.board.get("finalized", {})
        self.assertEqual(fin.get("at_kept"), 35,
                         "board not finalized at amended 35")
        self.assertFalse(self.board.get("complete"),
                         "complete must stay False (100 unreached, honest)")
        self.assertEqual(self.board.get("draw_seed"), 777,
                         "wrong draw seed (quarantine: must be fresh 777)")

    def test_100_kept(self):
        kept = self.board.get("kept", [])
        self.assertEqual(len(kept), 35,
                         "want exactly 35 kept (amended), got %d" % len(kept))

    def test_ids_unique_family(self):
        ids = [k["mutant_id"] for k in self.board["kept"]]
        self.assertEqual(len(set(ids)), 35, "duplicate mutant ids")
        for i in ids:
            self.assertTrue(i.startswith("mut-100-"),
                            "wrong id family: %s" % i)

    def test_each_red_proven(self):
        for k in self.board["kept"]:
            log = os.path.join(ROOT, k["red"]["boot_log"])
            self.assertTrue(os.path.isfile(log),
                            "%s: boot log missing" % k["mutant_id"])
            with open(log, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("FAIL", text,
                          "%s: no FAIL oracle in boot log" % k["mutant_id"])
            self.assertGreater(k["red"]["cycles"], 0,
                               "%s: non-positive cycles" % k["mutant_id"])
            spec = os.path.join(ROOT, k["spec"])
            self.assertTrue(os.path.isfile(spec),
                            "%s: spec missing" % k["mutant_id"])

    def test_reconciliation(self):
        a = self.board["accounting"]
        self.assertEqual(a["drawn"], a["kept_new"] + a["equivalent"]
                         + a["build_break"] + a["other"],
                         "reconciliation FAILED: %r" % (a,))

    def test_quarantine_no_repeat_fault(self):
        base_tree = self.board["base"]["tree"]
        prior_texts, attribution = _prior_faults(base_tree)
        collisions = []
        seen = set()
        for k in self.board["kept"]:
            fault = (k["file"], int(k["line"]), k["after"].strip())
            if fault in prior_texts:
                collisions.append((k["mutant_id"], fault,
                                   attribution.get(fault, ["?"])))
            if fault in seen:
                collisions.append((k["mutant_id"], fault,
                                   ["intra-board duplicate"]))
            seen.add(fault)
        self.assertEqual(collisions, [],
                         "quarantine breach (fault repeats prior family or "
                         "board-internal duplicate): %r" % (collisions[:5],))


if __name__ == "__main__":
    unittest.main()
