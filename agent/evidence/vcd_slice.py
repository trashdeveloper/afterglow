#!/usr/bin/env python3
"""Task B1: VCD-as-evidence (plan item 11, Wave B SEE MORE).

EvidencePack extension: enables --trace on a scratch build, slices the
VCD window around the first FAIL cycle, and summarizes the top toggling
signals (ranked by transition count in window).

Interfaces:
  find_fail_time(sim_log_text, oracle_trap) -> fail anchor dict
  slice_vcd(trace, fail_time, window_cycles, time_per_cycle) -> window dict
  rank_toggles(sliced, top_k) -> [(signal, transitions)] desc
  summarize_slice(trace, fail_time, ...) -> slice + rankings + summary str
  build_trace_run(tree, case, outdir, seed) -> {vcd, log, rc, ...}
  build_evidence_pack(case, tree, ..., enable_vcd) -> EvidencePack dict

VCD parsing is REUSED from agent/discover/protocol.py (imported, never
duplicated): slice_vcd takes a protocol.parse_vcd trace dict.

Default-off contract (Wave B header: every addition is additive +
default-off): build_evidence_pack with enable_vcd=False (the default)
returns {"vcd_enabled": False, "vcd_slice": None} WITHOUT reading any
file, building anything, or touching the loop. The loop (agent/loop.py)
is NOT edited by this task; prompt builders opt in by passing
enable_vcd=True with artifacts.

Honesty contract (mirrors protocol.py: never invent signal/time):
- Empty sim log, or a log with no FAIL marker, raises ValueError --
  a passing run has no FAIL point, so callers analyzing a passing end
  pass fail_time explicitly (documented, never guessed).
- TRAP is the primary fail anchor (matches the collector); a
  "tohost: FAIL" line is the fallback. Per-line "cycle N" stamps are
  used when present, else fail_cycle is None (loudly None, not zero).
- Slice bounds clamp at 0; samples outside the window are excluded,
  never smeared in. A window with zero toggles reports zero loudly.
- Vector/x/z values compare as strings; any change of level counts.

Time model: VCD stamps are raw time units. window_cycles converts to
time via time_per_cycle (default 10: every repo TB under sim/tb/ runs
`always #5 clk` with no `timescale, i.e. 10 time units per cycle at
Verilator's default 1ns/1ns). Non-repo benches MUST pass it explicitly.
"""
import os
import re
import subprocess

from discover import protocol

DEFAULT_WINDOW_CYCLES = 10
DEFAULT_TIME_PER_CYCLE = 10
DEFAULT_TOP_K = 8

_CYCLES_RE = re.compile(r"CYCLES=(\d+)")
_LINE_CYCLE_RE = re.compile(r"(?:cycles?|cyc)\s*[=:\s]?\s*(\d+)",
                            re.IGNORECASE)


def _clock_like(name):
    """True for free-running clock nets (excluded from *_no_clk ranks)."""
    short = str(name).split(".")[-1].lower()
    return short in ("clk", "clock") or short.endswith("_clk")


def find_fail_time(sim_log_text, oracle_trap="TRAP:"):
    """Locate the first FAIL anchor in a sim log.

    Returns {"log_lineno" (1-based), "marker" (line text),
    "fail_cycle" (per-line stamp or None), "end_cycle" (CYCLES= or None)}.
    Raises ValueError on empty logs or logs with no FAIL marker.
    """
    if not isinstance(sim_log_text, str) or not sim_log_text.strip():
        raise ValueError("empty sim log: refusing to invent a fail point")
    lines = sim_log_text.splitlines()
    idx = None
    for i, ln in enumerate(lines):
        if oracle_trap in ln:
            idx = i
            break
    if idx is None:
        for i, ln in enumerate(lines):
            if "tohost: FAIL" in ln:
                idx = i
                break
    if idx is None:
        raise ValueError(
            "no FAIL marker (%r or 'tohost: FAIL') in sim log: "
            "refusing to invent a fail point (pass fail_time explicitly "
            "for passing runs)" % (oracle_trap,))
    marker = lines[idx]
    m = _LINE_CYCLE_RE.search(marker)
    fail_cycle = int(m.group(1)) if m else None
    m = _CYCLES_RE.search(sim_log_text)
    end_cycle = int(m.group(1)) if m else None
    return {"log_lineno": idx + 1, "marker": marker,
            "fail_cycle": fail_cycle, "end_cycle": end_cycle}


def slice_vcd(trace, fail_time, window_cycles=DEFAULT_WINDOW_CYCLES,
              time_per_cycle=DEFAULT_TIME_PER_CYCLE):
    """Slice per-signal samples to [fail-half, fail+half] (VCD units).

    half = window_cycles * time_per_cycle; start clamps at 0.
    transitions counts value changes with timestamp inside the window
    (a change AT the start edge counts against the pre-window level).
    """
    if not isinstance(trace, dict) or not trace.get("samples"):
        raise ValueError("trace has no signals: refusing to slice emptiness")
    if not isinstance(fail_time, (int, float)) or fail_time < 0:
        raise ValueError("fail_time must be a non-negative VCD timestamp, "
                         "got %r" % (fail_time,))
    if (not isinstance(window_cycles, (int, float))
            or window_cycles <= 0):
        raise ValueError("window_cycles must be positive, got %r"
                         % (window_cycles,))
    if (not isinstance(time_per_cycle, (int, float))
            or time_per_cycle <= 0):
        raise ValueError("time_per_cycle must be positive, got %r"
                         % (time_per_cycle,))
    half = window_cycles * time_per_cycle
    start = max(0, fail_time - half)
    end = fail_time + half
    signals = {}
    for name, samples in trace["samples"].items():
        ordered = sorted(samples)
        level = None
        kept = []
        transitions = 0
        for (t, v) in ordered:
            if t < start:
                level = v
                continue
            if t > end:
                break
            kept.append((t, v))
            if level is not None and v != level:
                transitions += 1
            level = v
        signals[name] = {"samples": kept, "transitions": transitions}
    return {"start": start, "end": end, "fail_time": fail_time,
            "window_cycles": window_cycles,
            "time_per_cycle": time_per_cycle,
            "signals": signals, "n_signals": len(signals),
            "t_end": trace.get("t_end")}


def rank_toggles(sliced, top_k=None, skip_clocks=True):
    """Rank sliced signals by transition count, desc (ties by name).

    Zero-transition signals are excluded (silence is not evidence).
    skip_clocks drops free-running clock nets; top_k caps the list.
    """
    items = [(name, info["transitions"])
             for name, info in sliced["signals"].items()
             if info["transitions"] > 0
             and not (skip_clocks and _clock_like(name))]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    if top_k is not None:
        if top_k <= 0:
            raise ValueError("top_k must be positive, got %r" % (top_k,))
        items = items[:top_k]
    return items


def summarize_slice(trace, fail_time,
                    window_cycles=DEFAULT_WINDOW_CYCLES,
                    time_per_cycle=DEFAULT_TIME_PER_CYCLE,
                    top_k=DEFAULT_TOP_K):
    """One-call slice + rank + human summary (EvidencePack section body)."""
    sliced = slice_vcd(trace, fail_time, window_cycles, time_per_cycle)
    ranking = rank_toggles(sliced, top_k=None, skip_clocks=False)
    ranking_no_clk = rank_toggles(sliced, top_k=top_k, skip_clocks=True)
    if ranking_no_clk:
        top_str = ", ".join("%s(%d)" % kv for kv in ranking_no_clk)
    else:
        top_str = "none"
    summary = ("VCD slice [t=%s..%s] around fail t=%s (%s cycles x %s "
               "units): %d signal(s) toggled (clocks excluded); top: %s."
               % (sliced["start"], sliced["end"], fail_time,
                  window_cycles, time_per_cycle,
                  len(ranking_no_clk), top_str))
    return {"slice": sliced, "ranking": ranking,
            "ranking_no_clk": ranking_no_clk, "top_k": top_k,
            "summary": summary}


_TRACE_WRAPPER = """\
// tb_trace_top (Task B1 scratch wrapper, generated): instantiates the
// case TB top and dumps the whole hierarchy below it. Assumes a
// portless TB top (true for every TB under sim/tb/).
module tb_trace_top;
\t%s tb_inst();
\tinitial begin
\t\t$dumpfile("trace.vcd");
\t\t$dumpvars(0, tb_inst);
\tend
endmodule
"""


def build_trace_run(tree, case, outdir, seed=42, timeout=900):
    """Trace-enabled scratch build+run: --trace, separate obj dir.

    Writes a generated wrapper top into outdir, builds with
    `--trace` into <tree>/obj_afterglow_<name>_trace (never the normal
    obj dir, never the repo), runs the binary with cwd=outdir so
    trace.vcd lands there. Returns {"vcd", "log", "rc", "cycles",
    "tohost", "seed", "binary"}. Raises loudly on missing sources,
    build failure, or a missing VCD. Sim-only, stdlib-only, no API.
    """
    for key in ("files", "top", "tb", "name"):
        if key not in case:
            raise ValueError("case missing key %r" % (key,))
    if not os.path.isdir(str(tree)):
        raise ValueError("tree not a directory: %r" % (tree,))
    os.makedirs(outdir, exist_ok=True)
    simdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "sim")
    tb_path = os.path.join(simdir, "tb", case["tb"])
    if not os.path.isfile(tb_path):
        raise FileNotFoundError("missing testbench: %s" % tb_path)
    srcs = []
    for rel in case["files"]:
        p = os.path.join(str(tree), rel)
        if not os.path.isfile(p):
            raise FileNotFoundError("missing RTL source: %s" % p)
        srcs.append(p)
    wrapper_path = os.path.join(outdir, "tb_trace_top.sv")
    with open(wrapper_path, "w", encoding="utf-8") as fh:
        fh.write(_TRACE_WRAPPER % case["top"])
    mdir = os.path.join(str(tree),
                        "obj_afterglow_%s_trace" % case["name"])
    binary = os.path.join(mdir, "Vtb_trace_top")
    cmd = ["verilator", "--binary", "--timing", "--trace", "-j", "4",
           "--top-module", "tb_trace_top", "-Mdir", mdir,
           "-o", "Vtb_trace_top"]
    cmd += srcs + [tb_path, wrapper_path] + list(case.get("vflags", []))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          timeout=timeout, cwd=str(tree))
    if proc.returncode != 0 or not os.path.isfile(binary):
        raise RuntimeError("trace build failed (exit %d):\n%s"
                           % (proc.returncode, proc.stdout[-2000:]))
    try:
        run = subprocess.run(
            [binary, "+seed=%d" % int(seed)], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=300, cwd=outdir)
    except subprocess.TimeoutExpired:
        raise RuntimeError("trace run timed out after 300s")
    log = run.stdout
    vcd_path = os.path.join(outdir, "trace.vcd")
    if not os.path.isfile(vcd_path):
        raise RuntimeError("trace run produced no trace.vcd; log tail:\n%s"
                           % log[-2000:])
    m = _CYCLES_RE.search(log)
    cycles = int(m.group(1)) if m else None
    tohost = None
    for ln in log.splitlines():
        if "tohost:" in ln:
            tohost = ln.split("tohost:", 1)[1].strip()
    return {"vcd": vcd_path, "log": log, "rc": run.returncode,
            "cycles": cycles, "tohost": tohost, "seed": int(seed),
            "binary": binary}


def build_evidence_pack(case, tree, sim_log=None, vcd_text=None,
                        vcd_path=None, enable_vcd=False,
                        fail_time=None, oracle_trap="TRAP:",
                        window_cycles=DEFAULT_WINDOW_CYCLES,
                        time_per_cycle=DEFAULT_TIME_PER_CYCLE,
                        top_k=DEFAULT_TOP_K):
    """EvidencePack dict with a default-off VCD slice section.

    enable_vcd=False (default): returns {"vcd_enabled": False,
    "vcd_slice": None, ...} WITHOUT reading files or building --
    existing loop/prompt behavior is byte-identical.
    enable_vcd=True: requires (sim_log or fail_time) AND (vcd_text or
    vcd_path); parses via protocol.parse_vcd (reused, never
    duplicated); fail_time explicit wins, else find_fail_time(sim_log).
    """
    pack = {"case": (case or {}).get("name", "?"),
            "vcd_enabled": bool(enable_vcd), "vcd_slice": None}
    if not enable_vcd:
        return pack
    if vcd_text is None and vcd_path is not None:
        with open(vcd_path, encoding="utf-8") as fh:
            vcd_text = fh.read()
    if vcd_text is None:
        raise ValueError("enable_vcd=True needs vcd_text or vcd_path: "
                         "refusing to report evidence without a waveform")
    trace = protocol.parse_vcd(vcd_text)
    if fail_time is None:
        if sim_log is None:
            raise ValueError("enable_vcd=True needs sim_log or fail_time: "
                             "refusing to invent a fail point")
        # Log stamps are cycles; VCD stamps are time units.
        found = find_fail_time(sim_log, oracle_trap)
        cycle = found["fail_cycle"]
        if cycle is None:
            cycle = found["end_cycle"]
        if cycle is None:
            raise ValueError("sim log carries no cycle stamp and no "
                             "CYCLES=: pass fail_time explicitly")
        fail_time = cycle * time_per_cycle
    section = summarize_slice(trace, fail_time, window_cycles,
                              time_per_cycle, top_k)
    section["fail_time"] = fail_time
    pack["vcd_slice"] = section
    return pack
