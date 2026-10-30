"""Task E7 (Metastability injector) red-first tests.

- inject(pristine_tree, crossing, workdir=None) -> scratch path:
  forces X on an UNSYNCHRONIZED crossing in a COPY of the tree
  (shutil.copytree to tmp), never in place. Refuses synchronized
  crossings (has_sync) and same-clock pairs loudly.
- detect(log, observables) -> [evidence]: X reaching an observable
  signal line (evidence cites lineno, line, signal).

Fixture pair: one direct cross-clock signal (clk_a -> clk_b, no
synchronizer -> inject takes, detect finds X on the observable) and
one synchronized cross-clock signal (clk_a -> clk_b through a 2-flop
sync -> inject refuses, and a clean trace detects nothing).

CRITICAL: inject must copy the tree first, never modify the input
tree -- asserted by test_input_tree_untouched.

Run: python3 tests/test_discover_metainject.py -v   (from repo root)
"""
import ast
import filecmp
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

from discover import metainject  # noqa: E402  (RED before implementation: ModuleNotFoundError)


# ---------------------------------------------------------------------------
# Fixture pair: same clock-domain pair, sync bit differs.
# ---------------------------------------------------------------------------
UNSYNC = {"signal": "req_a2b", "driver_clock": "clk_a",
          "load_clock": "clk_b", "has_sync": False}
SYNCED = {"signal": "ack_b2a", "driver_clock": "clk_a",
          "load_clock": "clk_b", "has_sync": True}
SAMECLK = {"signal": "datapath", "driver_clock": "clk_a",
           "load_clock": "clk_a", "has_sync": False}

OBSERVABLES = ["out_b", "tohost"]

# Canned fixture traces (no Verilator build: static-trace proof).
# Unsync run: X forced on req_a2b propagates to observable out_b.
UNSYNC_TRACE = "\n".join([
    "CYCLES=10 req_a2b=0x00000001 out_b=0x00000001",
    "CYCLES=11 req_a2b=0xxxxxxx out_b=0xxxxxxx",
    "CYCLES=12 TRAP: none tohost=0x00000000",
    "",
])
# Synced run: synchronizer blocks the X; observables stay clean.
SYNCED_TRACE = "\n".join([
    "CYCLES=10 ack_b2a=0x00000001 out_b=0x00000001",
    "CYCLES=11 ack_b2a=0x00000001 out_b=0x00000001",
    "CYCLES=12 TRAP: none tohost=0x00000000",
    "",
])


def make_fixture_tree(parent):
    """Build a minimal fake RTL tree; returns its path."""
    tree = os.path.join(parent, "pristine")
    os.makedirs(tree)
    with open(os.path.join(tree, "top.v"), "w",
              encoding="utf-8") as fh:
        fh.write("module top(input clk_a, input clk_b, output out_b);\n"
                 "  assign out_b = req_a2b;\n"
                 "endmodule\n")
    with open(os.path.join(tree, "tb.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("fixture stimulus\n")
    return tree


def snapshot_tree(tree):
    """Map relpath -> bytes for every file under tree."""
    snap = {}
    for dirpath, _dirnames, filenames in os.walk(tree):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, tree)
            with open(full, "rb") as fh:
                snap[rel] = fh.read()
    return snap


def fake_run_fn(scratch, crossing, seed=42):
    """Fixture stand-in for a sim run: canned trace per crossing."""
    del seed  # determinism pinned; seed unused by the fixture
    assert os.path.isdir(scratch), "run needs the injected scratch tree"
    if crossing.get("has_sync"):
        return SYNCED_TRACE
    return UNSYNC_TRACE


class TestInjectDetectUnsync(unittest.TestCase):
    def test_unsync_inject_then_detect_finds_x(self):
        tmp = tempfile.mkdtemp(prefix="metainject-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = make_fixture_tree(tmp)
        scratch = metainject.inject(tree, dict(UNSYNC), workdir=tmp)
        self.assertTrue(os.path.isdir(scratch))
        self.assertNotEqual(os.path.abspath(scratch),
                            os.path.abspath(tree))
        log = fake_run_fn(scratch, UNSYNC)
        evidence = metainject.detect(log, observables=list(OBSERVABLES))
        self.assertTrue(evidence,
                        "X forced on the unsync crossing must reach "
                        "an observable")
        self.assertTrue(any(ev["signal"] == "out_b" for ev in evidence),
                        "evidence must cite the observable out_b: %r"
                        % (evidence,))
        lineno = [ev["lineno"] for ev in evidence
                  if ev["signal"] == "out_b"][0]
        self.assertEqual(lineno, 2)

    def test_detect_evidence_shape(self):
        evidence = metainject.detect(UNSYNC_TRACE,
                                     observables=list(OBSERVABLES))
        self.assertEqual(len(evidence), 1)
        ev = evidence[0]
        self.assertEqual(ev["lineno"], 2)
        self.assertEqual(ev["signal"], "out_b")
        self.assertIn("0xxxxxxx", ev["line"])


class TestSyncedCrossing(unittest.TestCase):
    def test_synced_inject_refuses_or_detect_finds_nothing(self):
        tmp = tempfile.mkdtemp(prefix="metainject-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = make_fixture_tree(tmp)
        try:
            scratch = metainject.inject(tree, dict(SYNCED), workdir=tmp)
        except (RuntimeError, ValueError):
            return  # inject refuses the synchronized crossing: RED
        log = fake_run_fn(scratch, SYNCED)
        evidence = metainject.detect(log, observables=list(OBSERVABLES))
        self.assertEqual(evidence, [],
                         "synchronized crossing must show no X at any "
                         "observable: %r" % (evidence,))

    def test_synced_inject_refuses_loudly(self):
        tmp = tempfile.mkdtemp(prefix="metainject-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = make_fixture_tree(tmp)
        with self.assertRaises((RuntimeError, ValueError)):
            metainject.inject(tree, dict(SYNCED), workdir=tmp)

    def test_same_clock_is_not_a_crossing(self):
        tmp = tempfile.mkdtemp(prefix="metainject-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = make_fixture_tree(tmp)
        with self.assertRaises((TypeError, ValueError, KeyError)):
            metainject.inject(tree, dict(SAMECLK), workdir=tmp)

    def test_clean_trace_detects_nothing(self):
        self.assertEqual(
            metainject.detect(SYNCED_TRACE,
                              observables=list(OBSERVABLES)), [])


class TestCopyFirst(unittest.TestCase):
    def test_input_tree_untouched(self):
        tmp = tempfile.mkdtemp(prefix="metainject-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = make_fixture_tree(tmp)
        before = snapshot_tree(tree)
        scratch = metainject.inject(tree, dict(UNSYNC), workdir=tmp)
        after = snapshot_tree(tree)
        self.assertEqual(before, after,
                         "inject must never modify the input tree")
        # The injection marker lives ONLY in the copy.
        pristine_markers = [
            rel for rel in after if "metainject" in rel.lower()]
        self.assertEqual(pristine_markers, [],
                         "no injection marker may appear in the input "
                         "tree: %r" % (pristine_markers,))
        scratch_files = []
        for dirpath, _d, fns in os.walk(scratch):
            scratch_files.extend(fns)
        self.assertTrue(
            any("metainject" in n.lower() for n in scratch_files),
            "the COPY must carry the X-injection marker: %r"
            % (scratch_files,))

    def test_scratch_is_a_real_copy(self):
        tmp = tempfile.mkdtemp(prefix="metainject-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = make_fixture_tree(tmp)
        scratch = metainject.inject(tree, dict(UNSYNC), workdir=tmp)
        for rel in ("top.v", "tb.txt"):
            self.assertTrue(
                filecmp.cmp(os.path.join(tree, rel),
                            os.path.join(scratch, rel),
                            shallow=False),
                "scratch must start as a byte-identical copy: %s" % rel)

    def test_inject_rejects_bad_crossings(self):
        tmp = tempfile.mkdtemp(prefix="metainject-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = make_fixture_tree(tmp)
        with self.assertRaises((TypeError, ValueError, KeyError)):
            metainject.inject(tree, "not-a-dict", workdir=tmp)
        with self.assertRaises((TypeError, ValueError, KeyError)):
            metainject.inject(tree, {"signal": "x"}, workdir=tmp)
        with self.assertRaises((TypeError, ValueError, KeyError)):
            metainject.inject(tree, {"signal": "  ",
                                     "driver_clock": "clk_a",
                                     "load_clock": "clk_b"},
                              workdir=tmp)
        with self.assertRaises((TypeError, ValueError, KeyError,
                                FileNotFoundError)):
            metainject.inject(os.path.join(tmp, "no-such-tree"),
                              dict(UNSYNC), workdir=tmp)

    def test_detect_rejects_bad_inputs(self):
        with self.assertRaises((TypeError, ValueError)):
            metainject.detect(None, observables=list(OBSERVABLES))
        with self.assertRaises((TypeError, ValueError)):
            metainject.detect(UNSYNC_TRACE, observables=[])


class TestLineHasX(unittest.TestCase):
    def test_x_tokens_match(self):
        self.assertTrue(metainject.line_has_x("out_b=0xxxxxxx"))
        self.assertTrue(metainject.line_has_x("sig=0x0000xxxx"))
        self.assertTrue(metainject.line_has_x("q=32'hx"))

    def test_clean_and_names_do_not_match(self):
        self.assertFalse(metainject.line_has_x("out_b=0xdeadbeef"))
        self.assertFalse(metainject.line_has_x("xfer complete max_addr ok"))
        self.assertFalse(metainject.line_has_x("no values here"))


class TestStandardGates(unittest.TestCase):
    MODULE = "metainject"

    def test_no_loop_wiring(self):
        for sacred in ("agent/loop.py", "agent/guard.py"):
            path = os.path.join(ROOT, sacred)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(
                self.MODULE, text,
                "%s must not reference the %s module "
                "(default-off: no loop/guard wiring)" % (sacred,
                                                          self.MODULE))

    def test_tmp_only(self):
        # inject writes ONLY under the injected workdir (default: tmp).
        tmp = tempfile.mkdtemp(prefix="metainject-gate-")
        self.addCleanup(shutil.rmtree, tmp, True)
        tree = make_fixture_tree(tmp)
        scratch = metainject.inject(tree, dict(UNSYNC))
        self.addCleanup(shutil.rmtree,
                        os.path.dirname(scratch), True)
        self.assertTrue(
            os.path.abspath(scratch).startswith(
                tempfile.gettempdir().rstrip(os.sep) + os.sep),
            "default scratch must live under tmp, got %r" % (scratch,))
        repo_marks = []
        for dirpath, _d, fns in os.walk(ROOT):
            if ".git" in dirpath:
                continue
            for name in fns:
                if "metainject_x_" in name.lower():
                    repo_marks.append(os.path.join(dirpath, name))
        self.assertEqual(repo_marks, [],
                         "no injection marker may land in the repo: %r"
                         % (repo_marks,))

    def test_no_live_api(self):
        with open(os.path.join(ROOT, "agent", "discover",
                                "metainject.py"),
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
