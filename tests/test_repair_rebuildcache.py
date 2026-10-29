"""Task C10 (incremental rebuild cache, plan item 30, Wave C) tests:
agent/repair/rebuildcache.py.

Preserves the Verilator obj dir across attempts when only one file
changed (vs the current loop drop-on-suspect in
``agent/loop.py::resim_with_integrity``, which drops
``obj_afterglow_<case>`` on ANY stale-tree / stale-build suspect); the
suspect path still drops.

  obj_dir_for(tree, case) -> str
  snapshot(tree, files) -> {rel: sha256}
  changed_files(before, after) -> [sorted changed rels]
  decide(before, after, suspect=False, suspect_detail="") ->
      {"reuse", "drop", "changed", "reason"}
  rebuild(tree, case, build_fn, before=None, after=None, suspect=False,
          suspect_detail="") ->
      {"reused", "dropped", "changed", "reason", "build_secs",
       "result", "obj_dir"}

Contract:
- Zero or one changed file + no suspect -> REUSE (obj dir kept,
  incremental rebuild).
- Two or more changed files -> DROP (clean rebuild, obj dir removed).
- suspect True (stale-tree / stale-build, loop parity) -> DROP even
  when only one file changed; the suspect detail lands in ``reason``.
- REFUSE (raise, never fabricate): blank tree, non-dict case, missing
  case name, bad files list, missing RTL source on disk, non-dict
  before/after, build_fn None / non-callable. A build_fn exception
  propagates loudly (never a fake ``reused``).
- Timed proof (sim-only): a cache-hit rebuild is FASTER than a clean
  rebuild (a presence-dependent fake stands in for Verilator: full
  compile without cached objects, quick incremental with them).
- Real-Verilator proof (sim-only, no API): clean ``--binary`` build vs
  single-file-edit incremental rebuild of the same tiny design;
  incremental finishes first, both binaries exist.
- Default-off: explicit call only; agent/loop.py and agent/guard.py
  untouched (no rebuildcache wiring).

RED-first TDD: this file was written BEFORE
agent/repair/rebuildcache.py existed, so the first run must FAIL on
the missing module (proving the test exercises real code, not a stub).

Run: python3 tests/test_repair_rebuildcache.py -v   (from repo root)
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from repair import rebuildcache  # noqa: E402  (RED before implementation)


GOOD_V = (
    "module cachegood(input wire clk, output reg q);\n"
    "always @(posedge clk) q <= ~q;\n"
    "endmodule\n"
)

OTHER_V = (
    "module cacheother(input wire clk, output reg r);\n"
    "always @(posedge clk) r <= q_unused(1'b0);\n"
    "endmodule\n"
)


def _tiny_case(name="cache-tiny", files=("a.v", "b.v")):
    return {"name": name, "top": "cachegood", "files": list(files)}


def _write_tree(files):
    tmp = tempfile.mkdtemp(prefix="rebuildcache-")
    for name, body in files.items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    return tmp


def _seed_obj(tree, case, marker="cached.o"):
    mdir = rebuildcache.obj_dir_for(tree, case)
    os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(mdir, marker), "w", encoding="utf-8") as fh:
        fh.write("object-bytes\n")
    # Sentinel a FRESH build never recreates: its absence after
    # rebuild() proves the obj dir was really dropped (a fresh
    # build recreates the dir itself, so dir-absence is unobservable).
    with open(os.path.join(mdir, "stale.o"), "w", encoding="utf-8") as fh:
        fh.write("stale\n")
    return mdir


def _sim_build(tree, case):
    """Presence-dependent fake: full compile without cached objects,
    quick incremental with them (stands in for Verilator timing)."""
    mdir = rebuildcache.obj_dir_for(tree, case)
    marker = os.path.join(mdir, "cached.o")
    if os.path.isfile(marker):
        time.sleep(0.02)
    else:
        os.makedirs(mdir, exist_ok=True)
        time.sleep(0.30)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("object-bytes\n")
    return {"pass": False, "checksum": "sim-cache"}


class TestObjDirFor(unittest.TestCase):
    def test_path_is_obj_afterglow_case(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        got = rebuildcache.obj_dir_for(tree, _tiny_case(name="uart-div"))
        self.assertEqual(got, os.path.join(tree, "obj_afterglow_uart-div"))

    def test_blank_tree_refuses(self):
        with self.assertRaises(ValueError):
            rebuildcache.obj_dir_for("", _tiny_case())
        with self.assertRaises(ValueError):
            rebuildcache.obj_dir_for("   ", _tiny_case())

    def test_bad_case_refuses(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        with self.assertRaises(ValueError):
            rebuildcache.obj_dir_for(tree, "not-a-dict")
        with self.assertRaises(ValueError):
            rebuildcache.obj_dir_for(tree, {"name": "   "})
        with self.assertRaises(ValueError):
            rebuildcache.obj_dir_for(tree, {"top": "x"})


class TestSnapshot(unittest.TestCase):
    def test_hashes_match_file_content(self):
        import hashlib
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        got = rebuildcache.snapshot(tree, ["a.v"])
        want = hashlib.sha256(GOOD_V.encode("utf-8")).hexdigest()
        self.assertEqual(got, {"a.v": want})

    def test_edit_changes_the_hash(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        before = rebuildcache.snapshot(tree, ["a.v"])
        with open(os.path.join(tree, "a.v"), "a",
                  encoding="utf-8") as fh:
            fh.write("// one-line fix\n")
        after = rebuildcache.snapshot(tree, ["a.v"])
        self.assertNotEqual(before, after)

    def test_missing_source_refuses_loudly(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        with self.assertRaises(FileNotFoundError):
            rebuildcache.snapshot(tree, ["ghost.v"])

    def test_blank_tree_or_bad_files_refuse(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        with self.assertRaises(ValueError):
            rebuildcache.snapshot("", ["a.v"])
        with self.assertRaises(ValueError):
            rebuildcache.snapshot(tree, [])
        with self.assertRaises(ValueError):
            rebuildcache.snapshot(tree, "a.v")
        with self.assertRaises(ValueError):
            rebuildcache.snapshot(tree, ["  "])


class TestChangedFiles(unittest.TestCase):
    def test_identical_snapshots_no_change(self):
        snap = {"a.v": "h1", "b.v": "h2"}
        self.assertEqual(rebuildcache.changed_files(snap, dict(snap)), [])

    def test_modified_added_removed_all_listed_sorted(self):
        before = {"a.v": "h1", "b.v": "h2", "c.v": "h3"}
        after = {"a.v": "h1", "b.v": "h2x", "d.v": "h4"}
        self.assertEqual(rebuildcache.changed_files(before, after),
                         ["b.v", "c.v", "d.v"])

    def test_non_dict_refuses(self):
        with self.assertRaises((TypeError, ValueError)):
            rebuildcache.changed_files("nope", {})
        with self.assertRaises((TypeError, ValueError)):
            rebuildcache.changed_files({}, None)


class TestDecide(unittest.TestCase):
    def test_zero_change_reuses(self):
        snap = {"a.v": "h1"}
        out = rebuildcache.decide(snap, dict(snap))
        self.assertTrue(out["reuse"])
        self.assertFalse(out["drop"])
        self.assertEqual(out["changed"], [])

    def test_single_file_change_reuses(self):
        out = rebuildcache.decide({"a.v": "h1", "b.v": "h2"},
                                  {"a.v": "h1", "b.v": "h2x"})
        self.assertTrue(out["reuse"], "one file -> keep obj dir: %r" % (out,))
        self.assertFalse(out["drop"])
        self.assertEqual(out["changed"], ["b.v"])
        self.assertIn("1", out["reason"])

    def test_multi_file_change_drops(self):
        out = rebuildcache.decide(
            {"a.v": "h1", "b.v": "h2"}, {"a.v": "h1x", "b.v": "h2x"})
        self.assertFalse(out["reuse"])
        self.assertTrue(out["drop"])
        self.assertEqual(out["changed"], ["a.v", "b.v"])

    def test_suspect_drops_even_on_single_file_change(self):
        # Parity with loop.py resim_with_integrity drop-on-suspect:
        # the suspect path still forces a clean rebuild.
        out = rebuildcache.decide({"a.v": "h1"}, {"a.v": "h1x"},
                                  suspect=True,
                                  suspect_detail="stale-tree-suspect")
        self.assertFalse(out["reuse"])
        self.assertTrue(out["drop"])
        self.assertIn("stale-tree-suspect", out["reason"])

    def test_suspect_drops_even_with_no_change(self):
        out = rebuildcache.decide({"a.v": "h1"}, {"a.v": "h1"},
                                  suspect=True)
        self.assertFalse(out["reuse"])
        self.assertTrue(out["drop"])

    def test_bad_snapshots_refuse(self):
        with self.assertRaises((TypeError, ValueError)):
            rebuildcache.decide("nope", {})
        with self.assertRaises((TypeError, ValueError)):
            rebuildcache.decide({}, "nope")


class TestRebuild(unittest.TestCase):
    def test_single_file_change_keeps_obj_dir(self):
        tree = _write_tree({"a.v": GOOD_V, "b.v": OTHER_V})
        self.addCleanup(shutil.rmtree, tree, True)
        case = _tiny_case(files=["a.v", "b.v"])
        _seed_obj(tree, case)
        before = rebuildcache.snapshot(tree, case["files"])
        with open(os.path.join(tree, "b.v"), "a",
                  encoding="utf-8") as fh:
            fh.write("// single-file fix\n")
        after = rebuildcache.snapshot(tree, case["files"])
        out = rebuildcache.rebuild(tree, case, _sim_build,
                                  before=before, after=after)
        self.assertTrue(out["reused"])
        self.assertFalse(out["dropped"])
        self.assertTrue(os.path.isfile(
            os.path.join(rebuildcache.obj_dir_for(tree, case), "cached.o")),
            "incremental path must keep the cached objects")
        self.assertTrue(os.path.isfile(
            os.path.join(rebuildcache.obj_dir_for(tree, case), "stale.o")),
            "incremental path must not touch the obj dir at all")
        self.assertEqual(out["changed"], ["b.v"])
        self.assertIsInstance(out["build_secs"], float)
        self.assertGreaterEqual(out["build_secs"], 0.0)

    def test_suspect_forces_clean_rebuild(self):
        tree = _write_tree({"a.v": GOOD_V, "b.v": OTHER_V})
        self.addCleanup(shutil.rmtree, tree, True)
        case = _tiny_case(files=["a.v", "b.v"])
        mdir = _seed_obj(tree, case)
        before = rebuildcache.snapshot(tree, case["files"])
        with open(os.path.join(tree, "b.v"), "a",
                  encoding="utf-8") as fh:
            fh.write("// single-file fix\n")
        after = rebuildcache.snapshot(tree, case["files"])
        out = rebuildcache.rebuild(tree, case, _sim_build,
                                  before=before, after=after,
                                  suspect=True,
                                  suspect_detail="stale-build-suspect")
        self.assertFalse(out["reused"])
        self.assertTrue(out["dropped"])
        self.assertFalse(os.path.exists(os.path.join(
            rebuildcache.obj_dir_for(tree, case), "stale.o")),
            "suspect path must drop the obj dir (loop parity)")
        self.assertIn("stale-build-suspect", out["reason"])

    def test_multi_file_change_forces_clean_rebuild(self):
        tree = _write_tree({"a.v": GOOD_V, "b.v": OTHER_V})
        self.addCleanup(shutil.rmtree, tree, True)
        case = _tiny_case(files=["a.v", "b.v"])
        mdir = _seed_obj(tree, case)
        before = rebuildcache.snapshot(tree, case["files"])
        for name in ("a.v", "b.v"):
            with open(os.path.join(tree, name), "a",
                      encoding="utf-8") as fh:
                fh.write("// touch\n")
        after = rebuildcache.snapshot(tree, case["files"])
        out = rebuildcache.rebuild(tree, case, _sim_build,
                                  before=before, after=after)
        self.assertFalse(out["reused"])
        self.assertTrue(out["dropped"])
        self.assertFalse(os.path.exists(os.path.join(
            rebuildcache.obj_dir_for(tree, case), "stale.o")),
            "multi-file change must drop the obj dir")

    def test_result_forwarded_verbatim(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        case = _tiny_case(files=["a.v"])
        verdict = {"pass": True, "checksum": "verbatim-123"}
        out = rebuildcache.rebuild(tree, case, lambda t, c: verdict,
                                  before={}, after={})
        self.assertIs(out["result"], verdict)

    def test_build_error_propagates_never_fabricates(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        case = _tiny_case(files=["a.v"])

        def _boom(t, c):
            raise RuntimeError("build exploded")

        with self.assertRaises(RuntimeError):
            rebuildcache.rebuild(tree, case, _boom,
                                before={}, after={})

    def test_missing_build_fn_refuses(self):
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        case = _tiny_case(files=["a.v"])
        with self.assertRaises(ValueError):
            rebuildcache.rebuild(tree, case, None,
                                before={}, after={})
        with self.assertRaises(ValueError):
            rebuildcache.rebuild(tree, case, "not-callable",
                                before={}, after={})

    def test_blank_tree_or_bad_case_refuses(self):
        with self.assertRaises(ValueError):
            rebuildcache.rebuild("", _tiny_case(), _sim_build)
        tree = _write_tree({"a.v": GOOD_V})
        self.addCleanup(shutil.rmtree, tree, True)
        with self.assertRaises(ValueError):
            rebuildcache.rebuild(tree, "not-a-dict", _sim_build)
        with self.assertRaises(ValueError):
            rebuildcache.rebuild(tree, {"top": "x"}, _sim_build)


class TestTimedCacheHit(unittest.TestCase):
    def test_cache_hit_rebuilds_faster_than_clean(self):
        tree = _write_tree({"a.v": GOOD_V, "b.v": OTHER_V})
        self.addCleanup(shutil.rmtree, tree, True)
        case = _tiny_case(files=["a.v", "b.v"])
        _seed_obj(tree, case)  # warm cache: objects present
        before = rebuildcache.snapshot(tree, case["files"])
        with open(os.path.join(tree, "b.v"), "a",
                  encoding="utf-8") as fh:
            fh.write("// single-file fix\n")
        after = rebuildcache.snapshot(tree, case["files"])

        hit = rebuildcache.rebuild(tree, case, _sim_build,
                                  before=before, after=after)
        self.assertTrue(hit["reused"], "one file -> cache hit: %r" % (hit,))

        clean = rebuildcache.rebuild(
            tree, case, _sim_build, before=before, after=after,
            suspect=True, suspect_detail="stale-tree-suspect")
        self.assertTrue(clean["dropped"], "suspect -> clean: %r" % (clean,))
        self.assertLess(
            hit["build_secs"], clean["build_secs"],
            "cache hit (%.3fs) must beat clean rebuild (%.3fs)"
            % (hit["build_secs"], clean["build_secs"]))


class TestRealVerilator(unittest.TestCase):
    @unittest.skipUnless(shutil.which("verilator"), "no verilator binary")
    def test_incremental_beats_clean_on_real_verilator(self):
        import subprocess
        tree = _write_tree({
            "bigrom.v": (
                "module bigrom(input wire [7:0] a, output reg [31:0] d);\n"
                "always @(*) case (a)\n"
                + "".join("  8'h%02x: d = 32'h%08x;\n" % (i, i * 2654435761 % 2**32)
                           for i in range(256))
                + "  default: d = 32'h0;\nendcase\nendmodule\n"),
            "small.v": (
                "module ctop(input wire clk, input wire [7:0] a,\n"
                "             output wire [31:0] d);\n"
                "bigrom r(.a(a), .d(d));\n"
                "endmodule\n"),
            "tb_main.cpp": (
                '#include "Vctop.h"\n#include "verilated.h"\n'
                "int main(int argc, char** argv) {\n"
                "  Verilated::commandArgs(argc, argv);\n"
                "  Vctop* top = new Vctop;\n"
                "  top->eval(); delete top; return 0;\n}\n"),
        })
        self.addCleanup(shutil.rmtree, tree, True)
        case = {"name": "cache-real", "top": "ctop",
                "files": ["bigrom.v", "small.v"]}
        obj = rebuildcache.obj_dir_for(tree, case)

        def _real_build(t, c):
            # Classic --cc/--exe/--build flow (user main in the
            # testbench; --binary would generate its own main and
            # collide at link time).
            cmd = ["verilator", "--cc", "--exe", "--build", "-j", "4",
                   "--top-module", c["top"], "-Mdir", obj,
                   os.path.join(t, "bigrom.v"),
                   os.path.join(t, "small.v"),
                   os.path.join(t, "tb_main.cpp")]
            t0 = time.monotonic()
            proc = subprocess.run(cmd, cwd=t, capture_output=True,
                                  text=True, timeout=600)
            secs = time.monotonic() - t0
            if proc.returncode != 0 or not os.path.isfile(
                    os.path.join(obj, "Vctop")):
                raise RuntimeError(
                    "real verilator build failed:\n%s" % proc.stderr[-2000:])
            return {"rc": proc.returncode, "wall_secs": secs}

        before = rebuildcache.snapshot(tree, case["files"])
        clean = rebuildcache.rebuild(tree, case, _real_build,
                                    before=before, after=dict(before),
                                    suspect=True,
                                    suspect_detail="cold-cache")
        self.assertTrue(clean["dropped"])
        self.assertTrue(os.path.isfile(os.path.join(obj, "Vctop")))

        with open(os.path.join(tree, "small.v"), "a",
                  encoding="utf-8") as fh:
            fh.write("// single-file fix: re-tap check\n")
        after = rebuildcache.snapshot(tree, case["files"])
        hit = rebuildcache.rebuild(tree, case, _real_build,
                                  before=before, after=after)
        self.assertTrue(hit["reused"], "one file -> reuse: %r" % (hit,))
        self.assertTrue(os.path.isfile(os.path.join(obj, "Vctop")))
        self.assertLess(
            hit["build_secs"], clean["build_secs"],
            "incremental (%.1fs) must beat clean (%.1fs)"
            % (hit["build_secs"], clean["build_secs"]))


class TestDefaultOffNoWiring(unittest.TestCase):
    # Precise import/wiring markers only: a bare "rebuildcache"
    # substring check would false-positive on this file's own markers.
    WIRING_MARKERS = ("repair/rebuildcache", "repair.rebuildcache",
                      "from repair import rebuildcache",
                      "import rebuildcache")

    def test_loop_and_guard_do_not_wire_rebuildcache(self):
        for path in ("agent/loop.py", "agent/guard.py"):
            full = os.path.join(ROOT, path)
            with open(full, encoding="utf-8") as fh:
                src = fh.read()
            hits = [m for m in self.WIRING_MARKERS if m in src]
            self.assertFalse(
                hits,
                "%s must not wire agent/repair/rebuildcache.py "
                "(default-off: explicit call only); found %r"
                % (path, hits))


if __name__ == "__main__":
    unittest.main()
