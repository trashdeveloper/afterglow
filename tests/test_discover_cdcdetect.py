"""Task E6 (CDC detector) red-first tests.

- classify_crossing(driver_clock, load_clock, has_sync)
  -> "sync" | "cdc-safe" | "cdc-risk".
- find_crossings(crossings) -> [risky...]: flags only unsynchronized
  cross-clock crossings (fixture pair: unsynchronized found,
  synchronized not flagged).
- check_sync(crossing) -> bool: True unless the crossing is cdc-risk.

Fixture pair: one direct cross-clock signal (clk_a -> clk_b, no
synchronizer -> RISK, found) and one synchronized cross-clock signal
(clk_a -> clk_b through a 2-flop sync -> SAFE, not flagged).

Run: python3 tests/test_discover_cdcdetect.py -v   (from repo root)
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import cdcdetect  # noqa: E402  (RED before implementation: ModuleNotFoundError)


# ---------------------------------------------------------------------------
# Fixture pair: same clock-domain pair, sync bit differs.
# ---------------------------------------------------------------------------
UNSYNC = {"signal": "req_a2b", "driver_clock": "clk_a",
          "load_clock": "clk_b", "has_sync": False}
SYNCED = {"signal": "ack_b2a", "driver_clock": "clk_a",
          "load_clock": "clk_b", "has_sync": True}
SAMECLK = {"signal": "datapath", "driver_clock": "clk_a",
           "load_clock": "clk_a", "has_sync": False}


class TestClassifyCrossing(unittest.TestCase):
    def test_same_clock_is_sync(self):
        self.assertEqual(
            cdcdetect.classify_crossing("clk_a", "clk_a", False), "sync")
        self.assertEqual(
            cdcdetect.classify_crossing("clk_a", "clk_a", True), "sync")

    def test_synchronized_crossing_is_cdc_safe(self):
        self.assertEqual(
            cdcdetect.classify_crossing("clk_a", "clk_b", True), "cdc-safe")

    def test_direct_cross_clock_is_cdc_risk(self):
        self.assertEqual(
            cdcdetect.classify_crossing("clk_a", "clk_b", False), "cdc-risk")

    def test_classify_dict_fixture_pair(self):
        self.assertEqual(
            cdcdetect.classify_crossing(**{k: SYNCED[k] for k in
                                           ("driver_clock", "load_clock",
                                            "has_sync")}),
            "cdc-safe")
        self.assertEqual(
            cdcdetect.classify_crossing(**{k: UNSYNC[k] for k in
                                           ("driver_clock", "load_clock",
                                            "has_sync")}),
            "cdc-risk")

    def test_classify_rejects_bad_clocks(self):
        with self.assertRaises((TypeError, ValueError)):
            cdcdetect.classify_crossing("", "clk_b", False)
        with self.assertRaises((TypeError, ValueError)):
            cdcdetect.classify_crossing(None, "clk_b", False)
        with self.assertRaises((TypeError, ValueError)):
            cdcdetect.classify_crossing("clk_a", None, False)


class TestFindCrossings(unittest.TestCase):
    def test_unsynchronized_crossing_found(self):
        found = cdcdetect.find_crossings([dict(UNSYNC)])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["signal"], "req_a2b")

    def test_synchronized_crossing_not_flagged(self):
        found = cdcdetect.find_crossings([dict(SYNCED)])
        self.assertEqual(found, [])

    def test_fixture_pair_flags_exactly_the_unsync(self):
        found = cdcdetect.find_crossings(
            [dict(UNSYNC), dict(SYNCED), dict(SAMECLK)])
        self.assertEqual([c["signal"] for c in found], ["req_a2b"])

    def test_empty_portfolio_reports_empty(self):
        self.assertEqual(cdcdetect.find_crossings([]), [])

    def test_find_does_not_mutate_input(self):
        inbox = [dict(UNSYNC), dict(SYNCED)]
        snapshot = [dict(c) for c in inbox]
        cdcdetect.find_crossings(inbox)
        self.assertEqual(inbox, snapshot)

    def test_find_rejects_bad_entries(self):
        with self.assertRaises((TypeError, ValueError, KeyError)):
            cdcdetect.find_crossings("not-a-list")
        with self.assertRaises((TypeError, ValueError, KeyError)):
            cdcdetect.find_crossings([{"signal": "x"}])


class TestCheckSync(unittest.TestCase):
    def test_unsync_crossing_fails_check(self):
        self.assertFalse(cdcdetect.check_sync(dict(UNSYNC)))

    def test_synced_crossing_passes_check(self):
        self.assertTrue(cdcdetect.check_sync(dict(SYNCED)))

    def test_same_clock_passes_check(self):
        self.assertTrue(cdcdetect.check_sync(dict(SAMECLK)))


class TestStandardGates(unittest.TestCase):
    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                "cdcdetect", text,
                "%s must not reference the cdcdetect module "
                "(default-off: no loop/guard wiring)" % sacred)

    def test_tmp_only(self):
        # cdcdetect is in-memory only; it must never write files.
        with open(os.path.join(ROOT, "agent", "discover",
                               "cdcdetect.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        writes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in ("write_text", "dump", "dumpf", "copyfile",
                            "copy", "move", "rename", "makedirs", "mkdir"):
                    writes.add(name)
                if name == "open":
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value,
                                                           ast.Constant):
                            if any(c in str(kw.value.value) for c in "wax+"):
                                writes.add("open(%r)" % kw.value.value)
                    if (len(node.args) >= 2
                            and isinstance(node.args[1], ast.Constant)
                            and any(c in str(node.args[1].value)
                                    for c in "wax+")):
                        writes.add("open(%r)" % node.args[1].value)
        self.assertEqual(writes, set(),
                         "cdcdetect must not write files (tmp-only "
                         "gate): %r" % (writes,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                               "cdcdetect.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint({"requests", "urllib"}),
                        "no live HTTP imports allowed ($0 gate): %r"
                        % (imported,))
        for token in ("MODEL_API_KEY", "urllib", "requests"):
            self.assertNotIn(token, src,
                             "no live-API usage allowed ($0 gate): %r"
                             % token)


if __name__ == "__main__":
    unittest.main()
