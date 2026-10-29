#!/usr/bin/env python3
"""Task A6: assertion synthesizer (Wave A FIND BUGS).

Takes an RTL excerpt + spec text, calls the text model ONCE via the
existing agent/loop.py model-call seam (loop.text_call -- the exact
request function used by the live loop, never a new API client) to
propose SystemVerilog assertion properties; stores them to a pinned
properties file; then bound-checks each property against a small
fixture set of KNOWN mutants by evaluating signal conditions on
per-cycle trace values from a sim run.

Interfaces:
  build_prompt(excerpt, spec_text) -> str
  parse_model_output(content) -> [property, ...]
  synthesize(excerpt, spec_text, call_fn=None) -> [property, ...]
      call_fn defaults to loop.text_call (lazy import so this module
      stays import-light); tests inject a stub, never live.
  write_properties_file(path, properties) -> path
  parse_properties_file(path) -> [property, ...]
  bound_check(properties, mutant_traces) -> {prop: {mutant: status}}

Property dict: {"name": str, "signals": [str], "sva": str}.
Pinned file format (one block per property, blank-line separated):
  property <name>
  signals: <s1>, <s2>
  sva: <single-line SVA text>

SUPPORTED EVALUATOR SUBSET (honest, no full SVA engine):
  1. Implication invariant: (A == <const>) |-> (B == <const>)
     -- every cycle where A equals the const must have B equal to
     its const. Antecedent and consequent must each be a single
     (signal == const) term with == (no !=, no &&/||).
  2. Plain invariant: (signal == <const>) -- every cycle holds.
  Consts: decimal ints or Verilog sized literals (1'b0, 8'hA5).
  An optional "@(posedge <clk>)" prefix and the outer
  "assert property (...)" wrapper are ignored (clocking only).
  EVERYTHING ELSE (temporal ## delays, $past/$rose, sequences,
  cover statements, multi-term boolean structure) is reported as
  "unsupported" -- never evaluated, never claimed to hold.

Honesty contract (mirrors protocol.py: never invent signal):
- Empty excerpt/spec raises ValueError BEFORE any model call.
- Empty model output or non-JSON output raises ValueError loudly.
- A property naming a signal absent from a trace yields
  "missing-signal" for that mutant -- never a silent hold.
- An SVA outside the subset yields "unsupported" -- never hold/falsified.
- Empty property list or empty trace set raises ValueError (nothing
  to check is not clean).
- Ragged traces (signals of differing length) raise ValueError.
"""

import json
import os
import re

STATUSES = ("holds", "falsified", "unsupported", "missing-signal")

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INT_RE = re.compile(r"^\s*(\d+)'([bBhHdD])([0-9a-fA-F_xXzZ?]+)\s*$")
_EQ_RE = re.compile(
    r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*==\s*([^)]+?)\s*\)")


def _parse_const(text):
    """Parse a decimal or Verilog sized-literal const to int."""
    text = str(text).strip()
    m = _INT_RE.match(text)
    if m:
        _width, base, digits = m.groups()
        digits = digits.replace("_", "")
        if re.search(r"[xXzZ?]", digits):
            raise ValueError("const has x/z/? bits: %r" % (text,))
        return int(digits, {"b": 2, "h": 16, "d": 10}[base.lower()])
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text, 10)
    raise ValueError("const not a plain integer or sized literal: %r"
                     % (text,))


def _check_name(name):
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError("bad property/signal name: %r" % (name,))
    return name


def _check_property(prop):
    if not isinstance(prop, dict):
        raise ValueError("property must be an object, got %r" % (prop,))
    name = _check_name(prop.get("name", ""))
    signals = prop.get("signals")
    if (not isinstance(signals, list) or not signals
            or not all(isinstance(s, str) and _NAME_RE.match(s)
                       for s in signals)):
        raise ValueError("property %r needs a non-empty signals list of "
                         "identifiers, got %r" % (name, signals))
    sva = prop.get("sva")
    if not isinstance(sva, str) or not sva.strip():
        raise ValueError("property %r needs non-empty SVA text" % (name,))
    if "assert" not in sva:
        raise ValueError("property %r SVA must contain 'assert', got %r"
                         % (name, sva))
    return {"name": name, "signals": list(signals), "sva": sva.strip()}


def build_prompt(excerpt, spec_text):
    """Assemble the single model prompt for property synthesis."""
    if not isinstance(excerpt, str) or not excerpt.strip():
        raise ValueError("empty excerpt: refusing to call the model "
                         "with no RTL")
    if not isinstance(spec_text, str) or not spec_text.strip():
        raise ValueError("empty spec text: refusing to call the model "
                         "with no spec")
    return "\n".join([
        "You are an SVA property synthesizer for RTL discovery.",
        "Given the RTL excerpt and the spec text, propose SystemVerilog",
        "assertion properties a bound-checker can falsify per-cycle.",
        "Stay inside the checkable subset: per-cycle invariants of the",
        "forms (A == const) |-> (B == const) or (S == const) with plain",
        "integer or sized-literal consts. No temporal ##, no $past/$rose.",
        "Each sva MUST be a complete single-line assertion starting with",
        "'assert property', e.g. 'assert property (@(posedge clk)",
        "(send_bitcnt == 0) |-> (ser_tx == 1));'. Bare boolean",
        "expressions without the assert wrapper will be rejected.",
        "Output JSON only, exactly: "
        '{"properties": [{"name": ..., "signals": [...], "sva": ...}]}.',
        "",
        "RTL EXCERPT:",
        excerpt.strip(),
        "",
        "SPEC TEXT:",
        spec_text.strip(),
    ])


def parse_model_output(content):
    """Parse the model's JSON-only output into validated properties."""
    if content is None or not str(content).strip():
        raise ValueError("model returned empty content: refusing to "
                         "invent properties")
    text = str(content).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model output is not a JSON object: %r"
                         % (content[:200],))
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise ValueError("model output JSON parse failed: %s" % (exc,))
    if not isinstance(obj, dict) or not isinstance(
            obj.get("properties"), list) or not obj["properties"]:
        raise ValueError("model output has no non-empty 'properties' list")
    return [_check_property(p) for p in obj["properties"]]


def synthesize(excerpt, spec_text, call_fn=None):
    """Call the text model ONCE and return validated properties.

    call_fn defaults to loop.text_call (the exact live-loop request
    function, imported lazily). It must take the prompt string and
    return the loop envelope dict {"content", ...}. Tests inject a
    stub -- this function never invents its own client.
    """
    prompt = build_prompt(excerpt, spec_text)  # validates BEFORE any call
    if call_fn is None:
        from loop import text_call as call_fn  # noqa: E402  (loop seam)
    envelope = call_fn(prompt)
    if isinstance(envelope, dict):
        content = envelope.get("content", "")
    else:
        content = str(envelope)
    return parse_model_output(content)


def write_properties_file(path, properties):
    """Write properties in the pinned one-block-per-property format."""
    if not isinstance(properties, list) or not properties:
        raise ValueError("no properties given: refusing to write an "
                         "empty properties file")
    checked = [_check_property(p) for p in properties]
    blocks = []
    for prop in checked:
        blocks.append("property %s\nsignals: %s\nsva: %s"
                      % (prop["name"], ", ".join(prop["signals"]),
                         prop["sva"]))
    text = "\n\n".join(blocks) + "\n"
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def parse_properties_file(path):
    """Parse a pinned properties file back into property dicts."""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    if not raw.strip():
        raise ValueError("empty properties file: %s" % (path,))
    props = []
    for block in raw.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) != 3:
            raise ValueError("malformed property block (want exactly 3 "
                             "lines: property/signals/sva), got %r" % (block,))
        if not lines[0].startswith("property "):
            raise ValueError("malformed property block: first line must "
                             "be 'property <name>', got %r" % (lines[0],))
        name = lines[0][len("property "):].strip()
        if not lines[1].startswith("signals:"):
            raise ValueError("malformed property block %r: second line "
                             "must be 'signals: ...'" % (name,))
        signals = [s.strip() for s in
                   lines[1][len("signals:"):].split(",")]
        if not lines[2].startswith("sva:"):
            raise ValueError("malformed property block %r: third line "
                             "must be 'sva: ...'" % (name,))
        sva = lines[2][len("sva:"):].strip()
        props.append(_check_property(
            {"name": name, "signals": signals, "sva": sva}))
    if not props:
        raise ValueError("no properties parsed from %s" % (path,))
    return props


def _strip_wrapper(sva):
    """Remove the outer assert-property wrapper + clocking prefix.

    Returns the inner boolean expression, or None when the text is
    not an assert-property shape at all ( -> unsupported).
    """
    text = sva.strip()
    m = re.search(r"assert\s+property\s*\(", text)
    if not m:
        return None
    inner = text[m.end():]
    if inner.endswith(");"):
        inner = inner[:-2]
    elif inner.endswith(")"):
        inner = inner[:-1]
    else:
        return None
    inner = inner.strip()
    inner = re.sub(r"^@\s*\([^)]*\)\s*", "", inner).strip()
    return inner or None


def _compile_check(sva):
    """Compile an SVA string to a supported per-cycle check.

    Returns ("imply", cond_sig, cond_val, tgt_sig, tgt_val) or
    ("always", sig, val); returns None when outside the subset.
    """
    inner = _strip_wrapper(sva)
    if inner is None:
        return None
    if "|->" in inner:
        parts = inner.split("|->")
        if len(parts) != 2:
            return None
        ma = _EQ_RE.fullmatch(parts[0].strip())
        mb = _EQ_RE.fullmatch(parts[1].strip())
        if ma is None or mb is None:
            return None
        try:
            return ("imply", ma.group(1), _parse_const(ma.group(2)),
                    mb.group(1), _parse_const(mb.group(2)))
        except ValueError:
            return None
    m = _EQ_RE.fullmatch(inner)
    if m is None:
        return None
    try:
        return ("always", m.group(1), _parse_const(m.group(2)))
    except ValueError:
        return None


def _trace_values(trace, signal):
    vals = trace.get(signal)
    if vals is None:
        return None
    if not isinstance(vals, (list, tuple)) or not vals:
        raise ValueError("trace for signal %r is empty/non-list" % (signal,))
    out = []
    for v in vals:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            raise ValueError("trace value %r for signal %r is not an "
                             "integer cycle value" % (v, signal))
    return out


def bound_check(properties, mutant_traces):
    """Bound-check each property against each named mutant trace.

    mutant_traces: {mutant_name: {signal: [per-cycle int values]}}.
    Returns {prop_name: {mutant_name: status}} with status one of
    holds / falsified / unsupported / missing-signal.
    """
    if not isinstance(properties, list) or not properties:
        raise ValueError("no properties given: refusing to report "
                         "agreement from zero checks")
    if not isinstance(mutant_traces, dict) or not mutant_traces:
        raise ValueError("no mutant traces given: refusing to report "
                         "agreement with nothing checked")
    checked = [_check_property(p) for p in properties]
    result = {}
    for prop in checked:
        compiled = _compile_check(prop["sva"])
        per_mutant = {}
        for mutant, trace in mutant_traces.items():
            if compiled is None:
                per_mutant[mutant] = "unsupported"
                continue
            if compiled[0] == "imply":
                _, csig, cval, tsig, tval = compiled
                need = (csig, tsig)
            else:
                _, ssig, sval = compiled
                need = (ssig,)
            if any(s not in trace for s in need):
                per_mutant[mutant] = "missing-signal"
                continue
            cols = {s: _trace_values(trace, s) for s in need}
            lengths = {len(v) for v in cols.values()}
            if len(lengths) != 1:
                raise ValueError("ragged trace for mutant %r: signal "
                                 "lengths %s" % (mutant, sorted(lengths)))
            n = lengths.pop()
            if compiled[0] == "imply":
                bad = any(cols[csig][i] == cval and cols[tsig][i] != tval
                          for i in range(n))
            else:
                bad = any(cols[ssig][i] != sval for i in range(n))
            per_mutant[mutant] = "falsified" if bad else "holds"
        result[prop["name"]] = per_mutant
    return result
