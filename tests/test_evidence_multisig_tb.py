"""Task B6 (multi-signal TBs, plan item 16, Wave B SEE MORE) red-first tests:
agent/evidence/multisig_tb.py.

TB generator emitting data+control+interrupt checks in ONE testbench: given
an RTL file + clock/reset/port description (plain dict from caller), it
generates a SystemVerilog TB that (a) drives data patterns through the DUT,
(b) asserts control-signal sequencing, (c) raises and checks an
interrupt/flag condition. Every check reports PASS/FAIL per check with
tohost-style markers (``CHECK <name>: PASS/FAIL``, ``tohost: PASS`` /
``tohost: FAIL 0xdead``, ``TRAP: multisig <check>``, ``CYCLES=<n>``).

Protocols: ``handshake`` (generic req/ack/irq DUT, used by the fixture)
and ``uart`` (simpleuart register/loopback shape, used for validation).

Proof tests (need Verilator 5.x + pinned tree; LOUD skip otherwise, never
a faked kill):
- generated multisig TB goes RED (``CHECK control: FAIL`` + overall
  ``tohost: FAIL``) on a control-only mutant (ack stuck high, data path
  untouched) while DATA and IRQ checks still PASS (kill is control-only);
- a data-only TB from the same spec goes PASS on the SAME mutant build
  (the miss is real);
- both TBs go PASS on the pristine fixture;
- the uart TB goes PASS on the pristine simpleuart (validation).

Default-off: generation runs only on explicit call; agent/loop.py is
untouched (asserted); TBs land in the caller-chosen outdir only.
Stdlib-only; no API, no keys.

Run: python3 tests/test_evidence_multisig_tb.py -v   (from repo root)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

from evidence import multisig_tb  # noqa: E402  (RED before implementation)

VERILATOR = shutil.which("verilator")
PINNED_PICORV32 = os.path.expanduser(
    "~/.cache/afterglow/work/picorv32-ef203c2b0a3f")
NEEDS_VERILATOR = unittest.skipUnless(
    VERILATOR,
    "SKIP: verilator not on PATH -- control-mutant kill NOT proven "
    "(no fake PASS emitted; install Verilator 5.x to prove it)")
NEEDS_TREE = unittest.skipUnless(
    os.path.isfile(os.path.join(PINNED_PICORV32, "picosoc/simpleuart.v")),
    "SKIP: pinned picorv32 tree absent -- uart validation NOT proven "
    "(no network fetch in tests)")

# ---------------------------------------------------------------------------
# Fixture: 2-signal handshake DUT (data path + control-only fault surface).
# Protocol: pulse req with din; next cycle ack=1 with dout=latched din;
# irq=1 iff latched byte == 0xFF. The control-only mutant pins ack high.
# ---------------------------------------------------------------------------

FIXTURE_V = """\
module dut2 (
  input wire clk,
  input wire rst_n,
  input wire [7:0] din,
  input wire req,
  output reg [7:0] dout,
  output reg ack,
  output reg irq
);
  reg [7:0] lat;
  reg pend;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      dout <= 8'h00;
      ack <= 1'b0;
      irq <= 1'b0;
      lat <= 8'h00;
      pend <= 1'b0;
    end else begin
      ack <= 1'b0;
      irq <= 1'b0;
      if (req) begin
        lat <= din;
        pend <= 1'b1;
      end else if (pend) begin
        dout <= lat;
        ack <= 1'b1;
        pend <= 1'b0;
        if (lat == 8'hFF)
          irq <= 1'b1;
      end
    end
  end
endmodule
"""

# Control-only mutant: ack stuck high. Data (dout/lat/pend) and irq logic
# are byte-identical -- only the control line changes. Three-line anchor
# so the reset branch (same single line) is not touched.
MUT_ANCHOR = ("      ack <= 1'b0;\n"
              "      irq <= 1'b0;\n"
              "      if (req) begin")
MUT_BUG = ("      ack <= 1'b1;\n"
           "      irq <= 1'b0;\n"
           "      if (req) begin")


def _handshake_spec(rtl_file, **over):
    spec = {
        "module": "dut2",
        "rtl_file": rtl_file,
        "protocol": "handshake",
        "clock": "clk",
        "reset": "rst_n",
        "reset_active": 0,
        "data_in": "din",
        "data_out": "dout",
        "width": 8,
        "req": "req",
        "ack": "ack",
        "irq": "irq",
        "irq_pattern": 0xFF,
        "patterns": [0xA5, 0x3C, 0x00, 0xFF],
        "seed": 42,
    }
    spec.update(over)
    return spec


def _uart_spec(rtl_file, **over):
    spec = {
        "module": "simpleuart",
        "rtl_file": rtl_file,
        "protocol": "uart",
        "clock": "clk",
        "reset": "resetn",
        "reset_active": 0,
        "divider": 32,
        "patterns": [0xA5, 0x3C],
        "seed": 42,
    }
    spec.update(over)
    return spec


class SpecValidation(unittest.TestCase):
    def test_missing_required_key_refused_loudly(self):
        spec = _handshake_spec("/nonexistent/dut2.v")
        del spec["ack"]
        with self.assertRaises(ValueError):
            multisig_tb.generate_tb_text(spec)

    def test_unknown_protocol_refused_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            rtl = os.path.join(tmp, "dut2.v")
            with open(rtl, "w", encoding="utf-8") as fh:
                fh.write(FIXTURE_V)
            with self.assertRaises(ValueError):
                multisig_tb.generate_tb_text(
                    _handshake_spec(rtl, protocol="pcie"))

    def test_missing_rtl_file_refused_loudly(self):
        with self.assertRaises(ValueError):
            multisig_tb.generate_tb_text(
                _handshake_spec("/does/not/exist.v"))

    def test_module_name_mismatch_refused_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            rtl = os.path.join(tmp, "other.v")
            with open(rtl, "w", encoding="utf-8") as fh:
                fh.write(FIXTURE_V)  # defines dut2, not claimed name
            with self.assertRaises(ValueError):
                multisig_tb.generate_tb_text(
                    _handshake_spec(rtl, module="notdut"))

    def test_bad_identifier_refused_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            rtl = os.path.join(tmp, "dut2.v")
            with open(rtl, "w", encoding="utf-8") as fh:
                fh.write(FIXTURE_V)
            with self.assertRaises(ValueError):
                multisig_tb.generate_tb_text(
                    _handshake_spec(rtl, req="req;evil"))

    def test_dataonly_uart_refused_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            rtl = os.path.join(tmp, "simpleuart.v")
            shutil.copyfile(
                os.path.join(PINNED_PICORV32, "picosoc/simpleuart.v"), rtl) \
                if os.path.isfile(
                    os.path.join(PINNED_PICORV32,
                                 "picosoc/simpleuart.v")) else None
            if not os.path.isfile(rtl):
                self.skipTest("pinned tree absent; refusing loudly needs it")
            with self.assertRaises(ValueError):
                multisig_tb.generate_dataonly_tb_text(_uart_spec(rtl))


class GeneratorContract(unittest.TestCase):
    def _write_rtl(self, tmp):
        rtl = os.path.join(tmp, "dut2.v")
        with open(rtl, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE_V)
        return rtl

    def test_handshake_tb_names_all_three_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = multisig_tb.generate_tb_text(
                _handshake_spec(self._write_rtl(tmp)))
            for sig in ("din", "dout", "req", "ack", "irq", "dut2"):
                self.assertIn(sig, text, sig)
            for marker in ("CHECK data:", "CHECK control:", "CHECK irq:",
                           "tohost: PASS", "tohost: FAIL 0xdead",
                           "TRAP: multisig", "CYCLES=", "+seed"):
                self.assertIn(marker, text, marker)

    def test_uart_tb_names_data_divcnt_flag_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            rtl = os.path.join(tmp, "simpleuart.v")
            if not os.path.isfile(
                    os.path.join(PINNED_PICORV32, "picosoc/simpleuart.v")):
                self.skipTest("pinned tree absent")
            shutil.copyfile(
                os.path.join(PINNED_PICORV32, "picosoc/simpleuart.v"), rtl)
            text = multisig_tb.generate_tb_text(_uart_spec(rtl))
            for sig in ("reg_dat_di", "reg_dat_do", "reg_div_do",
                        "reg_dat_wait", "simpleuart"):
                self.assertIn(sig, text, sig)
            for marker in ("CHECK data:", "CHECK control:", "CHECK flag:",
                           "tohost: PASS", "tohost: FAIL 0xdead",
                           "TRAP: multisig", "CYCLES=", "+seed"):
                self.assertIn(marker, text, marker)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = _handshake_spec(self._write_rtl(tmp))
            self.assertEqual(multisig_tb.generate_tb_text(spec),
                             multisig_tb.generate_tb_text(spec))

    def test_dataonly_tb_checks_data_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = multisig_tb.generate_dataonly_tb_text(
                _handshake_spec(self._write_rtl(tmp)))
            self.assertIn("CHECK data:", text)
            self.assertIn("tohost: PASS", text)
            self.assertNotIn("CHECK control:", text)
            self.assertNotIn("CHECK irq:", text)

    def test_writes_to_caller_outdir_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            rtl = self._write_rtl(tmp)
            outdir = os.path.join(tmp, "my_tbs", "nested")
            before = set()
            for dp, _dn, fn in os.walk(tmp):
                before.update(os.path.join(dp, f) for f in fn)
            path = multisig_tb.generate_tb(_handshake_spec(rtl), outdir)
            self.assertTrue(path.startswith(outdir), path)
            self.assertTrue(os.path.isfile(path))
            with open(rtl, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), FIXTURE_V,
                                 "generator must not modify the RTL file")
            self.assertTrue(path.endswith(".sv"))

    def test_default_off_loop_untouched(self):
        loop = os.path.join(ROOT, "agent", "loop.py")
        with open(loop, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("multisig", text,
                         "Wave B default-off: loop.py must not wire this in")


# ---------------------------------------------------------------------------
# Live Verilator proof (short sims, fixed seed 42).
# ---------------------------------------------------------------------------

def _build_and_run(tree, rtl_text_or_path, tb_text, top, seed=42,
                   extra=()):
    """Write RTL+TB into tree, verilator build, run with +seed. -> log."""
    rtl_path = os.path.join(tree, "dut_build.v")
    if os.path.isfile(str(rtl_text_or_path)):
        shutil.copyfile(str(rtl_text_or_path), rtl_path)
    else:
        with open(rtl_path, "w", encoding="utf-8") as fh:
            fh.write(rtl_text_or_path)
    tb_path = os.path.join(tree, "gen_%s_tb.sv" % top)
    with open(tb_path, "w", encoding="utf-8") as fh:
        fh.write(tb_text)
    mdir = os.path.join(tree, "obj_%s" % top)
    binary = os.path.join(mdir, "V%s" % top)
    cmd = [VERILATOR, "--binary", "--timing", "-j", "4",
           "--top-module", top, "-Mdir", mdir, "-o", "V%s" % top,
           rtl_path, tb_path] + list(extra)
    proc = subprocess.run(cmd, cwd=tree, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=900)
    assert proc.returncode == 0, \
        "%s build failed:\n%s" % (top, proc.stdout[-3000:])
    run = subprocess.run([binary, "+seed=%d" % seed], cwd=tree,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, timeout=300)
    assert run.returncode == 0, \
        "%s run crashed (want tohost verdict, not a crash):\n%s" % (
            top, run.stdout[-3000:])
    return run.stdout


class LiveKillProof(unittest.TestCase):
    @NEEDS_VERILATOR
    def test_pristine_all_three_checks_pass(self):
        tree = tempfile.mkdtemp(prefix="multisig-")
        try:
            outdir = os.path.join(tree, "tbs")
            with tempfile.TemporaryDirectory() as srctmp:
                rtl = os.path.join(srctmp, "dut2.v")
                with open(rtl, "w", encoding="utf-8") as fh:
                    fh.write(FIXTURE_V)
                spec = _handshake_spec(rtl)
                tb_text = multisig_tb.generate_tb_text(spec)
                multisig_tb.generate_tb(spec, outdir)
            self.assertTrue(os.path.isfile(
                os.path.join(outdir, "tb_dut2_multisig.sv")))
            log = _build_and_run(tree, FIXTURE_V, tb_text,
                                 "tb_dut2_multisig")
            for line in ("CHECK data: PASS", "CHECK control: PASS",
                         "CHECK irq: PASS", "tohost: PASS"):
                self.assertIn(line, log,
                              "pristine should be all-PASS:\n%s"
                              % log[-2000:])
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    @NEEDS_VERILATOR
    def test_control_mutant_killed_while_dataonly_misses(self):
        # Control-only mutant: ack stuck high; data/irq paths untouched.
        self.assertEqual(FIXTURE_V.count(MUT_ANCHOR), 1,
                         "mutant anchor must hit exactly one line")
        mutant = FIXTURE_V.replace(MUT_ANCHOR, MUT_BUG)
        tree = tempfile.mkdtemp(prefix="multisig-mut-")
        try:
            with tempfile.TemporaryDirectory() as srctmp:
                rtl = os.path.join(srctmp, "dut2.v")
                with open(rtl, "w", encoding="utf-8") as fh:
                    fh.write(FIXTURE_V)
                spec = _handshake_spec(rtl)
                tb_multi = multisig_tb.generate_tb_text(spec)
                tb_dataonly = multisig_tb.generate_dataonly_tb_text(spec)
            log_multi = _build_and_run(tree, mutant, tb_multi,
                                       "tb_dut2_multisig")
            # The kill is control-only: DATA + IRQ still PASS, CONTROL FAILs.
            self.assertIn("CHECK data: PASS", log_multi,
                          "kill must be control-only (data passes):\n%s"
                          % log_multi[-2000:])
            self.assertIn("CHECK control: FAIL", log_multi,
                          "multisig must flag the control fault:\n%s"
                          % log_multi[-2000:])
            self.assertIn("CHECK irq: PASS", log_multi,
                          "kill must be control-only (irq passes):\n%s"
                          % log_multi[-2000:])
            self.assertIn("tohost: FAIL 0xdead", log_multi,
                          "multisig overall verdict must be FAIL:\n%s"
                          % log_multi[-2000:])
            self.assertIn("TRAP: multisig control", log_multi,
                          "RED must carry the control TRAP:\n%s"
                          % log_multi[-2000:])
            log_single = _build_and_run(tree, mutant, tb_dataonly,
                                        "tb_dut2_dataonly")
            self.assertIn("tohost: PASS", log_single,
                          "data-only TB must MISS the control mutant "
                          "(proving the miss is real):\n%s"
                          % log_single[-2000:])
            self.assertNotIn("tohost: FAIL", log_single,
                             "data-only TB must not catch it:\n%s"
                             % log_single[-2000:])
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    @NEEDS_VERILATOR
    @NEEDS_TREE
    def test_uart_pristine_validation(self):
        # Sim validation: uart TB (data + divcnt + flag checks) on the
        # pristine simpleuart tree must go GREEN.
        tree = tempfile.mkdtemp(prefix="multisig-uart-")
        try:
            rtl_src = os.path.join(PINNED_PICORV32, "picosoc/simpleuart.v")
            spec = _uart_spec(rtl_src)
            tb_text = multisig_tb.generate_tb_text(spec)
            log = _build_and_run(tree, rtl_src, tb_text,
                                 "tb_simpleuart_multisig",
                                 extra=("-Wno-WIDTH",))
            for line in ("CHECK data: PASS", "CHECK control: PASS",
                         "CHECK flag: PASS", "tohost: PASS"):
                self.assertIn(line, log,
                              "pristine simpleuart should be all-PASS:\n%s"
                              % log[-2000:])
        finally:
            shutil.rmtree(tree, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
