"""Multi-signal TB writer tests (Task-wave-5: REAL per-class TB generation).

The old oracles poll only coarse completion (``xfer_done``-style byte
arrival / PC progress). ``sim/tbgen.py`` generates per-fault-class
testbenches that watch the ACTUAL signals named in the bug line --
e.g. ``mem_la_addr`` LSBs for fetch-align, ``reg_next_pc``/``current_pc``
for pc-progress, ``trap`` for trap-clean, ``mem_valid``/launch pulses
for fetch-valid-gate, ``mem_rdata`` vs ``mem_rdata_q`` for rdata-mux,
``recv_divcnt``/``send_divcnt`` vs ``cfg_divider`` for the uart family.

STATIC + LOCAL-SIM ONLY: real Verilator lint/builds/runs on scratch
dirs holding a COPY of the pinned trees (never the shared work-cache
tree, never the repo). No API, no model, no network, no writes to
``eval/``, ``runs/``, or any bundle. No commits.

- test_eight_classes_defined: exactly the big-8 board classes, each
  with signals + watcher + oracle markers.
- test_classify_board_bug_lines: every REAL ``sim/targets/*.yaml``
  bug line (loaded via the real spec loader) classifies to its board
  class (start-half disambiguated by its ``2*`` anchor).
- test_classify_unknown_loud: garbage classifies ``unknown`` and
  ``generate_tb`` refuses it loudly (never a silent generic TB).
- test_watchers_name_real_signals (>=3 classes, done for all 8):
  generated TB text contains the class's actual signal names plus the
  ``tohost``/``TRAP:``/``CYCLES=`` oracle contract and ``+seed``.
- test_no_xfer_done_only_oracle: no generated TB passes on completion
  polling alone -- each names its class signals in a check.
- test_prompt_separation_preserved: tbgen is prompt-independent
  (Task-6 verdict: default stays text-only; v3 vs v3opt untouched).
- test_deterministic: two generations are byte-identical.
- test_lint_clean_all_eight (needs verilator+tree): every generated
  TB lints clean against the pinned RTL.
- test_live_green_pristine (3 classes): fetch-align, pc-progress,
  trap-clean go GREEN on the pristine tree.
- test_live_red_on_injected_bug: fetch-align TB goes RED with a
  class-named TRAP on the injected misalign bug.

Run: python3 tests/test_tbgen.py -v   (from repo root)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIMDIR = os.path.join(ROOT, "sim")
sys.path.insert(0, SIMDIR)
sys.path.insert(0, ROOT)

import tbgen  # noqa: E402  (RED before implementation: ModuleNotFoundError)

CASES = ("picosoc-uart-div", "picorv32-fetch-misalign", "uart-tx-div",
         "uart-start-half", "fetch-highword", "rdata-latch",
         "trap-stuck", "pc-incr")

# Board case -> expected tbgen class (grounds the big-8 in the real specs).
CASE_CLASS = {
    "picosoc-uart-div": "uart-rx-div",
    "picorv32-fetch-misalign": "fetch-align",
    "uart-tx-div": "uart-tx-div",
    "uart-start-half": "uart-start-half",
    "fetch-highword": "fetch-valid-gate",
    "rdata-latch": "rdata-mux",
    "trap-stuck": "trap-clean",
    "pc-incr": "pc-progress",
}

# Class -> signals the watcher MUST name (actual bug-line signals).
CLASS_SIGNALS = {
    "uart-rx-div": ("recv_divcnt", "cfg_divider", "recv_buf_data"),
    "uart-tx-div": ("send_divcnt", "cfg_divider", "send_bitcnt"),
    "uart-start-half": ("recv_divcnt", "cfg_divider"),
    "fetch-align": ("mem_la_addr", "mem_la_read"),
    "fetch-valid-gate": ("mem_valid", "launch_next_insn"),
    "rdata-mux": ("mem_rdata", "mem_rdata_q", "mem_xfer"),
    "trap-clean": ("trap",),
    "pc-progress": ("reg_next_pc", "current_pc"),
}

VERILATOR = shutil.which("verilator")
PINNED_PICORV32 = os.path.expanduser(
    "~/.cache/afterglow/work/picorv32-ef203c2b0a3f")
NEEDS_VERILATOR = unittest.skipUnless(
    VERILATOR, "verilator not on PATH (local-sim proof needs it)")
NEEDS_TREE = unittest.skipUnless(
    os.path.isfile(os.path.join(PINNED_PICORV32, "picorv32.v")),
    "pinned picorv32 tree absent (no network fetch in tests)")


def _load_spec(case):
    from afterglow_core import verilator as vcore
    return vcore.load_case_file(os.path.join(ROOT, "sim", "targets",
                                             case + ".yaml"))


class TestTbgen(unittest.TestCase):
    def test_eight_classes_defined(self):
        ids = [c["id"] for c in tbgen.FAULT_CLASSES]
        self.assertEqual(len(ids), 8, ids)
        self.assertEqual(set(ids), set(CLASS_SIGNALS))
        self.assertEqual(len(set(ids)), 8, "class ids must be unique")
        for c in tbgen.FAULT_CLASSES:
            for key in ("id", "case", "signals", "top", "family",
                        "description"):
                self.assertIn(key, c, c.get("id"))
            self.assertIn(c["case"], CASES, c["id"])
            self.assertTrue(c["signals"], c["id"])

    def test_classify_board_bug_lines(self):
        for case in CASES:
            spec = _load_spec(case)
            got = tbgen.classify(spec["bug"], spec.get("anchor", ""))
            self.assertEqual(got, CASE_CLASS[case],
                             "case %s bug %r" % (case, spec["bug"]))

    def test_classify_two_signal_line_prefers_gate(self):
        # A line carrying both mem_valid and mem_la_addr is a gate
        # fault, not an alignment fault (branch-order regression test).
        got = tbgen.classify(
            "assign x = mem_valid & prefetch & (mem_la_addr[1:0] == 0);")
        self.assertEqual(got, "fetch-valid-gate")

    def test_classify_unknown_loud(self):
        self.assertEqual(tbgen.classify("wire foo = bar;"), "unknown")
        with self.assertRaises(ValueError):
            tbgen.generate_tb("unknown")
        with self.assertRaises(ValueError):
            tbgen.generate_tb("not-a-class")

    def test_watchers_name_real_signals(self):
        # Proves per-class signal watchers for ALL 8 (>=3 required).
        for cid, sigs in CLASS_SIGNALS.items():
            text = tbgen.generate_tb(cid)
            for sig in sigs:
                self.assertIn(sig, text,
                              "%s watcher must name signal %s" % (cid, sig))
            for marker in ("tohost: PASS", "tohost: FAIL 0xdead",
                           "TRAP:", "CYCLES=", "+seed"):
                self.assertIn(marker, text, cid)
            self.assertIn("TRAP: %s" % cid, text,
                           "%s TRAP must name its class" % cid)

    def test_no_xfer_done_only_oracle(self):
        # No generated TB may pass on completion polling alone: each
        # must gate its verdict on at least one class signal check.
        for c in tbgen.FAULT_CLASSES:
            text = tbgen.generate_tb(c["id"])
            watch = tbgen.watcher_for(c["id"])
            self.assertTrue(watch.strip(), c["id"])
            for sig in c["signals"][:2]:
                self.assertIn(sig, watch,
                              "%s watcher snippet must check %s"
                              % (c["id"], sig))

    def test_prompt_separation_preserved(self):
        # Task-6 verdict: default stays text-only. tbgen must not drag
        # in prompts, loop, visual, or multimodal paths.
        with open(os.path.join(SIMDIR, "tbgen.py"),
                   encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("from agent", "import agent", "multimodal",
                       "v3opt", "image_request"):
            self.assertNotIn(banned, src,
                             "tbgen must stay prompt/visual-independent")
        for prompt in ("v3.txt", "v3opt.txt"):
            with open(os.path.join(ROOT, "agent", "prompts", prompt),
                      encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("tbgen", text, prompt)

    def test_deterministic(self):
        for c in tbgen.FAULT_CLASSES:
            self.assertEqual(tbgen.generate_tb(c["id"]),
                             tbgen.generate_tb(c["id"]), c["id"])

    # ---- local-sim proof (verilator + pinned tree, $0, no network) ----

    def _scratch_tree(self, files):
        tmp = tempfile.mkdtemp(prefix="tbgen-")
        for rel in files:
            src = os.path.join(PINNED_PICORV32, rel)
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        return tmp

    def _lint(self, tree, cid):
        from afterglow_core import verilator as vcore
        entry = {c["id"]: c for c in tbgen.FAULT_CLASSES}[cid]
        tb_path = os.path.join(tree, "gen_%s_tb.sv" % cid)
        with open(tb_path, "w", encoding="utf-8") as fh:
            fh.write(tbgen.generate_tb(cid))
        if entry["family"] == "picorv32-fetch":
            srcs = [os.path.join(tree, "picorv32.v")]
            flags = ["-Wno-PINMISSING"]
        else:
            srcs = [os.path.join(tree, "picosoc/simpleuart.v")]
            flags = ["-Wno-WIDTH"]
        proc = subprocess.run(
            [VERILATOR, "--lint-only", "--top-module", entry["top"]]
            + flags + srcs + [tb_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=300)
        return proc

    @NEEDS_VERILATOR
    @NEEDS_TREE
    def test_lint_clean_all_eight(self):
        tree = self._scratch_tree(["picorv32.v",
                                   "picosoc/simpleuart.v"])
        try:
            for c in tbgen.FAULT_CLASSES:
                proc = self._lint(tree, c["id"])
                self.assertEqual(proc.returncode, 0,
                                 "%s lint failed:\n%s"
                                 % (c["id"], proc.stdout[-3000:]))
                self.assertNotIn("Error", proc.stdout, c["id"])
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    def _build_and_run(self, tree, cid, seed=42):
        from afterglow_core import verilator as vcore
        entry = {c["id"]: c for c in tbgen.FAULT_CLASSES}[cid]
        tb_path = os.path.join(tree, "gen_%s_tb.sv" % cid)
        with open(tb_path, "w", encoding="utf-8") as fh:
            fh.write(tbgen.generate_tb(cid))
        srcs = [os.path.join(tree, "picorv32.v")]
        mdir = os.path.join(tree, "obj_%s" % cid)
        binary = os.path.join(mdir, "V%s" % entry["top"])
        cmd = [VERILATOR, "--binary", "--timing", "-j", "4",
               "--top-module", entry["top"], "-Mdir", mdir,
               "-o", "V%s" % entry["top"]] + srcs + [tb_path] + [
                   "-Wno-PINMISSING"]
        proc = subprocess.run(cmd, cwd=tree,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              timeout=900)
        self.assertEqual(proc.returncode, 0,
                         "%s build failed:\n%s" % (cid, proc.stdout[-3000:]))
        run = subprocess.run([binary, "+seed=%d" % seed], cwd=tree,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             timeout=300)
        return run.stdout

    @NEEDS_VERILATOR
    @NEEDS_TREE
    def test_live_green_pristine(self):
        # 3 classes live-GREEN on the pristine tree (the >=3 proof).
        tree = self._scratch_tree(["picorv32.v"])
        try:
            for cid in ("fetch-align", "pc-progress", "trap-clean"):
                log = self._build_and_run(tree, cid)
                self.assertIn("tohost: PASS", log,
                              "%s pristine should be GREEN:\n%s"
                              % (cid, log[-2000:]))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    @NEEDS_VERILATOR
    @NEEDS_TREE
    def test_live_red_on_injected_bug(self):
        # The recorded misalign bug goes RED under the fetch-align TB
        # with a class-named TRAP (blind-spot movement proof).
        spec = _load_spec("picorv32-fetch-misalign")
        tree = self._scratch_tree(["picorv32.v"])
        try:
            rtl = os.path.join(tree, "picorv32.v")
            with open(rtl, encoding="utf-8") as fh:
                lines = fh.readlines()
            hits = [i for i, ln in enumerate(lines)
                    if ln.strip() == spec["anchor"].strip()]
            self.assertTrue(hits, "anchor not found in pinned rtl")
            for i in hits:
                indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
                lines[i] = indent + spec["bug"].strip() + "\n"
            with open(rtl, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            log = self._build_and_run(tree, "fetch-align")
            self.assertIn("tohost: FAIL 0xdead", log,
                          "injected misalign should be RED:\n%s"
                          % log[-2000:])
            self.assertIn("TRAP: fetch-align", log,
                          "RED must carry the class TRAP:\n%s"
                          % log[-2000:])
        finally:
            shutil.rmtree(tree, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
