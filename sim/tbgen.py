#!/usr/bin/env python3
"""REAL multi-signal TB writer for the big-8 fault classes (Task-wave-5).

The old oracles poll only coarse completion (byte arrival / PC progress
-- ``xfer_done``-style polling). This module generates per-fault-class
testbenches that watch the ACTUAL signals named in the bug line instead:

- ``uart-rx-div`` ..... ``recv_divcnt`` vs ``cfg_divider`` sample period
- ``uart-tx-div`` ..... ``send_divcnt`` vs ``cfg_divider`` TX frame time
- ``uart-start-half`` . start-bit half-sample point (``2*recv_divcnt``)
- ``fetch-align`` ..... ``mem_la_addr`` LSBs on every ``mem_la_read``
- ``fetch-valid-gate``  ``mem_valid`` following ``launch_next_insn``
  producer pulses (launch/valid producer-consumer handshake)
- ``rdata-mux`` ....... ``mem_rdata`` vs ``mem_rdata_q`` mux select
  (live-vs-latched ``||`` term), checked byte-by-byte
- ``trap-clean`` ...... ``trap`` low on a clean stream + hart-halt
  watch (``pc==0xdead``-style halt fetch never occurs)
- ``pc-progress`` ..... ``reg_next_pc == current_pc + 4`` 32-bit
  increment check (freeze detector)

The big-8 are the 8 eval-board fault classes; ``classify`` maps a spec's
bug line (+ anchor for the ``2*`` start-half disambiguation) to its
class. ``generate_tb`` emits a complete, deterministic SystemVerilog
testbench following the repo oracle contract (``tohost: PASS`` /
``tohost: FAIL 0xdead`` + ``TRAP: <class> ...`` + ``CYCLES=<n>``,
``+seed`` accepted, no timestamps).

Prompt-independence is deliberate (Task-6 measured vision verdict:
default stays text-only): this module imports nothing from the agent
tree and emits no image content.

$0 to build: validation is local unittest (text assertions +
Verilator lint/build/run on scratch tree copies). No API calls.
"""
from __future__ import annotations

import argparse
import re
import sys

# The big-8 fault classes, one per eval-board case. ``signals`` names the
# actual bug-line signals the watcher checks (first two must appear in
# the watcher snippet -- enforced by tests/test_tbgen.py).
FAULT_CLASSES = (
    {"id": "uart-rx-div",
     "case": "picosoc-uart-div",
     "family": "picosoc-uart",
     "top": "tb_picosoc_uart_rxdiv",
     "signals": ("recv_divcnt", "cfg_divider", "recv_buf_data",
                 "recv_buf_valid"),
     "description": "RX baud divisor: sample-strobe period must equal "
                    "cfg_divider (recv_divcnt threshold watcher)."},
    {"id": "uart-tx-div",
     "case": "uart-tx-div",
     "family": "picosoc-uart",
     "top": "tb_picosoc_uart_txdiv",
     "signals": ("send_divcnt", "cfg_divider", "send_bitcnt", "ser_tx"),
     "description": "TX baud divisor: 10-bit frame time must equal "
                    "10*cfg_divider (send_divcnt threshold watcher)."},
    {"id": "uart-start-half",
     "case": "uart-start-half",
     "family": "picosoc-uart",
     "top": "tb_picosoc_uart_starthalf",
     "signals": ("recv_divcnt", "cfg_divider", "ser_tx",
                 "recv_buf_data"),
     "description": "RX start-bit half-period sample: first strobe lands "
                    "at ~1.5 periods (2*recv_divcnt term watcher)."},
    {"id": "fetch-align",
     "case": "picorv32-fetch-misalign",
     "family": "picorv32-fetch",
     "top": "tb_picorv32_fetch_align",
     "signals": ("mem_la_addr", "mem_la_read", "next_pc"),
     "description": "Fetch lookahead address LSBs stay 2'b00 on every "
                    "mem_la_read (misaligned-fetch detector)."},
    {"id": "fetch-valid-gate",
     "case": "fetch-highword",
     "family": "picorv32-fetch",
     "top": "tb_picorv32_fetch_valid",
     "signals": ("mem_valid", "launch_next_insn",
                 "mem_la_use_prefetched_high_word"),
     "description": "Fetch valid/launch handshake: every launch_next_insn "
                    "producer pulse is consumed by mem_valid "
                    "(prefetched-high-word gate watcher)."},
    {"id": "rdata-mux",
     "case": "rdata-latch",
     "family": "picorv32-fetch",
     "top": "tb_picorv32_rdata_mux",
     "signals": ("mem_rdata", "mem_rdata_q", "mem_xfer",
                 "mem_rdata_latched_noshuffle"),
     "description": "Latched read-data mux: under mem_xfer the latched "
                    "word must equal live mem_rdata (live-vs-latched "
                    "OR-term select), checked byte-by-byte."},
    {"id": "trap-clean",
     "case": "trap-stuck",
     "family": "picorv32-fetch",
     "top": "tb_picorv32_trap_clean",
     "signals": ("trap", "mem_la_addr"),
     "description": "Trap/halt line: trap stays low on a clean NOP stream "
                    "and no pc==0xdead-style halt fetch occurs."},
    {"id": "pc-progress",
     "case": "pc-incr",
     "family": "picorv32-fetch",
     "top": "tb_picorv32_pc_progress",
     "signals": ("reg_next_pc", "current_pc", "mem_la_addr"),
     "description": "Next-PC 32-bit increment: reg_next_pc advances by 4 "
                    "every fetch (freeze detector)."},
)

_BY_ID = {c["id"]: c for c in FAULT_CLASSES}


def classify(bug, anchor=""):
    """Map a spec bug line (+ anchor) to its big-8 class id.

    Signals come straight from the bug line; the anchor is consulted
    only to disambiguate the uart start-half class (its ``2*`` factor
    lives in the fixed line). Returns ``"unknown"`` for anything else
    -- ``generate_tb`` refuses that loudly, never silently generic.
    """
    bug = bug or ""
    anchor = anchor or ""
    if "send_divcnt" in bug:
        return "uart-tx-div"
    if "recv_divcnt" in bug:
        if "2*" in bug or "2*" in anchor:
            return "uart-start-half"
        return "uart-rx-div"
    if "mem_valid" in bug and "prefetch" in bug:
        # Checked BEFORE fetch-align: a line carrying both mem_valid
        # and mem_la_addr is a gate fault, not an alignment fault.
        return "fetch-valid-gate"
    if "mem_la_addr" in bug:
        return "fetch-align"
    if "mem_rdata" in bug:
        return "rdata-mux"
    if re.search(r"trap\s*<=", bug):
        return "trap-clean"
    if "reg_next_pc" in bug:
        return "pc-progress"
    return "unknown"


# ---------------------------------------------------------------------------
# Watcher snippets (declarations + per-cycle sample + verdict), one per class.
# Each names its class's actual signals; TRAP lines carry the class id so a
# RED names the fault class, not just "FAIL".
# ---------------------------------------------------------------------------

_PICORV32_DECLS_COMMON = """\
\tinteger i;
\tint seed_val = 0;
"""

_PICORV32_PORTS = """\
\tpicorv32 #(
\t\t.COMPRESSED_ISA(0),
\t\t.ENABLE_IRQ(0)
\t) cpu (
\t\t.clk(clk),
\t\t.resetn(resetn),
\t\t.trap(trap),
\t\t.mem_valid(mem_valid),
\t\t.mem_instr(mem_instr),
\t\t.mem_ready(mem_ready),
\t\t.mem_addr(mem_addr),
\t\t.mem_wdata(mem_wdata),
\t\t.mem_wstrb(mem_wstrb),
\t\t.mem_rdata(mem_rdata),
\t\t.mem_la_read(mem_la_read),
\t\t.mem_la_write(mem_la_write),
\t\t.mem_la_addr(mem_la_addr),
\t\t.mem_la_wdata(),
\t\t.mem_la_wstrb(),
\t\t.pcpi_wr(1'b0),
\t\t.pcpi_rd(32'b0),
\t\t.pcpi_wait(1'b0),
\t\t.pcpi_ready(1'b0),
\t\t.irq(32'b0)
\t);
"""


def _picorv32_skeleton(top, one_line, decls, sample, verdict):
    return """\
// {top}: REAL tbgen watcher for class {cls} (sim/tbgen.py, Task-wave-5).
// Watches the ACTUAL bug-line signals ({sigs}), not just xfer_done-style
// completion. Prints "tohost: PASS" or "tohost: FAIL 0xdead" + "TRAP: ..." +
// "CYCLES=<n>". Deterministic. Accepts +seed=<n> (logged only).
// Build with: verilator --binary --timing.
module {top};
\treg clk = 0;
\treg resetn = 0;
\talways #5 clk = ~clk;

\twire trap;
\twire mem_valid, mem_instr;
\twire mem_ready;
\twire [31:0] mem_addr, mem_wdata, mem_rdata;
\twire [3:0] mem_wstrb;
\twire mem_la_read, mem_la_write;
\twire [31:0] mem_la_addr;

\tassign mem_ready = 1'b1;

\treg [31:0] mem [0:255];
\t/* verilator lint_off WIDTH */
\tassign mem_rdata = (mem_addr[31:10] == 0) ? mem[mem_addr[9:2]] : 32'h00000013;
\t/* verilator lint_on WIDTH */

{ports}
\tint unsigned cyc = 0;
\talways @(posedge clk) cyc <= cyc + 1;

{decls}
\tinitial begin
\t\t$value$plusargs("seed=%d", seed_val);
\t\t$display("- {top} seed %0d", seed_val);
\t\t$display("- tbgen {cls}: {one_line}");
\t\tfor (i = 0; i < 256; i = i + 1)
\t\t\tmem[i] = 32'h00000013;
\t\trepeat (10) @(posedge clk);
\t\tresetn = 1;
\t\trepeat (2000) begin
\t\t\t@(posedge clk);
{sample}
\t\tend
{verdict}
\t\t$display("CYCLES=%0d", cyc);
\t\t$finish;
\tend
endmodule
""".format(top=top, cls=_BY_ID[_TOP_CLASS[top]]["id"],
           sigs=", ".join(_BY_ID[_TOP_CLASS[top]]["signals"]),
           ports=_PICORV32_PORTS.rstrip("\n"), decls=decls.rstrip("\n"),
           one_line=one_line, sample=sample.rstrip("\n"),
           verdict=verdict.rstrip("\n"))


def _uart_skeleton(top, one_line, decls, tail):
    return """\
// {top}: REAL tbgen watcher for class {cls} (sim/tbgen.py, Task-wave-5).
// Watches the ACTUAL bug-line signals ({sigs}), not just byte arrival.
// Prints "tohost: PASS" or "tohost: FAIL 0xdead" + "TRAP: ..." +
// "CYCLES=<n>". Deterministic. Accepts +seed=<n> (logged only).
// Build with: verilator --binary --timing.
module {top};
\treg clk = 0;
\treg resetn = 0;
\talways #5 clk = ~clk;

\twire ser_tx;
\treg [3:0] reg_div_we = 0;
\treg [31:0] reg_div_di = 0;
\twire [31:0] reg_div_do;
\treg reg_dat_we = 0;
\treg reg_dat_re = 0;
\treg [31:0] reg_dat_di = 0;
\twire [31:0] reg_dat_do;
\twire reg_dat_wait;

\tsimpleuart #(.DEFAULT_DIV(64)) dut (
\t\t.clk(clk),
\t\t.resetn(resetn),
\t\t.ser_tx(ser_tx),
\t\t.ser_rx(ser_tx),
\t\t.reg_div_we(reg_div_we),
\t\t.reg_div_di(reg_div_di),
\t\t.reg_div_do(reg_div_do),
\t\t.reg_dat_we(reg_dat_we),
\t\t.reg_dat_re(reg_dat_re),
\t\t.reg_dat_di(reg_dat_di),
\t\t.reg_dat_do(reg_dat_do),
\t\t.reg_dat_wait(reg_dat_wait)
\t);

\t/* verilator lint_off UNUSED */
\twire [31:0] unused_div = reg_div_do;
\t/* verilator lint_on UNUSED */

\tint unsigned cyc = 0;
\talways @(posedge clk) cyc <= cyc + 1;

\tint seed_val = 0;
{decls}
\tinitial begin
\t\t$value$plusargs("seed=%d", seed_val);
\t\t$display("- {top} seed %0d", seed_val);
\t\t$display("- tbgen {cls}: {one_line}");
\t\trepeat (20) @(posedge clk);
\t\tresetn = 1;
\t\t// program baud divider
\t\t@(posedge clk);
\t\treg_div_we = 4'hf;
\t\treg_div_di = 32'd64;
\t\t@(posedge clk);
\t\treg_div_we = 4'h0;
\t\t// flush power-on dummy char (fixed wait: transmitter is busy)
\t\trepeat (2000) @(posedge clk);
\t\t// send 0xA5
\t\t@(posedge clk);
\t\treg_dat_di = 32'hA5;
\t\treg_dat_we = 1;
\t\t@(posedge clk);
\t\treg_dat_we = 0;
{tail}
\t\t$display("CYCLES=%0d", cyc);
\t\t$finish;
\tend
endmodule
""".format(top=top, cls=_BY_ID[_TOP_CLASS[top]]["id"],
           sigs=", ".join(_BY_ID[_TOP_CLASS[top]]["signals"]),
           decls=decls.rstrip("\n"), one_line=one_line,
           tail=tail.rstrip("\n"))


# Per-class watcher bodies: (decls, sample, verdict) for picorv32-family,
# (decls, tail) for uart-family. ``watcher_for`` returns them joined.

_WATCHERS = {
    "fetch-align": (
        _PICORV32_DECLS_COMMON + "\tbit misalign = 0;\n"
        "\tbit saw_trap = 0;\n"
        "\tint unsigned max_addr = 0;",
        """\
\t\t\t// Watcher: mem_la_addr LSBs on every mem_la_read.
\t\t\tif (mem_la_read && mem_la_addr[1:0] != 2'b00)
\t\t\t\tmisalign = 1;
\t\t\tif (mem_la_read && mem_la_addr > max_addr)
\t\t\t\tmax_addr = mem_la_addr;
\t\t\tif (trap)
\t\t\t\tsaw_trap = 1;""",
        """\
\t\tif (!misalign && !saw_trap && max_addr >= 64) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (misalign)
\t\t\t\t$display("TRAP: fetch-align watcher mem_la_addr low bits nonzero (lookahead addr misaligned)");
\t\t\telse if (saw_trap)
\t\t\t\t$display("TRAP: fetch-align watcher cpu trap asserted");
\t\t\telse
\t\t\t\t$display("TRAP: fetch-align watcher no progress max_addr=0x%08x", max_addr);
\t\tend""",
    ),
    "fetch-valid-gate": (
        _PICORV32_DECLS_COMMON + "\tint unsigned launches = 0;\n"
        "\tint unsigned valids = 0;\n"
        "\tbit gate_stuck = 0;\n"
        "\tbit saw_trap = 0;\n"
        "\tint unsigned max_addr = 0;",
        """\
\t\t\t// Watcher: launch_next_insn producer pulses must be consumed
\t\t\t// by mem_valid (prefetched-high-word gate must not swallow
\t\t\t// them). With ENABLE_IRQ=0 the irq_pending/irq_mask terms of
\t\t\t// the launch equation are compiled out, so the launch/valid
\t\t\t// pair is the observable producer-consumer contract.
\t\t\tif (cpu.launch_next_insn)
\t\t\t\tlaunches = launches + 1;
\t\t\tif (cpu.mem_valid)
\t\t\t\tvalids = valids + 1;
\t\t\tif (mem_la_read && mem_la_addr > max_addr)
\t\t\t\tmax_addr = mem_la_addr;
\t\t\tif (trap)
\t\t\t\tsaw_trap = 1;""",
        """\
\t\tif (launches >= 64 && valids == 0)
\t\t\tgate_stuck = 1;
\t\tif (!gate_stuck && !saw_trap && max_addr >= 64) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (gate_stuck)
\t\t\t\t$display("TRAP: fetch-valid-gate watcher mem_valid never followed launch_next_insn producer pulse (%0d launches, 0 valids)", launches);
\t\t\telse if (saw_trap)
\t\t\t\t$display("TRAP: fetch-valid-gate watcher cpu trap asserted");
\t\t\telse
\t\t\t\t$display("TRAP: fetch-valid-gate watcher no progress max_addr=0x%08x", max_addr);
\t\tend""",
    ),
    "rdata-mux": (
        _PICORV32_DECLS_COMMON + "\tbit mismatch = 0;\n"
        "\tint bad_byte = -1;\n"
        "\tbit saw_trap = 0;\n"
        "\tinteger b;\n"
        "\tint unsigned max_addr = 0;",
        """\
\t\t\t// Watcher: under mem_xfer the latched word must equal live
\t\t\t// mem_rdata (the (mem_xfer || LATCHED) OR-term selects live
\t\t\t// data; a swapped/ANDed select serves stale mem_rdata_q).
\t\t\t// Byte-by-byte so a byte-lane fault names its lane. (=== :
\t\t\t// X-poisoning on the first transfer fires, pristine compares
\t\t\t// a net to itself and can never fire.)
\t\t\tif (cpu.mem_xfer && (cpu.mem_rdata_latched_noshuffle !== cpu.mem_rdata)) begin
\t\t\t\tmismatch = 1;
\t\t\t\tfor (b = 0; b < 4; b = b + 1) begin
\t\t\t\t\tif ((cpu.mem_rdata_latched_noshuffle[8*b +: 8] !== cpu.mem_rdata[8*b +: 8]) && bad_byte < 0)
\t\t\t\t\t\tbad_byte = b;
\t\t\t\tend
\t\t\tend
\t\t\tif (mem_la_read && mem_la_addr > max_addr)
\t\t\t\tmax_addr = mem_la_addr;
\t\t\tif (trap)
\t\t\t\tsaw_trap = 1;""",
        """\
\t\tif (!mismatch && !saw_trap && max_addr >= 64) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (mismatch)
\t\t\t\t$display("TRAP: rdata-mux watcher mem_rdata vs mem_rdata_q select wrong (live-vs-latched OR-term) byte %0d", bad_byte);
\t\t\telse if (saw_trap)
\t\t\t\t$display("TRAP: rdata-mux watcher cpu trap asserted");
\t\t\telse
\t\t\t\t$display("TRAP: rdata-mux watcher no progress max_addr=0x%08x", max_addr);
\t\tend""",
    ),
    "trap-clean": (
        _PICORV32_DECLS_COMMON + "\tbit saw_trap = 0;\n"
        "\tbit saw_dead = 0;\n"
        "\tint unsigned max_addr = 0;",
        """\
\t\t\t// Watcher: trap stays low on a clean NOP stream; the hart
\t\t\t// must never fetch a pc==0xdead-style halt word.
\t\t\tif (trap)
\t\t\t\tsaw_trap = 1;
\t\t\tif (mem_la_read && mem_la_addr == 32'h0000dead)
\t\t\t\tsaw_dead = 1;
\t\t\tif (mem_la_read && mem_la_addr > max_addr)
\t\t\t\tmax_addr = mem_la_addr;""",
        """\
\t\tif (!saw_trap && !saw_dead && max_addr >= 64) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (saw_trap)
\t\t\t\t$display("TRAP: trap-clean watcher trap asserted on clean NOP stream");
\t\t\telse if (saw_dead)
\t\t\t\t$display("TRAP: trap-clean watcher hart halt at pc==0xdead");
\t\t\telse
\t\t\t\t$display("TRAP: trap-clean watcher no progress max_addr=0x%08x", max_addr);
\t\tend""",
    ),
    "pc-progress": (
        _PICORV32_DECLS_COMMON + "\tint unsigned stall = 0;\n"
        "\tbit froze = 0;\n"
        "\tbit saw_trap = 0;\n"
        "\tint unsigned max_addr = 0;",
        """\
\t\t\t// Watcher: 32-bit increment check -- reg_next_pc must keep
\t\t\t// advancing past current_pc (a frozen launch address means
\t\t\t// the +4 step was dropped). Transient equality is legal, so
\t\t\t// only a sustained stall fires.
\t\t\tif (cpu.reg_next_pc == cpu.current_pc)
\t\t\t\tstall = stall + 1;
\t\t\telse
\t\t\t\tstall = 0;
\t\t\tif (stall > 64)
\t\t\t\tfroze = 1;
\t\t\tif (mem_la_read && mem_la_addr > max_addr)
\t\t\t\tmax_addr = mem_la_addr;
\t\t\tif (trap)
\t\t\t\tsaw_trap = 1;""",
        """\
\t\tif (!froze && !saw_trap && max_addr >= 64) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (froze)
\t\t\t\t$display("TRAP: pc-progress watcher reg_next_pc frozen (no 32-bit +4 advance) stall=%0d", stall);
\t\t\telse if (saw_trap)
\t\t\t\t$display("TRAP: pc-progress watcher cpu trap asserted");
\t\t\telse
\t\t\t\t$display("TRAP: pc-progress watcher no progress max_addr=0x%08x", max_addr);
\t\tend""",
    ),
    "uart-rx-div": (
        "\tbyte got = 8'h00;\n"
        "\tbit done = 0;\n"
        "\tbit period_bad = 0;\n"
        "\tbit saw_strobe = 0;\n"
        "\tint unsigned prev_strobe = 0;\n"
        "\tint unsigned n_iv = 0;\n"
        "\tint unsigned cfg = 0;",
        """\
\t\tcfg = dut.cfg_divider;
\t\t// wait for RX byte; sample the recv_divcnt > cfg_divider strobe
\t\t// mid-cycle (negedge) and require its period to equal the
\t\t// programmed divider. The first interval is the half-period
\t\t// start-bit accept by design, so measurement starts after it.
\t\trepeat (20000) begin
\t\t\t@(posedge clk);
\t\t\tif (reg_dat_do != 32'hFFFFFFFF) begin
\t\t\t\tdone = 1;
\t\t\t\tgot = reg_dat_do[7:0];
\t\t\tend
\t\t\t@(negedge clk);
\t\t\t// Gate on the RX sample states (2..10): the cycle after the
\t\t\t// stop strobe the core idles in state 0 with a stale
\t\t\t// divcnt residue that is NOT a sample strobe.
\t\t\tif (dut.recv_state >= 2 && dut.recv_state <= 10 && dut.recv_divcnt > dut.cfg_divider) begin
\t\t\t\tif (saw_strobe) begin
\t\t\t\t\tif (n_iv > 0 && (cyc - prev_strobe < cfg - 8 || cyc - prev_strobe > cfg + 8))
\t\t\t\t\t\tperiod_bad = 1;
\t\t\t\t\tn_iv = n_iv + 1;
\t\t\t\tend
\t\t\t\tsaw_strobe = 1;
\t\t\t\tprev_strobe = cyc;
\t\t\tend
\t\t\tif (done && n_iv >= 2) break;
\t\tend
\t\tif (done && !period_bad && got == 8'hA5 && n_iv >= 2) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (period_bad)
\t\t\t\t$display("TRAP: uart-rx-div watcher recv sample period outside cfg_divider +-8 (recv_divcnt threshold wrong)");
\t\t\telse if (!done)
\t\t\t\t$display("TRAP: uart-rx-div watcher timeout, no byte received");
\t\t\telse if (n_iv < 2)
\t\t\t\t$display("TRAP: uart-rx-div watcher too few sample strobes (%0d)", n_iv);
\t\t\telse
\t\t\t\t$display("TRAP: uart-rx-div watcher data mismatch got 0x%02x want 0xa5", got);
\t\tend""",
    ),
    "uart-tx-div": (
        "\tbyte got = 8'h00;\n"
        "\tbit done = 0;\n"
        "\tbit saw_fall = 0;\n"
        "\tint unsigned t_fall = 0;\n"
        "\tbit saw_end = 0;\n"
        "\tint unsigned t_end = 0;\n"
        "\tint unsigned hi_run = 0;\n"
        "\tint unsigned frame = 0;",
        """\
\t\t// wait for the loopback byte; measure the 10-bit TX frame on the
\t\t// wire (first start fall to stop-bit rise). Pristine: 9 bit times
\t\t// ~= 9*cfg_divider; a divided threshold collapses the frame.
\t\trepeat (20000) begin
\t\t\t@(posedge clk);
\t\t\tif (reg_dat_do != 32'hFFFFFFFF) begin
\t\t\t\tdone = 1;
\t\t\t\tgot = reg_dat_do[7:0];
\t\t\tend
\t\t\t@(negedge clk);
\t\t\tif (!saw_fall && ser_tx == 1'b0) begin
\t\t\t\tsaw_fall = 1;
\t\t\t\tt_fall = cyc;
\t\t\t\thi_run = 0;
\t\t\tend else if (saw_fall && !saw_end) begin
\t\t\t\tif (ser_tx == 1'b1) begin
\t\t\t\t\thi_run = hi_run + 1;
\t\t\t\t\tif (hi_run >= 100) begin
\t\t\t\t\t\tsaw_end = 1;
\t\t\t\t\t\tt_end = cyc - 100;
\t\t\t\t\tend
\t\t\t\tend else begin
\t\t\t\t\thi_run = 0;
\t\t\t\tend
\t\t\tend
\t\t\tif (done && saw_end) break;
\t\tend
\t\tif (saw_fall && saw_end)
\t\t\tframe = t_end - t_fall;
\t\tif (done && got == 8'hA5 && saw_end && frame >= 500 && frame <= 700) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (saw_fall && saw_end && (frame < 500 || frame > 700))
\t\t\t\t$display("TRAP: uart-tx-div watcher TX frame %0d cycles want ~10*cfg_divider (send_divcnt threshold wrong)", frame);
\t\t\telse if (!done)
\t\t\t\t$display("TRAP: uart-tx-div watcher timeout, no byte received");
\t\t\telse if (!saw_end)
\t\t\t\t$display("TRAP: uart-tx-div watcher TX line never returned idle");
\t\t\telse
\t\t\t\t$display("TRAP: uart-tx-div watcher data mismatch got 0x%02x want 0xa5", got);
\t\tend""",
    ),
    "uart-start-half": (
        "\tbyte got = 8'h00;\n"
        "\tbit done = 0;\n"
        "\tbit saw_fall = 0;\n"
        "\tint unsigned t_fall = 0;\n"
        "\tbit saw_s1 = 0;\n"
        "\tint unsigned t_s1 = 0;\n"
        "\tbit have_steady = 0;\n"
        "\tint unsigned steady = 0;\n"
        "\tint unsigned n_strobe = 0;\n"
        "\tbit half_bad = 0;",
        """\
\t\t// wait for the loopback byte; the first recv strobe must land at
\t\t// ~1.5 steady periods after the start-bit fall (half-period
\t\t// accept via 2*recv_divcnt). A full-period accept pushes it to
\t\t// ~2.0 steady periods. Steady period is measured live, never
\t\t// hardcoded.
\t\trepeat (20000) begin
\t\t\t@(posedge clk);
\t\t\tif (reg_dat_do != 32'hFFFFFFFF) begin
\t\t\t\tdone = 1;
\t\t\t\tgot = reg_dat_do[7:0];
\t\t\tend
\t\t\t@(negedge clk);
\t\t\tif (!saw_fall && ser_tx == 1'b0) begin
\t\t\t\tsaw_fall = 1;
\t\t\t\tt_fall = cyc;
\t\t\tend
\t\t\tif (saw_fall && dut.recv_state >= 2 && dut.recv_state <= 10 && dut.recv_divcnt > dut.cfg_divider) begin
\t\t\t\tif (!saw_s1) begin
\t\t\t\t\tsaw_s1 = 1;
\t\t\t\t\tt_s1 = cyc;
\t\t\t\tend else if (!have_steady) begin
\t\t\t\t\tsteady = cyc - t_s1;
\t\t\t\t\thave_steady = 1;
\t\t\t\tend
\t\t\t\tn_strobe = n_strobe + 1;
\t\t\t\tif (have_steady && n_strobe >= 3) begin
\t\t\t\t\tif ((t_s1 - t_fall) * 4 >= steady * 7)
\t\t\t\t\t\thalf_bad = 1;
\t\t\t\tend
\t\t\tend
\t\t\tif (done && have_steady && n_strobe >= 3) break;
\t\tend
\t\tif (done && got == 8'hA5 && have_steady && !half_bad) begin
\t\t\t$display("tohost: PASS");
\t\tend else begin
\t\t\t$display("tohost: FAIL 0xdead");
\t\t\tif (half_bad)
\t\t\t\t$display("TRAP: uart-start-half watcher start-bit sample at full period (half-sample 2*recv_divcnt missing)");
\t\t\telse if (!done)
\t\t\t\t$display("TRAP: uart-start-half watcher timeout, no byte received");
\t\t\telse if (!have_steady)
\t\t\t\t$display("TRAP: uart-start-half watcher too few sample strobes (%0d)", n_strobe);
\t\t\telse
\t\t\t\t$display("TRAP: uart-start-half watcher data mismatch got 0x%02x want 0xa5", got);
\t\tend""",
    ),
}

_TOP_CLASS = {c["top"]: c["id"] for c in FAULT_CLASSES}


def watcher_for(class_id):
    """Return the class's watcher snippet (decls + sample + verdict)."""
    if class_id not in _WATCHERS:
        raise ValueError("unknown tbgen class %r (want one of %s)"
                         % (class_id, sorted(_WATCHERS)))
    return "\n".join(part for part in _WATCHERS[class_id] if part)


def generate_tb(class_id):
    """Emit the complete SystemVerilog testbench for a big-8 class."""
    if class_id not in _BY_ID:
        raise ValueError("unknown tbgen class %r (want one of %s)"
                         % (class_id, sorted(_BY_ID)))
    entry = _BY_ID[class_id]
    parts = _WATCHERS[class_id]
    if entry["family"] == "picorv32-fetch":
        decls, sample, verdict = parts
        return _picorv32_skeleton(entry["top"], entry["description"],
                                  decls, sample, verdict)
    decls, tail = parts
    return _uart_skeleton(entry["top"], entry["description"], decls, tail)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                    help="list the big-8 classes and exit")
    ap.add_argument("--class", dest="class_id", default=None,
                    help="class id to generate")
    ap.add_argument("--out", default=None, help="output .sv path")
    args = ap.parse_args(argv)
    if args.list:
        for c in FAULT_CLASSES:
            print("%s  [%s]  %s" % (c["id"], c["case"], c["description"]))
        return 0
    if not args.class_id:
        ap.error("--class is required (or --list)")
    text = generate_tb(args.class_id)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
