#!/usr/bin/env python3
"""Task A5: protocol-assertion checker (Wave A FIND BUGS).

A small stdlib-only library of signal-level protocol rules checked
against VCD waveform data parsed from a sim run:

- UART frame: start bit low, 8 data bits (LSB first), stop bit high.
- ready/valid handshake stability: valid held until ready.

Interfaces:
  parse_vcd(text) -> trace dict {timescale, signals, samples}
  parse_vcd_file(path) -> trace dict
  resolve_signal(trace, name) -> full hierarchical signal name
  check_trace(trace, rules) -> report {violations, all_clear, summary, ...}
  write_discovery_report(path, case_name, report) -> path

Honesty contract (mirrors fuzzer/blindspot/seeddiff: never invent signal):
- Malformed VCD (empty, missing $enddefinitions, unterminated $var,
  value change for an unknown id) raises ValueError loudly.
- A rule naming an unknown signal raises ValueError -- a missing
  signal is NEVER reported as agreement.
- An empty rule list raises ValueError (nothing to check is not clean).
- A truncated UART frame (trace ends before the stop-bit sample) is a
  violation (uart-frame-truncated), never silent.
- A clean trace yields an explicit ALL CLEAR report, never silence:
  all_clear is True AND summary carries the "ALL CLEAR" stamp.
"""

import os

SUPPORTED_KINDS = ("uart", "handshake")


def _parse_header_end(line, lineno):
    if "$end" not in line:
        raise ValueError("malformed VCD line %d: missing $end: %r"
                         % (lineno, line))


def parse_vcd(text):
    """Parse minimal VCD text into a trace dict.

    Understands: $timescale, $scope/$var/$upscope, $enddefinitions,
    #<time> stamps, scalar changes (0/1/x/z<id>), vector changes
    (b<bits> <id>), and $dumpvars/$dumpall markers (their entries are
    parsed as value changes at the current time). Anything else
    structural raises ValueError.

    Verilator aliases one net across scopes under a SINGLE id (e.g.
    tb.ser_tx, dut.ser_tx and dut.ser_rx on a loopback TB all share
    one id): every name declared for an id receives that id's changes,
    and all names sharing an id are listed in trace["aliases"] so the
    sharing is disclosed, never silent.
    """
    if not isinstance(text, str):
        raise TypeError("VCD text must be str, got %r" % (type(text),))
    if not text.strip():
        raise ValueError("empty VCD: refusing to report agreement "
                         "from no waveform")
    id_to_names = {}
    name_to_id = {}
    samples = {}
    timescale = None
    scope = []
    in_definitions = True
    seen_enddefinitions = False
    pending_timescale = None
    cur_time = 0
    max_time = 0
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        lineno = i + 1
        raw = lines[i]
        i += 1
        line = raw.strip()
        if not line:
            continue
        if line.startswith("$timescale"):
            # One-line ("$timescale 1ns $end") or two-line form.
            if "$end" in line:
                timescale = line.split("$timescale", 1)[1].split("$end")[0].strip()
            else:
                pending_timescale = True
            continue
        if pending_timescale:
            timescale = line.split("$end")[0].strip()
            pending_timescale = None
            continue
        if line.startswith("$scope"):
            _parse_header_end(line, lineno)
            parts = line.split()
            if len(parts) >= 3:
                scope.append(parts[2])
            continue
        if line.startswith("$upscope"):
            _parse_header_end(line, lineno)
            if scope:
                scope.pop()
            continue
        if line.startswith("$var"):
            buf = line
            while "$end" not in buf:
                if i >= n:
                    raise ValueError(
                        "malformed VCD line %d: unterminated $var: %r"
                        % (lineno, buf))
                buf += " " + lines[i].strip()
                i += 1
            parts = buf.split()
            # $var <type> <width> <id> <reference> [$end]
            if len(parts) < 5:
                raise ValueError("malformed VCD line %d: bad $var: %r"
                                 % (lineno, buf))
            ident = parts[3]
            ref = parts[4]
            name = ".".join(scope + [ref]) if scope else ref
            if name in name_to_id:
                raise ValueError("malformed VCD line %d: duplicate signal %r"
                                 % (lineno, name))
            id_to_names.setdefault(ident, []).append(name)
            name_to_id[name] = ident
            samples[name] = []
            continue
        if line.startswith("$enddefinitions"):
            _parse_header_end(line, lineno)
            seen_enddefinitions = True
            in_definitions = False
            continue
        if in_definitions and line.startswith("$"):
            # $date/$version/$comment...: header noise, skip whole block.
            while "$end" not in line:
                if i >= n:
                    raise ValueError("malformed VCD header before "
                                     "$enddefinitions (line %d)" % lineno)
                line = lines[i].strip()
                i += 1
            continue
        if not seen_enddefinitions:
            raise ValueError("malformed VCD: value/timestamp before "
                             "$enddefinitions (line %d: %r)"
                             % (lineno, raw))
        if line.startswith("$dumpvars") or line.startswith("$dumpall"):
            continue
        if line in ("$end", "$dumpoff $end", "$dumpon $end"):
            continue
        if line.startswith("#"):
            try:
                cur_time = int(line[1:].strip())
            except ValueError:
                raise ValueError("malformed VCD line %d: bad timestamp: %r"
                                 % (lineno, raw))
            if cur_time < 0:
                raise ValueError("malformed VCD line %d: negative time: %r"
                                 % (lineno, raw))
            max_time = max(max_time, cur_time)
            continue
        # Value change: scalar "<v><id>" or vector "b<bits> <id>".
        ident = None
        value = None
        if line[0] in "01xzXZ" and " " not in line and len(line) > 1:
            value, ident = line[0].lower(), line[1:]
        elif line.startswith("b") and " " in line:
            bits, ident = line[1:].split(None, 1)
            ident = ident.strip()
            if not bits or any(c not in "01xzXZ" for c in bits):
                raise ValueError("malformed VCD line %d: bad vector: %r"
                                 % (lineno, raw))
            value = bits.lower()
        elif line.startswith("r") and " " in line:
            real, ident = line[1:].split(None, 1)
            ident = ident.strip()
            value = "r" + real
        else:
            raise ValueError("malformed VCD line %d: bad value change: %r"
                             % (lineno, raw))
        if ident not in id_to_names:
            raise ValueError("malformed VCD line %d: value change for "
                             "unknown id %r" % (lineno, ident))
        for _name in id_to_names[ident]:
            samples[_name].append((cur_time, value))
    if not seen_enddefinitions:
        raise ValueError("malformed VCD: missing $enddefinitions -- "
                         "refusing to judge a truncated header")
    if not name_to_id:
        raise ValueError("malformed VCD: no signals declared")
    aliases = {i: ns for (i, ns) in id_to_names.items() if len(ns) > 1}
    return {"timescale": timescale or "unknown",
            "signals": dict(name_to_id),
            "samples": samples,
            "aliases": aliases,
            "t_end": max_time}


def parse_vcd_file(path):
    """Read a VCD file and parse it (loud on missing/unreadable)."""
    with open(path, encoding="utf-8") as fh:
        return parse_vcd(fh.read())


def resolve_signal(trace, name):
    """Resolve a short signal name to its full hierarchical name.

    Exact match wins; else a unique dotted-suffix match
    (e.g. "ser_tx" -> "tb.ser_tx"). Zero or ambiguous matches raise
    ValueError -- never guess silently.
    """
    samples = trace["samples"]
    if name in samples:
        return name
    cands = [s for s in samples
             if s == name or s.endswith("." + name)]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise ValueError("signal %r not in VCD (have: %s): refusing to "
                         "report agreement without the signal"
                         % (name, sorted(samples)))
    raise ValueError("signal %r is ambiguous in VCD: %s"
                     % (name, sorted(cands)))


def _level_at(samples, t):
    """Signal level at time t: last change at or before t (None if none)."""
    level = None
    for (ct, cv) in samples:
        if ct > t:
            break
        level = cv
    return level


def check_uart(tx_samples, bit_period, signal="ser_tx", t_end=None):
    """Check UART TX frames in a (time, value) sample list.

    Finds each frame start (a falling edge while the line is idle-high),
    requires the start-bit middle (t0 + period/2) low, decodes 8
    LSB-first data bits, and requires the stop-bit middle
    (t0 + 9.5*period) high. Decoding is frame-synchronous: after a
    frame starts at t0 the next start is only accepted at or after
    t0 + 10*period, so intra-frame data transitions are data, not new
    frames. A start edge is only accepted after the line has been
    observed high (idle): a trace that begins mid-frame (line already
    low, e.g. a power-on dummy char) skips to the first idle->low
    edge instead of misreading mid-frame low as a start bit -- the
    same synchronization a hardware RX performs. Returns
    (violations, frames); a frame whose stop-bit sample lies past the
    trace end (t_end, defaulting to the last sample time) is a
    uart-frame-truncated violation, never dropped silently.
    """
    if bit_period <= 0:
        raise ValueError("bit_period must be positive, got %r"
                         % (bit_period,))
    vals = [(t, str(v)) for (t, v) in tx_samples]
    violations = []
    frames = []
    ordered = sorted(vals)
    if not ordered:
        return violations, frames
    end_t = ordered[-1][0] if t_end is None else t_end
    cur = None  # unknown until the first sample (no assumed idle)
    seen_high = False  # frame starts need observed idle-high first
    cursor = None  # earliest time a new frame start is accepted
    for (ct, cv) in ordered:
        if cv not in ("0", "1"):
            continue  # x/z: skip edge detection, sampling judges
        if cur is None:
            cur = cv  # first sample sets the level, never an edge
        if cv == "1":
            seen_high = True
        if (cur == "1" and cv == "0" and seen_high
                and (cursor is None or ct >= cursor)):
            t0 = ct
            half = bit_period / 2.0
            stop_t = t0 + 9.5 * bit_period
            # Start-bit middle must be low.
            s_mid = t0 + half
            if _level_at(ordered, s_mid) != "0":
                violations.append({
                    "time": s_mid if float(s_mid).is_integer()
                    else s_mid,
                    "signal": signal,
                    "rule": "uart-start-bit-high",
                    "detail": ("start bit not low at middle of start "
                               "interval (t=%s)") % _fmt_t(s_mid),
                })
            # Decode 8 data bits at interval middles.
            byte = 0
            for b in range(8):
                bt = t0 + (1.5 + b) * bit_period
                if _level_at(ordered, bt) == "1":
                    byte |= (1 << b)
            if end_t < stop_t:
                violations.append({
                    "time": end_t,
                    "signal": signal,
                    "rule": "uart-frame-truncated",
                    "detail": ("frame starting at t=%s ends at t=%s "
                               "before stop-bit sample t=%s"
                               % (_fmt_t(t0), _fmt_t(end_t),
                                  _fmt_t(stop_t))),
                })
                frames.append({"start": t0, "byte": byte,
                               "truncated": True})
                cur = cv
                continue
            if _level_at(ordered, stop_t) != "1":
                violations.append({
                    "time": stop_t if float(stop_t).is_integer()
                    else stop_t,
                    "signal": signal,
                    "rule": "uart-stop-bit-low",
                    "detail": ("stop bit not high (framing error) at "
                               "t=%s" % _fmt_t(stop_t)),
                })
            frames.append({"start": t0, "byte": byte,
                           "truncated": False})
            cursor = t0 + 10 * bit_period
        cur = cv
    violations2 = []
    for v in violations:
        tt = v["time"]
        v["time"] = int(tt) if isinstance(tt, float) and tt.is_integer() else tt
        violations2.append(v)
    return violations2, frames


def _fmt_t(t):
    if isinstance(t, float) and t.is_integer():
        return str(int(t))
    return str(t)


def check_handshake(valid_samples, ready_samples,
                    valid_signal="valid", ready_signal="ready"):
    """Check ready/valid stability: valid, once raised, must stay high
    until a handshake (a time where ready is high).

    Changes sharing a timestamp form one group: if valid falls 1->0
    within a group whose post-group ready level is high, the handshake
    completed at that time (no violation). A 1->0 valid edge landing
    while ready is low is a handshake-valid-drop violation.
    """
    groups = {}
    for (t, v) in valid_samples:
        groups.setdefault(t, {})["valid"] = str(v)
    for (t, v) in ready_samples:
        groups.setdefault(t, {})["ready"] = str(v)
    violations = []
    vlevel = None
    rlevel = None
    for t in sorted(groups):
        chg = groups[t]
        vprev = vlevel
        if "valid" in chg:
            vlevel = chg["valid"]
        if "ready" in chg:
            rlevel = chg["ready"]
        if vprev == "1" and vlevel == "0" and rlevel != "1":
            violations.append({
                "time": t,
                "signal": valid_signal,
                "rule": "handshake-valid-drop",
                "detail": ("valid fell at t=%s while ready=%s "
                           "(must hold valid until ready)"
                           % (t, rlevel)),
            })
    return violations


def check_trace(trace, rules):
    """Run protocol rules against a parsed VCD trace.

    rules: [{"kind": "uart", "signal": ..., "bit_period": ...},
            {"kind": "handshake", "valid": ..., "ready": ...}]
    Returns {"violations", "all_clear", "checks", "frames", "summary"}.
    Unknown signals, unknown kinds, and empty rule lists raise loudly;
    a clean trace returns all_clear True with an explicit ALL CLEAR
    summary (never silent).
    """
    if not rules:
        raise ValueError("no rules given: refusing to report agreement "
                         "from zero checks")
    violations = []
    frames = []
    checks = []
    for rule in rules:
        kind = rule.get("kind")
        if kind == "uart":
            full = resolve_signal(trace, rule["signal"])
            bit_period = rule["bit_period"]
            viols, frs = check_uart(trace["samples"][full], bit_period,
                                    signal=full,
                                    t_end=trace.get("t_end"))
            violations.extend(viols)
            frames.extend(frs)
            checks.append("uart(%s, bit_period=%s): %d frame(s), %d "
                          "violation(s)" % (full, bit_period, len(frs),
                                            len(viols)))
        elif kind == "handshake":
            vfull = resolve_signal(trace, rule["valid"])
            rfull = resolve_signal(trace, rule["ready"])
            viols = check_handshake(trace["samples"][vfull],
                                    trace["samples"][rfull],
                                    valid_signal=vfull,
                                    ready_signal=rfull)
            violations.extend(viols)
            checks.append("handshake(%s/%s): %d violation(s)"
                          % (vfull, rfull, len(viols)))
        else:
            raise ValueError("unknown rule kind %r (want one of %s)"
                             % (kind, SUPPORTED_KINDS))
    violations.sort(key=lambda v: (v["time"], v["signal"], v["rule"]))
    all_clear = not violations
    if all_clear:
        summary = ("ALL CLEAR: %d check(s) passed on %d signal(s), "
                   "0 violations." % (len(checks), len(trace["samples"])))
    else:
        summary = ("%d violation(s) in %d check(s): %s"
                   % (len(violations), len(checks),
                      "; ".join("[t=%s %s %s] %s"
                                % (v["time"], v["signal"], v["rule"],
                                   v["detail"]) for v in violations)))
    return {"violations": violations, "all_clear": all_clear,
            "checks": checks, "frames": frames, "summary": summary}


def write_discovery_report(path, case_name, report):
    """Write eval/discovery_protocol.md-style report: violations or
    explicit ALL CLEAR. Discovery reports NEVER carry verdicts."""
    lines = [
        "# Discovery: protocol-assertion checker (Task A5)",
        "",
        "- case: %s" % case_name,
        "- checks: %s" % ("; ".join(report.get("checks", []))),
        "- all_clear: %s" % ("YES" if report.get("all_clear") else "NO"),
        "",
    ]
    if report.get("all_clear"):
        lines.append(report.get("summary", "ALL CLEAR."))
    else:
        lines.append("VIOLATIONS: signal-level protocol rules fired:")
        for v in report.get("violations", []):
            lines.append("- t=%s %s %s: %s"
                         % (v.get("time"), v.get("signal"),
                            v.get("rule"), v.get("detail")))
    lines.append("")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
