#!/usr/bin/env python3
"""Task C7: Cross-fault transfer (plan item 27, Wave C FIX BETTER).

Index fixed diffs by signal names; a new fault naming the same
signals retrieves the known prior fix, top match first.

Interface::

  extract_signals(text) -> [signal names, encounter order, deduped]
  build_index(entries) -> {"entries": [...], "by_signal": {...}}
  suggest(fault, index, top_n=1) -> [patches, best overlap first]

``entries`` is a non-empty list/tuple of ``{"signals", "patch"}``
dicts (``signals`` is free text or a signal-name list, ``patch`` a
non-empty diff string); ``(signals, patch)`` pairs are also accepted.
``fault`` is free text or a signal-name list. A signal name is an
identifier containing ``_`` or a digit (``baud_cnt``, ``pc_addr``):
bare prose words (``stuck``, ``missing``) and Verilog keywords
(``always``, ``posedge``) are never signals. Scoring is overlap count
of the fault signal set vs each entry's signal set, descending; ties
break by insertion order. Zero overlap is a miss -> [] (never an
invented fix); only the first ``top_n`` patches are returned, so the
caller tries the top match first.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``re`` only); no API, no keys, no network, no I/O.
"""

import re

_SIGNAL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Verilog/SystemVerilog keywords: never signal names (belt-and-braces
# next to the underscore-or-digit rule, which already drops them).
VERILOG_KEYWORDS = frozenset(
    "always and assign begin buf bufif0 bufif1 case casex casez default "
    "defparam else end endcase endfunction endgenerate endmodule "
    "for force forever fork function generate genvar if initial inout "
    "input integer join large medium module negedge nmos output "
    "parameter pmos posedge reg release repeat rnmos rpmos rtran "
    "rtranif0 rtranif1 scalared signed small specify specparam "
    "strength strong0 strong1 supply0 supply1 table task time tran "
    "tranif0 tranif1 tri tri0 tri1 triand trior trireg unsigned vectored "
    "wait wand weak0 weak1 while wire wor xnor xor".split())


def _is_signal(token):
    """A signal name carries ``_`` or a digit and is no keyword."""
    if len(token) < 2:
        return False
    if "_" not in token and not any(ch.isdigit() for ch in token):
        return False
    return token.lower() not in VERILOG_KEYWORDS


def extract_signals(text):
    """Find signal names in free text, encounter order, deduped.

    Non-string input raises TypeError. Text with no signal names
    (prose, keywords, numbers only) -> [].
    """
    if not isinstance(text, str):
        raise TypeError(
            "transfer: text must be a string, got %r" % (text,))
    seen = set()
    found = []
    for token in _SIGNAL.findall(text):
        if _is_signal(token) and token not in seen:
            seen.add(token)
            found.append(token)
    return found


def _entry_signals(signals):
    """Normalize an entry's ``signals`` field to a deduped name list."""
    if isinstance(signals, str):
        names = extract_signals(signals)
    elif isinstance(signals, (list, tuple)):
        names = []
        for item in signals:
            if not isinstance(item, str) or not item:
                raise ValueError(
                    "transfer: entry signals must be non-empty "
                    "strings, got %r" % (item,))
            if not _SIGNAL.fullmatch(item) or not _is_signal(item):
                raise ValueError(
                    "transfer: not a signal name: %r" % (item,))
            if item not in names:
                names.append(item)
    else:
        raise TypeError(
            "transfer: entry signals must be text or a signal-name "
            "list, got %r" % (signals,))
    if not names:
        raise ValueError(
            "transfer: entry has no signal names (nothing to index)")
    return names


def build_index(entries):
    """Index fixed diffs by signal names.

    Returns ``{"entries": [{"signals": tuple, "patch": str}],
    "by_signal": {signal: [entry indices]}}``. Empty/non-list input,
    entries without a usable patch, or entries with no signal names
    raise ValueError/TypeError (refuse to fabricate an index).
    """
    if isinstance(entries, (str, bytes)) or not isinstance(
            entries, (list, tuple)):
        raise TypeError(
            "transfer: entries must be a non-empty list of "
            "{'signals', 'patch'} dicts, got %r" % (entries,))
    if not entries:
        raise ValueError(
            "transfer: entries must be a non-empty list of "
            "{'signals', 'patch'} dicts")
    normalized = []
    by_signal = {}
    for pos, entry in enumerate(entries):
        if isinstance(entry, dict):
            signals, patch = entry.get("signals"), entry.get("patch")
        elif (isinstance(entry, (list, tuple)) and len(entry) == 2
                and not isinstance(entry, str)):
            signals, patch = entry
        else:
            raise ValueError(
                "transfer: entry %d must be a {'signals', 'patch'} "
                "dict or (signals, patch) pair, got %r" % (pos, entry))
        if not isinstance(patch, str) or not patch.strip():
            raise ValueError(
                "transfer: entry %d patch must be a non-empty diff "
                "string, got %r" % (pos, patch))
        names = _entry_signals(signals)
        normalized.append({"signals": tuple(names), "patch": patch})
        for sig in names:
            by_signal.setdefault(sig, []).append(
                len(normalized) - 1)
    return {"entries": normalized, "by_signal": by_signal}


def _fault_signals(fault):
    """Normalize the fault to a non-empty signal-name list."""
    if isinstance(fault, str):
        names = extract_signals(fault)
    elif isinstance(fault, (list, tuple)):
        names = []
        for item in fault:
            if not isinstance(item, str) or not item:
                raise ValueError(
                    "transfer: fault signals must be non-empty "
                    "strings, got %r" % (item,))
            if item not in names:
                names.append(item)
    else:
        raise TypeError(
            "transfer: fault must be text or a signal-name list, "
            "got %r" % (fault,))
    if not names:
        raise ValueError(
            "transfer: fault has no signal names (nothing to match)")
    return names


def suggest(fault, index, top_n=1):
    """Return up to ``top_n`` stored patches, best overlap first.

    Scores each indexed fix by signal-overlap count with the fault;
    zero-overlap fixes are dropped, so an unknown fault returns []
    (a miss, not a guess). Bad fault / bad index / out-of-range
    ``top_n`` raise ValueError/TypeError.
    """
    if (isinstance(top_n, bool) or not isinstance(top_n, int)
            or isinstance(index, bool)):
        raise TypeError(
            "transfer: top_n must be an int and index a build_index "
            "result, got top_n=%r" % (top_n,))
    if not isinstance(index, dict) or not isinstance(
            index.get("entries"), list) or not index["entries"]:
        raise ValueError(
            "transfer: index must be a non-empty build_index result, "
            "got %r" % (index,))
    entries = index["entries"]
    if top_n < 1 or top_n > len(entries):
        raise ValueError(
            "transfer: top_n must be in [1, %d], got %r"
            % (len(entries), top_n))
    query = set(_fault_signals(fault))
    scored = []
    for pos, entry in enumerate(entries):
        overlap = len(query & set(entry["signals"]))
        if overlap:
            scored.append((-overlap, pos, entry["patch"]))
    scored.sort()
    return [patch for _, _, patch in scored[:top_n]]
