#!/usr/bin/env python3
"""Task H9 (persistent fix library, plan item 89) tests:
agent/repair/fixlib.py.

Persists the transfer.py fix index to disk with a version stamp;
a version-mismatched file refuses to load.

  save(index, path) -> None
  load(path) -> index

``index`` is a ``transfer.build_index`` result
(``{"entries": [{"signals": tuple, "patch": str}],
"by_signal": {signal: [entry indices]}}``).

Contract:
- Fixture: two stored fixes with disjoint signals; save then reload
  retrieves the same top fix (suggest parity before/after).
- Version stamp: the saved file carries the module version;
  a file with a mismatched version raises on load (ValueError).
- REFUSE (raise, never fabricate): bad index on save, blank path,
  missing file, corrupt JSON, missing version, wrong version.
- Tmp paths only: tests never write into the repo tree.
- Pure stdlib; no API, no keys. Default-off: explicit call only.

RED-first TDD: this file was written BEFORE agent/repair/fixlib.py
existed, so the first run must FAIL on the missing module (proving the
test exercises real code, not a stub).

Run: python3 tests/test_repair_fixlib.py -v   (from repo root)
"""
import ast
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import fixlib  # noqa: E402  (RED before implementation)
from repair import transfer  # noqa: E402


UART_DIFF = "\n".join([
    "diff --git a/uart.v b/uart.v",
    "--- a/uart.v",
    "+++ b/uart.v",
    "@@ -1,2 +1,2 @@",
    "-wire baud_tick = (baud_cnt == 16'd433);",
    "+wire baud_tick = (baud_cnt == 16'd434);",
]) + "\n"

FETCH_DIFF = "\n".join([
    "diff --git a/picorv32.v b/picorv32.v",
    "--- a/picorv32.v",
    "+++ b/picorv32.v",
    "@@ -1,2 +1,2 @@",
    "-wire fetch_ok = mem_valid && (pc_addr[1:0] == 2'b00);",
    "+wire fetch_ok = mem_valid && (pc_addr[1:0] != 2'b11);",
]) + "\n"

UART_FAULT = "tohost FAIL: baud_cnt stuck, baud_tick missing, tx_busy held"


def _index_two():
    return transfer.build_index([
        {"signals": ["baud_cnt", "baud_tick", "tx_busy"],
         "patch": UART_DIFF},
        {"signals": ["pc_addr", "mem_valid", "fetch_ok"],
         "patch": FETCH_DIFF},
    ])


def _tmp_path():
    fd, path = tempfile.mkstemp(prefix="fixlib-", suffix=".json")
    os.close(fd)
    os.unlink(path)
    return path


class TestSaveLoadRoundTrip(unittest.TestCase):
    def test_save_then_reload_retrieves_same_top_fix(self):
        idx = _index_two()
        path = _tmp_path()
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        before = transfer.suggest(UART_FAULT, idx)
        self.assertEqual(before, [UART_DIFF])
        fixlib.save(idx, path)
        reloaded = fixlib.load(path)
        after = transfer.suggest(UART_FAULT, reloaded)
        self.assertEqual(after, [UART_DIFF],
                         "reload must retrieve the same top fix")
        self.assertEqual(after, before)

    def test_saved_file_carries_version_stamp(self):
        idx = _index_two()
        path = _tmp_path()
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        fixlib.save(idx, path)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc.get("version"), fixlib.FIXLIB_VERSION)

    def test_reloaded_index_matches_original_entries(self):
        idx = _index_two()
        path = _tmp_path()
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        fixlib.save(idx, path)
        reloaded = fixlib.load(path)
        self.assertEqual(len(reloaded["entries"]), 2)
        self.assertEqual(reloaded["entries"][0]["patch"], UART_DIFF)
        self.assertEqual(reloaded["entries"][1]["patch"], FETCH_DIFF)
        self.assertEqual(list(reloaded["entries"][0]["signals"]),
                         ["baud_cnt", "baud_tick", "tx_busy"])


class TestVersionMismatch(unittest.TestCase):
    def test_version_mismatched_file_raises(self):
        idx = _index_two()
        path = _tmp_path()
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        fixlib.save(idx, path)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["version"] = fixlib.FIXLIB_VERSION + 999
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        with self.assertRaises(ValueError):
            fixlib.load(path)

    def test_missing_version_raises(self):
        path = _tmp_path()
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"index": {"entries": [], "by_signal": {}}}, fh)
        with self.assertRaises(ValueError):
            fixlib.load(path)


class TestRefusals(unittest.TestCase):
    def test_save_rejects_bad_index(self):
        path = _tmp_path()
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        for bad in (None, {}, {"entries": []}, [], "nope"):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    fixlib.save(bad, path)

    def test_save_rejects_blank_path(self):
        idx = _index_two()
        for bad in (None, "", "   ", 123):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    fixlib.save(idx, bad)

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            fixlib.load(os.path.join(
                tempfile.gettempdir(), "fixlib-no-such-file-xyz.json"))

    def test_load_corrupt_file_raises(self):
        path = _tmp_path()
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not json {{{")
        with self.assertRaises(ValueError):
            fixlib.load(path)

    def test_load_rejects_blank_path(self):
        for bad in (None, "", "   ", 123):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError, ValueError)):
                    fixlib.load(bad)


class TestStdlibOnly(unittest.TestCase):
    def test_module_is_stdlib_only(self):
        with open(os.path.join(ROOT, "agent", "repair", "fixlib.py"),
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
        allowed = {"json"}
        forbidden = imported - allowed
        self.assertEqual(forbidden, set(),
                         "fixlib may import only stdlib 'json', got: %s"
                         % sorted(imported))


if __name__ == "__main__":
    unittest.main()
