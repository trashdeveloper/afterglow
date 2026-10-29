#!/usr/bin/env python3
"""Task B6: Multi-signal TBs (plan item 16, Wave B SEE MORE).

TB generator emitting data+control+interrupt checks in ONE testbench.
Given an RTL file + clock/reset/port description (a plain dict from the
caller), it generates a deterministic SystemVerilog testbench that:

  (a) drives data patterns through the DUT (data check);
  (b) asserts control-signal sequencing (control check);
  (c) raises and checks an interrupt/flag condition (irq/flag check).

Every check reports PASS/FAIL per check with tohost-style markers::

  CHECK data: PASS            CHECK control: FAIL ack-stuck ...
  tohost: PASS  |  tohost: FAIL 0xdead
  TRAP: multisig control      (one line per failed check)
  CYCLES=<n>

plus ``+seed`` acceptance (logged; stimulus itself is fixed-pattern so
runs are deterministic).

Two protocol templates (``spec["protocol"]`` selects; anything else is
refused loudly with ValueError -- never a silent generic TB):

  ``handshake`` -- a generic req/ack/irq DUT: pulse ``req`` with
    ``data_in``; next cycle ``ack``=1 carries ``data_out``=latched word;
    ``irq``=1 iff the latched word equals ``irq_pattern``. The control
    check asserts ack-idle (no ack without req), ack-arrival after req,
    and ack-release (no stuck ack). The data check uses FIXED waits
    (never waits on ack) so a stuck-ack mutant still passes DATA --
    proving the kill is control-only.
  ``uart`` -- the real simpleuart register/loopback shape: program the
    baud divider (control: ``reg_div_do`` follows + ``reg_dat_wait``
    backpressure while TX is busy + true-TX-idle sequencing + TX
    completion via loopback arrival), send bytes through the TX->RX
    loopback (data: echoed bytes match), observe the valid flag raise
    (byte arrived) and clear after ``reg_dat_re`` (flag: raise +
    clear). TX-idle is polled hierarchically (``dut.send_bitcnt`` /
    ``dut.send_dummy``: testbench-only observation, no RTL change --
    holding ``reg_dat_we`` to observe ``reg_dat_wait`` would queue a
    spurious byte whenever the transmitter is idle).

Interfaces::

  generate_tb_text(spec) -> str          (pure; no filesystem writes)
  generate_dataonly_tb_text(spec) -> str (handshake only: data check alone,
      same stimulus/oracle; used to prove a control-only miss is real)
  generate_tb(spec, outdir) -> str       (writes <top>.sv, returns abspath)
  generate_dataonly_tb(spec, outdir) -> str

Default-off contract (Wave B header: additive + default-off): this module
runs ONLY on explicit call. It imports nothing from agent/loop.py and no
caller is wired: generated TBs go to the caller-chosen ``outdir`` alone
(the RTL file is opened read-only to verify it exists and defines the
claimed module; it is never modified).

Honesty contract: unknown protocol / missing key / bad identifier /
missing RTL / module-name mismatch all raise ValueError loudly. The
data-only variant for ``uart`` is refused loudly (not defined) rather
than faked.

Pure stdlib (os, re); no API, no keys, no network.
"""
import os
import re

_PROTOCOLS = ("handshake", "uart")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)\b")

_COMMON_REQUIRED = ("module", "rtl_file", "protocol")
_HANDSHAKE_REQUIRED = ("clock", "reset", "data_in", "data_out", "req",
                       "ack", "irq")
_UART_REQUIRED = ("clock", "reset")

# data-only comparison TBs exist only for the handshake protocol: the
# fixture kill-proof needs "same stimulus, data check alone".
_DATAONLY_PROTOCOLS = ("handshake",)


def _need_ident(spec, key):
    val = spec.get(key)
    if not isinstance(val, str) or not _IDENT_RE.match(val):
        raise ValueError(
            "multisig_tb: spec[%r] must be a Verilog identifier, got %r "
            "(refusing to guess port names)" % (key, val))
    return val


def _need_int(spec, key, default, lo=0, hi=(1 << 32) - 1):
    val = spec.get(key, default)
    if isinstance(val, bool) or not isinstance(val, int) \
            or not (lo <= val <= hi):
        raise ValueError(
            "multisig_tb: spec[%r] must be an int in [%d, %d], got %r"
            % (key, lo, hi, val))
    return val


def _validate_common(spec):
    """Shared spec checks -> (module, rtl_text). Loud ValueError on any gap."""
    if not isinstance(spec, dict):
        raise ValueError(
            "multisig_tb: spec must be a plain dict, got %r" % (type(spec),))
    for key in _COMMON_REQUIRED:
        if key not in spec or spec[key] is None \
                or (isinstance(spec[key], str) and not spec[key].strip()):
            raise ValueError(
                "multisig_tb: spec missing required key %r (refusing to "
                "guess)" % (key,))
    proto = spec["protocol"]
    if proto not in _PROTOCOLS:
        raise ValueError(
            "multisig_tb: unknown protocol %r (want one of %s)"
            % (proto, ", ".join(_PROTOCOLS)))
    module = _need_ident(spec, "module")
    rtl_file = spec["rtl_file"]
    if not isinstance(rtl_file, str) or not os.path.isfile(rtl_file):
        raise ValueError(
            "multisig_tb: rtl_file not found: %r" % (rtl_file,))
    try:
        with open(rtl_file, "r", encoding="utf-8", errors="replace") as fh:
            rtl_text = fh.read()
    except OSError as exc:
        raise ValueError(
            "multisig_tb: cannot read rtl_file %r: %s" % (rtl_file, exc))
    if module not in _MODULE_RE.findall(rtl_text):
        raise ValueError(
            "multisig_tb: module %r not defined in %s (refusing to wire "
            "a TB at the wrong DUT)" % (module, rtl_file))
    return module, rtl_text


def _validate_handshake(spec):
    _validate_common(spec)
    for key in _HANDSHAKE_REQUIRED:
        if key not in spec:
            raise ValueError(
                "multisig_tb: handshake spec missing required key %r"
                % (key,))
        _need_ident(spec, key)
    for key in ("clock", "reset"):
        _need_ident(spec, key)
    reset_active = spec.get("reset_active", 0)
    if reset_active not in (0, 1):
        raise ValueError(
            "multisig_tb: reset_active must be 0 or 1, got %r"
            % (reset_active,))
    width = _need_int(spec, "width", 8, lo=1, hi=32)
    pats = spec.get("patterns", (0xA5, 0x3C, 0x00, 0xFF))
    try:
        pats = list(pats)
    except TypeError:
        raise ValueError(
            "multisig_tb: patterns must be a list of ints, got %r" % (pats,))
    if not pats:
        raise ValueError("multisig_tb: patterns must be non-empty")
    for pat in pats:
        if isinstance(pat, bool) or not isinstance(pat, int) \
                or not (0 <= pat < (1 << width)):
            raise ValueError(
                "multisig_tb: pattern %r out of range for width %d"
                % (pat, width))
    irq_pat = _need_int(spec, "irq_pattern", 0xFF, lo=0,
                        hi=(1 << width) - 1)
    top = spec.get("top") or ("tb_%s_multisig" % spec["module"])
    if not _IDENT_RE.match(top):
        raise ValueError("multisig_tb: bad top %r" % (top,))
    return {"pats": pats, "irq_pat": irq_pat, "width": width,
            "reset_active": reset_active, "top": top}


def _validate_uart(spec):
    _validate_common(spec)
    for key in _UART_REQUIRED:
        if key not in spec:
            raise ValueError(
                "multisig_tb: uart spec missing required key %r" % (key,))
        _need_ident(spec, key)
    reset_active = spec.get("reset_active", 0)
    if reset_active not in (0, 1):
        raise ValueError(
            "multisig_tb: reset_active must be 0 or 1, got %r"
            % (reset_active,))
    divider = _need_int(spec, "divider", 32, lo=2, hi=1000000)
    pats = spec.get("patterns", (0xA5, 0x3C))
    try:
        pats = list(pats)
    except TypeError:
        raise ValueError(
            "multisig_tb: patterns must be a list of ints, got %r" % (pats,))
    if not pats:
        raise ValueError("multisig_tb: patterns must be non-empty")
    for pat in pats:
        if isinstance(pat, bool) or not isinstance(pat, int) \
                or not (0 <= pat <= 0xFF):
            raise ValueError(
                "multisig_tb: uart pattern %r must be a byte" % (pat,))
    top = spec.get("top") or ("tb_%s_multisig" % spec["module"])
    if not _IDENT_RE.match(top):
        raise ValueError("multisig_tb: bad top %r" % (top,))
    return {"divider": divider, "pats": pats,
            "reset_active": reset_active, "top": top}


def _handshake_tb(spec, v):
    """Full multisig TB text for the handshake protocol."""
    width = v["width"]
    msb = width - 1
    rst_assert = "0" if v["reset_active"] == 0 else "1"
    rst_deassert = "1" if v["reset_active"] == 0 else "0"
    pats = v["pats"]
    npats = len(pats)
    pat_inits = "\n".join(
        "\tpats[%d] = %d'h%X;" % (i, width, p & ((1 << width) - 1))
        for i, p in enumerate(pats))
    irq_hex = "%X" % (v["irq_pat"] & ((1 << width) - 1))
    return """\
// @TOP@: multisig TB (agent/evidence/multisig_tb.py, handshake protocol).
// One TB, three checks: (a) DATA drives fixed patterns and compares
// @DATA_OUT@ (fixed waits, never gates on @ACK@); (b) CONTROL asserts
// @ACK@ sequencing: idle-low, arrival after @REQ@, release after the
// transaction; (c) IRQ raises iff the latched word is @IRQ_HEX@.
// Oracle: "CHECK <name>: PASS/FAIL" + "tohost: PASS/FAIL 0xdead" +
// "TRAP: multisig <check>" + "CYCLES=<n>". Deterministic; +seed logged.
// Build with: verilator --binary --timing.
module @TOP@;
\treg @CLOCK@ = 0;
\talways #5 @CLOCK@ = ~@CLOCK@;

\treg @RESET@ = 1'b@RST_A@;
\treg [@MSB@:0] @DATA_IN@ = 0;
\treg @REQ@ = 0;
\twire [@MSB@:0] @DATA_OUT@;
\twire @ACK@;
\twire @IRQ@;

\t@MODULE@ dut (
\t\t.@CLOCK@(@CLOCK@),
\t\t.@RESET@(@RESET@),
\t\t.@DATA_IN@(@DATA_IN@),
\t\t.@REQ@(@REQ@),
\t\t.@DATA_OUT@(@DATA_OUT@),
\t\t.@ACK@(@ACK@),
\t\t.@IRQ@(@IRQ@)
\t);

\tint unsigned cyc = 0;
\talways @(posedge @CLOCK@) cyc <= cyc + 1;

\tint seed_val = 0;
\tbit data_ok = 1;
\tbit ctrl_ok = 1;
\tbit flag_ok = 1;
\tinteger i;
\treg [@MSB@:0] pats [0:@NPATS@];
\treg expect_irq;

\tinitial begin
\t\t$value$plusargs("seed=%d", seed_val);
@PAT_INITS@
\t\t$display("- @TOP@ seed %0d", seed_val);
\t\t$display("- multisig handshake: data+control+irq checks");
\t\t@RESET@ = 1'b@RST_A@;
\t\t@REQ@ = 1'b0;
\t\t@DATA_IN@ = 0;
\t\trepeat (5) @(posedge @CLOCK@);
\t\t@RESET@ = 1'b@RST_D@;
\t\trepeat (2) @(posedge @CLOCK@);
\t\t@(posedge @CLOCK@); #1;
\t\tif (@ACK@ !== 1'b0) begin
\t\t\tctrl_ok = 1'b0;
\t\t\t$display("CHECK control: FAIL ack-high-while-idle ack=%b", @ACK@);
\t\tend else begin
\t\t\t$display("CHECK control: idle-ack-low ok");
\t\tend
\t\tfor (i = 0; i < @NLOOPS@; i = i + 1) begin
\t\t\texpect_irq = (pats[i] == @W@'h@IRQ_HEX@);
\t\t\t@DATA_IN@ = pats[i];
\t\t\t@REQ@ = 1'b1;
\t\t\t@(posedge @CLOCK@); #1;
\t\t\t@REQ@ = 1'b0;
\t\t\t@(posedge @CLOCK@); #1;
\t\t\tif (@ACK@ !== 1'b1) begin
\t\t\t\tctrl_ok = 1'b0;
\t\t\t\t$display("CHECK control: FAIL no-ack pat=%h", pats[i]);
\t\t\tend
\t\t\tif (@IRQ@ !== expect_irq) begin
\t\t\t\tflag_ok = 1'b0;
\t\t\t\t$display("CHECK irq: FAIL pat=%h irq=%b want=%b",
\t\t\t\t\tpats[i], @IRQ@, expect_irq);
\t\t\tend
\t\t\trepeat (3) @(posedge @CLOCK@); #1;
\t\t\tif (@ACK@ !== 1'b0) begin
\t\t\t\tctrl_ok = 1'b0;
\t\t\t\t$display("CHECK control: FAIL ack-stuck pat=%h ack=%b",
\t\t\t\t\tpats[i], @ACK@);
\t\t\tend
\t\t\tif (@DATA_OUT@ !== pats[i]) begin
\t\t\t\tdata_ok = 1'b0;
\t\t\t\t$display("CHECK data: FAIL pat=%h got=%h",
\t\t\t\t\tpats[i], @DATA_OUT@);
\t\t\tend
\t\tend
\t\tif (data_ok) $display("CHECK data: PASS");
\t\telse $display("CHECK data: FAIL");
\t\tif (ctrl_ok) $display("CHECK control: PASS");
\t\telse $display("CHECK control: FAIL");
\t\tif (flag_ok) $display("CHECK irq: PASS");
\t\telse $display("CHECK irq: FAIL");
\t\tif (data_ok && ctrl_ok && flag_ok) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (!data_ok) $display("TRAP: multisig data");
\t\t\tif (!ctrl_ok) $display("TRAP: multisig control");
\t\t\tif (!flag_ok) $display("TRAP: multisig irq");
\t\tend
\t\t$display("CYCLES=%0d", cyc);
\t\t$finish;
\tend
endmodule
""".replace("@TOP@", v["top"]) \
       .replace("@MODULE@", spec["module"]) \
       .replace("@CLOCK@", spec["clock"]) \
       .replace("@RESET@", spec["reset"]) \
       .replace("@RST_A@", rst_assert) \
       .replace("@RST_D@", rst_deassert) \
       .replace("@DATA_IN@", spec["data_in"]) \
       .replace("@DATA_OUT@", spec["data_out"]) \
       .replace("@REQ@", spec["req"]) \
       .replace("@ACK@", spec["ack"]) \
       .replace("@IRQ@", spec["irq"]) \
       .replace("@MSB@", str(msb)) \
       .replace("@W@", str(width)) \
       .replace("@IRQ_HEX@", irq_hex) \
       .replace("@NPATS@", str(npats - 1)) \
       .replace("@NLOOPS@", str(npats)) \
       .replace("@PAT_INITS@", pat_inits)


def _uart_tb(spec, v):
    """Full multisig TB text for the simpleuart register/loopback shape."""
    divider = v["divider"]
    pats = v["pats"]
    npats = len(pats)
    pat_inits = "\n".join(
        "\tpats[%d] = 8'h%02X;" % (i, p) for i, p in enumerate(pats))
    rst_assert = "0" if v["reset_active"] == 0 else "1"
    rst_deassert = "1" if v["reset_active"] == 0 else "0"
    return """\
// @TOP@: multisig TB (agent/evidence/multisig_tb.py, uart protocol).
// One TB, three checks on simpleuart: (a) DATA sends fixed bytes through
// the TX->RX loopback and compares the echo; (b) CONTROL asserts divider
// sequencing (reg_div_do follows the programmed divider), wait
// backpressure (reg_dat_wait while TX busy) and TX completion via RX
// arrival; (c) FLAG raises (byte arrived: reg_dat_do == echo, not ~0)
// and clears after reg_dat_re.
// Oracle: "CHECK <name>: PASS/FAIL" + "tohost: PASS/FAIL 0xdead" +
// "TRAP: multisig <check>" + "CYCLES=<n>". Deterministic; +seed logged.
// Build with: verilator --binary --timing.
module @TOP@;
\treg clk = 0;
\talways #5 clk = ~clk;

\treg resetn = 1'b@RST_A@;
\twire ser_tx;
\twire ser_rx;
\tassign ser_rx = ser_tx;
\treg [3:0] reg_div_we = 0;
\treg [31:0] reg_div_di = 0;
\twire [31:0] reg_div_do;
\treg reg_dat_we = 0;
\treg reg_dat_re = 0;
\treg [31:0] reg_dat_di = 0;
\twire [31:0] reg_dat_do;
\twire reg_dat_wait;

\t@MODULE@ #(.DEFAULT_DIV(@DIV@)) dut (
\t\t.clk(clk),
\t\t.resetn(resetn),
\t\t.ser_tx(ser_tx),
\t\t.ser_rx(ser_rx),
\t\t.reg_div_we(reg_div_we),
\t\t.reg_div_di(reg_div_di),
\t\t.reg_div_do(reg_div_do),
\t\t.reg_dat_we(reg_dat_we),
\t\t.reg_dat_re(reg_dat_re),
\t\t.reg_dat_di(reg_dat_di),
\t\t.reg_dat_do(reg_dat_do),
\t\t.reg_dat_wait(reg_dat_wait)
\t);

\tint unsigned cyc = 0;
\talways @(posedge clk) cyc <= cyc + 1;

\tint seed_val = 0;
\tbit data_ok = 1;
\tbit ctrl_ok = 1;
\tbit flag_ok = 1;
\tinteger i;
\tinteger t;
\treg [7:0] pats [0:@NPATS@];
\treg arrived;

\tinitial begin
\t\t$value$plusargs("seed=%d", seed_val);
@PAT_INITS@
\t\t$display("- @TOP@ seed %0d", seed_val);
\t\t$display("- multisig uart: data+divcnt+flag checks div=@DIV@");
\t\tresetn = 1'b@RST_A@;
\t\trepeat (20) @(posedge clk);
\t\tresetn = 1'b@RST_D@;
\t\t// CONTROL: program the divider; reg_div_do must follow.
\t\t@(posedge clk);
\t\treg_div_we = 4'hf;
\t\treg_div_di = 32'd@DIV@;
\t\t@(posedge clk); #1;
\t\treg_div_we = 4'h0;
\t\t@(posedge clk); #1;
\t\tif (reg_div_do !== 32'd@DIV@) begin
\t\t\tctrl_ok = 1'b0;
\t\t\t$display("CHECK control: FAIL div-do=%h want=%h",
\t\t\t\treg_div_do, 32'd@DIV@);
\t\tend
\t\t// CONTROL: flush the power-on dummy frame(s). Poll the TX
\t\t// control state until the transmitter is truly idle. This MUST
\t\t// NOT hold reg_dat_we: a we cycle while idle would queue a
\t\t// spurious byte (learned the hard way: it off-by-ones every
\t\t// later arrival). Hierarchical read is testbench-only
\t\t// observation, no RTL change.
\t\tarrived = 1'b0;
\t\tfor (t = 0; t < 8000; t = t + 1) begin
\t\t\t@(posedge clk); #1;
\t\t\tif (dut.send_bitcnt == 0 && !dut.send_dummy) begin arrived = 1'b1; t = 8000; end
\t\tend
\t\tif (!arrived) begin
\t\t\tctrl_ok = 1'b0;
\t\t\t$display("CHECK control: FAIL dummy never idle");
\t\tend
\t\t// FLAG pre: nothing received yet -> reg_dat_do is ~0.
\t\t@(posedge clk); #1;
\t\tif (reg_dat_do !== 32'hFFFFFFFF) begin
\t\t\tflag_ok = 1'b0;
\t\t\t$display("CHECK flag: FAIL pre-valid do=%h", reg_dat_do);
\t\tend
\t\tfor (i = 0; i < @N@; i = i + 1) begin
\t\t\t// CONTROL: the previous byte's RX valid raises before its
\t\t\t// TX stop bit ends, so poll for true TX idle before sending
\t\t\t// (hierarchical read; holding we here would queue a
\t\t\t// spurious byte).
\t\t\tarrived = 1'b0;
\t\t\tfor (t = 0; t < 8000; t = t + 1) begin
\t\t\t\t@(posedge clk); #1;
\t\t\t\tif (dut.send_bitcnt == 0 && !dut.send_dummy) begin arrived = 1'b1; t = 8000; end
\t\t\tend
\t\t\tif (!arrived) begin
\t\t\t\tctrl_ok = 1'b0;
\t\t\t\t$display("CHECK control: FAIL tx-busy pat=%h", pats[i]);
\t\t\tend
\t\t\t// DATA: send one byte.
\t\t\t@(posedge clk);
\t\t\treg_dat_we = 1'b1;
\t\t\treg_dat_di = {24'h0, pats[i]};
\t\t\t@(posedge clk); #1;
\t\t\treg_dat_we = 1'b0;
\t\t\t// CONTROL: backpressure -- wait asserts while TX busy.
\t\t\treg_dat_we = 1'b1;
\t\t\t@(posedge clk); #1;
\t\t\tif (reg_dat_wait !== 1'b1) begin
\t\t\t\tctrl_ok = 1'b0;
\t\t\t\t$display("CHECK control: FAIL no-wait pat=%h", pats[i]);
\t\t\tend
\t\t\treg_dat_we = 1'b0;
\t\t\t// CONTROL+FLAG: TX completion shows up as RX arrival.
\t\t\tarrived = 1'b0;
\t\t\tfor (t = 0; t < 8000; t = t + 1) begin
\t\t\t\t@(posedge clk); #1;
\t\t\t\tif (reg_dat_do !== 32'hFFFFFFFF) begin
\t\t\t\t\tarrived = 1'b1; t = 8000;
\t\t\t\tend
\t\t\tend
\t\t\tif (!arrived) begin
\t\t\t\tctrl_ok = 1'b0;
\t\t\t\tflag_ok = 1'b0;
\t\t\t\t$display("CHECK control: FAIL no-arrival pat=%h", pats[i]);
\t\t\tend else begin
\t\t\t\tif (reg_dat_do[7:0] !== pats[i]) begin
\t\t\t\t\tdata_ok = 1'b0;
\t\t\t\t\t$display("CHECK data: FAIL pat=%h got=%h",
\t\t\t\t\t\tpats[i], reg_dat_do[7:0]);
\t\t\t\tend
\t\t\tend
\t\t\t// FLAG: clear on reg_dat_re -> reg_dat_do back to ~0.
\t\t\t@(posedge clk);
\t\t\treg_dat_re = 1'b1;
\t\t\t@(posedge clk); #1;
\t\t\treg_dat_re = 1'b0;
\t\t\t@(posedge clk); #1;
\t\t\tif (reg_dat_do !== 32'hFFFFFFFF) begin
\t\t\t\tflag_ok = 1'b0;
\t\t\t\t$display("CHECK flag: FAIL no-clear pat=%h do=%h",
\t\t\t\t\t\tpats[i], reg_dat_do);
\t\t\tend
\t\tend
\t\tif (data_ok) $display("CHECK data: PASS");
\t\telse $display("CHECK data: FAIL");
\t\tif (ctrl_ok) $display("CHECK control: PASS");
\t\telse $display("CHECK control: FAIL");
\t\tif (flag_ok) $display("CHECK flag: PASS");
\t\telse $display("CHECK flag: FAIL");
\t\tif (data_ok && ctrl_ok && flag_ok) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (!data_ok) $display("TRAP: multisig data");
\t\t\tif (!ctrl_ok) $display("TRAP: multisig control");
\t\t\tif (!flag_ok) $display("TRAP: multisig flag");
\t\tend
\t\t$display("CYCLES=%0d", cyc);
\t\t$finish;
\tend
endmodule
""".replace("@TOP@", v["top"]) \
       .replace("@MODULE@", spec["module"]) \
       .replace("@RST_A@", rst_assert) \
       .replace("@RST_D@", rst_deassert) \
       .replace("@DIV@", str(divider)) \
       .replace("@NPATS@", str(npats - 1)) \
       .replace("@N@", str(npats)) \
       .replace("@PAT_INITS@", pat_inits)


def _dataonly_handshake_tb(spec, v):
    """Data-check-alone TB: same stimulus, fixed waits, never looks at
    req/ack/irq sequencing. Used to prove a control-only miss is real."""
    width = v["width"]
    msb = width - 1
    rst_assert = "0" if v["reset_active"] == 0 else "1"
    rst_deassert = "1" if v["reset_active"] == 0 else "0"
    pats = v["pats"]
    npats = len(pats)
    pat_inits = "\n".join(
        "\tpats[%d] = %d'h%X;" % (i, width, p & ((1 << width) - 1))
        for i, p in enumerate(pats))
    return """\
// tb_@MODULE@_dataonly: data-check-alone TB (multisig_tb.py companion).
// Same stimulus as the multisig TB but checks ONLY @DATA_OUT@ values
// after fixed waits -- it never samples @ACK@/@IRQ@ sequencing, so a
// control-only fault passes it (the miss is real, not a weak stimulus).
// Oracle: "CHECK data: PASS/FAIL" + "tohost: PASS/FAIL 0xdead" +
// "CYCLES=<n>". Deterministic; +seed logged.
module tb_@MODULE@_dataonly;
	reg @CLOCK@ = 0;
	always #5 @CLOCK@ = ~@CLOCK@;

	reg @RESET@ = 1'b@RST_A@;
	reg [@MSB@:0] @DATA_IN@ = 0;
	reg @REQ@ = 0;
	wire [@MSB@:0] @DATA_OUT@;
	wire @ACK@;
	wire @IRQ@;

	@MODULE@ dut (
		.@CLOCK@(@CLOCK@),
		.@RESET@(@RESET@),
		.@DATA_IN@(@DATA_IN@),
		.@REQ@(@REQ@),
		.@DATA_OUT@(@DATA_OUT@),
		.@ACK@(@ACK@),
		.@IRQ@(@IRQ@)
	);

	/* verilator lint_off UNUSED */
	wire unused_ack = @ACK@;
	wire unused_irq = @IRQ@;
	/* verilator lint_on UNUSED */

	int unsigned cyc = 0;
	always @(posedge @CLOCK@) cyc <= cyc + 1;

	int seed_val = 0;
	bit data_ok = 1;
	integer i;
	reg [@MSB@:0] pats [0:@NPATS@];

	initial begin
		$value$plusargs("seed=%d", seed_val);
@PAT_INITS@
		$display("- tb_@MODULE@_dataonly seed %0d", seed_val);
		$display("- dataonly: data values alone, no control/irq checks");
		@RESET@ = 1'b@RST_A@;
		@REQ@ = 1'b0;
		@DATA_IN@ = 0;
		repeat (5) @(posedge @CLOCK@);
		@RESET@ = 1'b@RST_D@;
		repeat (2) @(posedge @CLOCK@);
		for (i = 0; i < @N@; i = i + 1) begin
			@DATA_IN@ = pats[i];
			@REQ@ = 1'b1;
			@(posedge @CLOCK@); #1;
			@REQ@ = 1'b0;
			repeat (4) @(posedge @CLOCK@); #1;
			if (@DATA_OUT@ !== pats[i]) begin
				data_ok = 1'b0;
				$display("CHECK data: FAIL pat=%h got=%h",
					pats[i], @DATA_OUT@);
			end
		end
		if (data_ok) $display("CHECK data: PASS");
		else $display("CHECK data: FAIL");
		if (data_ok) begin
			$display("tohost: PASS");
		end else begin
			$display("tohost: FAIL 0xdead");
			$display("TRAP: dataonly data");
		end
		$display("CYCLES=%0d", cyc);
		$finish;
	end
endmodule
""".replace("@MODULE@", spec["module"]) \
       .replace("@CLOCK@", spec["clock"]) \
       .replace("@RESET@", spec["reset"]) \
       .replace("@RST_A@", rst_assert) \
       .replace("@RST_D@", rst_deassert) \
       .replace("@DATA_IN@", spec["data_in"]) \
       .replace("@DATA_OUT@", spec["data_out"]) \
       .replace("@REQ@", spec["req"]) \
       .replace("@ACK@", spec["ack"]) \
       .replace("@IRQ@", spec["irq"]) \
       .replace("@MSB@", str(msb)) \
       .replace("@NPATS@", str(npats - 1)) \
       .replace("@N@", str(npats)) \
       .replace("@PAT_INITS@", pat_inits)


def generate_tb_text(spec):
    """Render the multisig TB text for spec (pure; writes nothing)."""
    _validate_common(spec)
    if spec["protocol"] == "handshake":
        return _handshake_tb(spec, _validate_handshake(spec))
    return _uart_tb(spec, _validate_uart(spec))


def generate_dataonly_tb_text(spec):
    """Render the data-check-alone TB text (handshake protocol only)."""
    _validate_common(spec)
    if spec["protocol"] not in _DATAONLY_PROTOCOLS:
        raise ValueError(
            "multisig_tb: data-only variant not defined for protocol %r "
            "(refusing to fake a comparison TB)" % (spec["protocol"],))
    return _dataonly_handshake_tb(spec, _validate_handshake(spec))


def _write_text(text, outdir, top):
    if not isinstance(outdir, str) or not outdir.strip():
        raise ValueError("multisig_tb: outdir must be a non-empty path")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.abspath(os.path.join(outdir, "%s.sv" % top))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def generate_tb(spec, outdir):
    """Validate spec, write <top>.sv into caller-chosen outdir. -> path."""
    _validate_common(spec)
    if spec["protocol"] == "handshake":
        v = _validate_handshake(spec)
    else:
        v = _validate_uart(spec)
    return _write_text(generate_tb_text(spec), outdir, v["top"])


def generate_dataonly_tb(spec, outdir):
    """Validate spec, write the data-only TB into outdir. -> path."""
    _validate_common(spec)
    if spec["protocol"] not in _DATAONLY_PROTOCOLS:
        raise ValueError(
            "multisig_tb: data-only variant not defined for protocol %r "
            "(refusing to fake a comparison TB)" % (spec["protocol"],))
    v = _validate_handshake(spec)
    top = "tb_%s_dataonly" % spec["module"]
    return _write_text(generate_dataonly_tb_text(spec), outdir, top)
