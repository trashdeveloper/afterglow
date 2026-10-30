#!/usr/bin/env python3
"""Task G2: Repair from counterexample (plan item 72).

Produces::

  focus_from_first_diff(first_diff) -> {file, line, signals}

for the next prompt's focus block.

``first_diff`` is the pinned first-counterexample diff record::

  {"file": "picorv32.v", "line": 373,
   "text": "assign mem_xfer = mem_valid & mem_ready;"}

The focus echoes the diff line (``file`` + ``line``) with ``signals``
extracted from the diff text: identifiers in order of first appearance,
deduplicated, minus Verilog keywords and numeric/sized literals
(``8'b00000000`` contributes no signal). Malformed records (non-dict,
missing/empty ``file``/``text``, non-positive-int ``line``) raise
ValueError.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``re`` only); no API, no keys, no network, no I/O.
"""

import re

# Identifier-shaped tokens; sized-literal bases (the ``b00000000`` in
# ``8'b00000000``) are dropped by the preceding-apostrophe check in
# _extract_signals, not by this pattern.
_IDENT = re.compile(r"[A-Za-z_]\w*")

# Verilog-2001 keywords plus common Verilog-2005/SV additions; none of
# these is a design signal.
_KEYWORDS = frozenset("""
always and assign begin buf bufif0 bufif1 case casex casez cmos deassign
default defparam disable edge else end endcase endfunction endmodule
endprimitive endspecify endtable endtask event for force forever fork
function generate genvar highz0 highz1 if ifnone initial inout input
integer join large logic macromodule medium module nand negedge nmos nor
not notif0 notif1 or output parameter pmos posedge primitive pull0 pull1
pulldown pullup rcmos real realtime reg release repeat rnmos rpmos rtran
rtranif0 rtranif1 scalared signed small specify specparam strong0 strong1
supply0 supply1 table task time tran tranif0 tranif1 tri tri0 tri1 triand
trior trireg unsigned vectored wait wand weak0 weak1 while wire wor xnor
xor bit int byte shortint longint
""".split())


def _extract_signals(text):
    """Return signal names in order of first appearance, deduplicated."""
    signals = []
    seen = set()
    for match in _IDENT.finditer(text):
        # Skip the base of a sized literal: 8'b00000000 -> "b00000000"
        # sits right after an apostrophe and is a value, not a signal.
        if match.start() > 0 and text[match.start() - 1] == "'":
            continue
        name = match.group(0)
        if name in _KEYWORDS or name in seen:
            continue
        seen.add(name)
        signals.append(name)
    return signals


def focus_from_first_diff(first_diff):
    """Build the next prompt's focus block from the first diff record.

    Returns ``{"file": file, "line": line, "signals": [...]}`` where
    file/line are the diff line and signals are extracted from its
    text. Raises ValueError on malformed records.
    """
    if not isinstance(first_diff, dict):
        raise ValueError(
            "cexrepair: first_diff must be a dict with "
            "'file'/'line'/'text', got %r" % (first_diff,))
    file = first_diff.get("file")
    if not isinstance(file, str) or not file.strip():
        raise ValueError(
            "cexrepair: first_diff['file'] must be a non-empty "
            "string, got %r" % (file,))
    line = first_diff.get("line")
    if (not isinstance(line, int) or isinstance(line, bool) or line <= 0):
        raise ValueError(
            "cexrepair: first_diff['line'] must be a positive int, "
            "got %r" % (line,))
    text = first_diff.get("text")
    if text is None:
        # Tolerated aliases for the diff-text field (primary: "text").
        for alias in ("content", "code", "diff"):
            if first_diff.get(alias) is not None:
                text = first_diff.get(alias)
                break
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "cexrepair: first_diff must carry non-empty diff text "
            "under 'text', got %r" % (text,))
    text = text.strip()
    # Tolerate one leading unified-diff marker ('+'/'-'); the marker is
    # not part of the code line.
    if text[0] in "+-" and len(text) > 1:
        text = text[1:].lstrip()
    return {"file": file.strip(), "line": line,
            "signals": _extract_signals(text)}
