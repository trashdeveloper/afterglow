"""Evidence board tests (Task D5, plan item 35, Wave D).

`agent/trust/evboard.py` proves every claim in a report links to a
bundle artifact (checksums, logs): a report with an unlinked claim
fails validation.

RED-first TDD: this file was written BEFORE agent/trust/evboard.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 -m unittest tests.test_trust_evboard -v   (from repo root)
"""
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.trust import evboard  # noqa: E402  (RED before implementation)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOOT_LOG = "artifacts/boot.log"
TRACE = "artifacts/input_trace.txt"
PATCH = "patch.diff"
META = "meta.json"

REAL_SHA = hashlib.sha256(b"boot log bytes").hexdigest()


_MISSING = object()


def _linked(claim="uart divisor /16 fixed", artifacts=_MISSING, cid="c1"):
    if artifacts is _MISSING:
        artifacts = [BOOT_LOG]
    return {"id": cid, "claim": claim, "artifacts": artifacts}


def _report(*claims):
    return {"report_id": "r1", "claims": list(claims)}


class ValidationRejectsJunk(unittest.TestCase):
    def test_board_must_be_list_or_report_dict(self):
        for bad in (None, "board", 42, {"no": "claims"}, {"claims": "x"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evboard.validate_board(bad)

    def test_claim_must_be_dict_with_text(self):
        for bad in (None, "claim", 42, [], {"artifacts": [BOOT_LOG]},
                    {"claim": "", "artifacts": [BOOT_LOG]},
                    {"claim": "   ", "artifacts": [BOOT_LOG]},
                    {"claim": 123, "artifacts": [BOOT_LOG]},
                    {"claim": True, "artifacts": [BOOT_LOG]}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evboard.validate_board([bad])

    def test_bad_artifacts_shape_raises(self):
        for bad_art in (None, "not-a-list", 42, {},
                        ["", "   "], [None], [123], [True], [b"x"]):
            with self.subTest(bad_art=bad_art):
                with self.assertRaises(ValueError):
                    evboard.validate_board(
                        [_linked(artifacts=bad_art)])

    def test_absolute_and_traversal_paths_rejected(self):
        for bad in ("/etc/passwd", "../secret", "a/../../b", "artifacts/../x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evboard.validate_board([_linked(artifacts=[bad])])

    def test_unknown_artifact_rejected(self):
        with self.assertRaises(ValueError):
            evboard.validate_board([_linked(artifacts=["random.txt"])])

    def test_duplicate_claim_ids_rejected(self):
        with self.assertRaises(ValueError):
            evboard.validate_board(
                [_linked(cid="dup"), _linked(cid="dup")])

    def test_bad_claim_id_rejected(self):
        with self.assertRaises(ValueError):
            evboard.validate_board(
                [_linked(cid="   ", artifacts=[BOOT_LOG])])

    def test_bad_checksum_dict_rejected(self):
        for bad in ({"path": BOOT_LOG},  # missing sha256
                    {"sha256": REAL_SHA},  # missing path
                    {"path": BOOT_LOG, "sha256": "xyz"},
                    {"path": BOOT_LOG, "sha256": "0" * 64},  # placeholder
                    {"path": BOOT_LOG,
                     "sha256": REAL_SHA.upper()},  # must be lower hex
                    {"path": "random.txt", "sha256": REAL_SHA}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evboard.validate_board([_linked(artifacts=[bad])])

    def test_validate_report_requires_claims_key(self):
        for bad in (None, [], {}, {"report_id": "r1"}, {"claims": {}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evboard.validate_report(bad)


class UnlinkedClaimFails(unittest.TestCase):
    """Core guarantee: a report with an unlinked claim fails validation."""

    def test_empty_artifacts_list_fails(self):
        with self.assertRaises(ValueError) as ctx:
            evboard.validate_board([_linked(artifacts=[])])
        self.assertIn("c1", str(ctx.exception))

    def test_missing_artifacts_key_fails(self):
        with self.assertRaises(ValueError):
            evboard.validate_board(
                [{"id": "c1", "claim": "green without proof"}])

    def test_report_dict_with_unlinked_claim_fails(self):
        report = _report(_linked(cid="c1"),
                         {"id": "c2", "claim": "divisor halved boot",
                          "artifacts": []})
        with self.assertRaises(ValueError) as ctx:
            evboard.validate_board(report)
        self.assertIn("c2", str(ctx.exception))
        with self.assertRaises(ValueError):
            evboard.validate_report(report)

    def test_verify_board_flags_unlinked_without_raising(self):
        report = _report(_linked(cid="c1"),
                         {"id": "c2", "claim": "no evidence here",
                          "artifacts": []})
        res = evboard.verify_board(report)
        self.assertFalse(res["ok"], res)
        self.assertIn("c2", res["unlinked"])
        self.assertIn("INVALID", res["reason"])

    def test_all_unlinked_report_verifies_not_ok(self):
        res = evboard.verify_board(
            _report({"id": "c9", "claim": "trust me", "artifacts": []}))
        self.assertFalse(res["ok"])
        self.assertEqual(res["unlinked"], ["c9"])


class LinkedClaimsPass(unittest.TestCase):
    def test_single_linked_claim_validates(self):
        ids = evboard.validate_board([_linked()])
        self.assertEqual(ids, ["c1"])

    def test_multiple_linked_claims_validate(self):
        board = [_linked(cid="c1", artifacts=[BOOT_LOG]),
                 _linked(cid="c2", artifacts=[TRACE, PATCH]),
                 _linked(cid="c3", artifacts=[META])]
        self.assertEqual(evboard.validate_board(board), ["c1", "c2", "c3"])

    def test_report_dict_validates(self):
        report = _report(_linked(cid="c1"), _linked(cid="c2"))
        self.assertEqual(evboard.validate_report(report), ["c1", "c2"])
        self.assertEqual(evboard.validate_board(report), ["c1", "c2"])

    def test_checksum_dict_artifact_links(self):
        entry = _linked(artifacts=[{"path": BOOT_LOG, "sha256": REAL_SHA}])
        self.assertEqual(evboard.validate_board([entry]), ["c1"])

    def test_mixed_string_and_dict_artifacts(self):
        entry = _linked(artifacts=[TRACE, {"path": PATCH,
                                           "sha256": REAL_SHA}])
        self.assertEqual(evboard.validate_board([entry]), ["c1"])

    def test_empty_board_validates_vacuous(self):
        self.assertEqual(evboard.validate_board([]), [])

    def test_claim_without_id_gets_position_id(self):
        ids = evboard.validate_board(
            [{"claim": "boot passes", "artifacts": [BOOT_LOG]}])
        self.assertEqual(len(ids), 1)

    def test_verify_linked_board_is_ok(self):
        res = evboard.verify_board(
            _report(_linked(cid="c1"), _linked(cid="c2")))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["n"], 2)
        self.assertEqual(res["unlinked"], [])
        self.assertIn("OK", res["reason"])

    def test_verify_empty_board_is_ok(self):
        res = evboard.verify_board([])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["n"], 0)

    def test_unicode_claim_round_trips(self):
        entry = _linked(claim="café Überlauf boot 🔥")
        self.assertEqual(evboard.validate_board([entry]), ["c1"])


class EvidenceBoardClass(unittest.TestCase):
    def test_add_validate_verify_counts(self):
        board = evboard.EvidenceBoard()
        self.assertEqual(len(board), 0)
        board.add("uart divisor fixed", [BOOT_LOG], claim_id="c1")
        board.add("checksum matches", [META], claim_id="c2")
        self.assertEqual(len(board), 2)
        self.assertEqual(board.validate(), ["c1", "c2"])
        res = board.verify()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["n"], 2)

    def test_class_rejects_unlinked_like_function(self):
        board = evboard.EvidenceBoard()
        with self.assertRaises(ValueError):
            board.add("no proof", [])
        with self.assertRaises(ValueError):
            board.add("   ", [BOOT_LOG])
        with self.assertRaises(ValueError):
            board.add("x", ["random.txt"])
        self.assertEqual(len(board), 0)

    def test_auto_id_when_omitted(self):
        board = evboard.EvidenceBoard()
        entry = board.add("boot passes", [BOOT_LOG])
        self.assertTrue(entry["id"])
        self.assertEqual(board.validate(), [entry["id"]])

    def test_duplicate_id_rejected(self):
        board = evboard.EvidenceBoard()
        board.add("first", [BOOT_LOG], claim_id="dup")
        with self.assertRaises(ValueError):
            board.add("second", [TRACE], claim_id="dup")


class JsonRoundTrip(unittest.TestCase):
    def test_to_from_json_validates_identical(self):
        board = [_linked(cid="c1"), _linked(cid="c2", artifacts=[TRACE])]
        text = evboard.board_to_json(board)
        parsed = json.loads(text)
        self.assertIsInstance(parsed, list)
        back = evboard.board_from_json(text)
        self.assertEqual(back, board)
        self.assertEqual(evboard.validate_board(back), ["c1", "c2"])

    def test_from_json_rejects_garbage(self):
        for bad in ("not json", '{"a": 1}', "[1, 2]", '"str"', "42", "",
                    json.dumps([_linked(artifacts=[])])):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evboard.board_from_json(bad)

    def test_to_json_rejects_non_board(self):
        with self.assertRaises(ValueError):
            evboard.board_to_json({"not": "a board"})

    def test_class_to_from_json_methods(self):
        board = evboard.EvidenceBoard()
        board.add("uart fixed", [BOOT_LOG], claim_id="c1")
        back = evboard.EvidenceBoard.from_json(board.to_json())
        self.assertEqual(back.validate(), ["c1"])

    def test_save_then_load_validates_identical(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="trust-evboard-")
        self.addCleanup(shutil.rmtree, tmp, True)
        board = evboard.EvidenceBoard()
        board.add("uart fixed", [BOOT_LOG], claim_id="c1")
        board.add("trace hashed", [TRACE], claim_id="c2")
        path = os.path.join(tmp, "evboard.json")
        board.save(path)
        loaded = evboard.EvidenceBoard.load(path)
        self.assertEqual(loaded.validate(), ["c1", "c2"])
        self.assertTrue(loaded.verify()["ok"])

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            evboard.EvidenceBoard.load("/nonexistent-dir-xyz/evboard.json")


class ContractUntouchedAndStdlib(unittest.TestCase):
    def test_loop_guard_score_ci_untouched(self):
        for name in ("agent/loop.py", "agent/guard.py",
                     "eval/score.py", "scripts/ci.sh"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "evboard", text,
                "Wave D default-off: %s must not wire this in" % name)

    def test_stdlib_only(self):
        src = os.path.join(ROOT, "agent", "trust", "evboard.py")
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        imports = [ln.strip() for ln in text.splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        for ln in imports:
            self.assertRegex(
                ln, r"^(import |from )(__future__|hashlib|json|os)",
                "evboard.py must stay stdlib-only: %r" % ln)
        lowered = text.lower()
        for token in ("urllib", "socket", "http", "bearer", "api_key",
                      "apikey", "subprocess"):
            self.assertNotIn(token, lowered,
                             "evboard.py must do no I/O or network: %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
