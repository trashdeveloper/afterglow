"""Failure fingerprint: (trap line, oracle signature, signal names) -> hex.

Serves eval cases: every failing sim window maps to ONE deterministic
fingerprint so the fix-memory can retrieve similar past failures and the
v3 pack can prove which failure the model saw.

Scheme (pinned by tests/test_fingerprint.py::test_golden_hash):

    fp = sha256(normalize(trap) + "|" + trap_verbatim
                + "|" + normalize(oracle_sig)
                + "|" + "|".join(sorted({s.lower()})))

- ``normalize_*`` erases volatile fields (cycle counters, hex case,
  whitespace runs) so the same bug with different counters collides.
- The verbatim trap line stays in the hash input AND in the record, so
  two different bugs never collapse to one signature (anti-collision).
- Signal order never changes the hash (sorted); signal case never does
  (lowered).

Stdlib only. No network, no sim, no model calls.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Dict, List

_ANCHOR_RE = re.compile(r"MISCLASS|TRAP:|tohost:")
_TOHOST_RE = re.compile(r"tohost:\s*(.+)", re.IGNORECASE)
_CYCLE_RE = re.compile(r"\bcycles?\s*=\s*\d+", re.IGNORECASE)
_CYCLE_WORD_RE = re.compile(r"\bcycle\s+\d+", re.IGNORECASE)
_SEED_RE = re.compile(r"\bseed\s+\d+", re.IGNORECASE)
_HEX_RE = re.compile(r"0[xX][0-9a-fA-F]+")
_WS_RE = re.compile(r"\s+")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOPWORDS = {
    "the", "and", "for", "with", "cycle", "cycles", "fail", "pass",
    "trap", "tohost", "expect", "expected", "mismatch", "mismatched",
    "error", "warning", "module", "begin", "end", "wire", "reg",
    "always", "got", "want", "data", "seed",
}

MAX_SIGNALS = 16


def normalize_text(text: str) -> str:
    """Lowercase + collapse whitespace + erase counters (deterministic)."""
    s = text.lower()
    s = _CYCLE_RE.sub("cycles=n", s)
    s = _CYCLE_WORD_RE.sub("cycle n", s)
    s = _SEED_RE.sub("seed n", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def normalize_trace(trace_block: str) -> str:
    """Normalized whole-trace form (volatile-erased, line-stable)."""
    lines = []
    for raw in (trace_block or "").splitlines():
        line = raw.strip()
        if line.startswith("- "):
            line = line[2:]
        lines.append(normalize_text(line))
    return "\n".join(lines)


def extract_trap_line(trace_block: str) -> str:
    """Last anchor line (MISCLASS|TRAP:|tohost:), verbatim, stripped.

    Returns "" when no anchor line exists (never raises: an empty trap
    still fingerprints deterministically).
    """
    last = ""
    for raw in (trace_block or "").splitlines():
        if _ANCHOR_RE.search(raw):
            last = raw.strip()
    return last


def oracle_signature(verdict_text: str) -> str:
    """Short oracle-mismatch string from verdict JSON or raw log text.

    Prefers the ``tohost`` value (the mismatch signature the oracle
    judges by); falls back to a FAIL/PASS marker, else the first 200
    chars stripped. Never raises on malformed JSON.
    """
    text = verdict_text or ""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            toh = obj.get("tohost")
            if toh is not None and str(toh).strip():
                return str(toh).strip()
            if "pass" in obj:
                return "PASS" if obj.get("pass") else "FAIL"
    except (ValueError, AttributeError):
        pass
    m = _TOHOST_RE.search(text)
    if m:
        return m.group(1).strip()[:200]
    upper = text.upper()
    if "FAIL" in upper:
        return "FAIL"
    if "PASS" in upper:
        return "PASS"
    return text.strip()[:200]


def extract_signals(trace_block: str) -> List[str]:
    """Signal-ish identifiers from anchor lines, sorted, capped.

    Same stopword discipline as agent/context.py:suspect_signals so the
    fingerprint and the lexical localizer agree on names.
    """
    freq: Dict[str, int] = {}
    for line in (trace_block or "").splitlines():
        if not _ANCHOR_RE.search(line):
            continue
        for tok in _IDENT_RE.findall(line):
            low = tok.lower()
            if low in _STOPWORDS or tok.isdigit():
                continue
            freq[low] = freq.get(low, 0) + 1
    ranked = sorted(freq, key=lambda t: (-freq[t], t))
    return ranked[:MAX_SIGNALS]


def canonical_verbatim(line: str) -> str:
    """Volatile-erased verbatim: collapse whitespace, lowercase hex only.

    Case outside hex is PRESERVED (so genuinely different traps never
    collide), while hex-case jitter (0xE0 vs 0xe0) collapses. The true
    raw line stays in the record; this form is the hash's verbatim slot.
    """
    s = _WS_RE.sub(" ", line or "").strip()
    return _HEX_RE.sub(lambda m: m.group(0).lower(), s)


def fingerprint(trap_line: str, oracle_sig: str,
                signals: List[str]) -> str:
    """Deterministic 64-hex fingerprint for one failure."""
    norm_trap = normalize_text(trap_line or "")
    norm_oracle = normalize_text(oracle_sig or "")
    joined = "|".join(sorted({str(s).lower() for s in (signals or [])}))
    payload = "|".join(
        [norm_trap, canonical_verbatim(trap_line or ""),
         norm_oracle, joined])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_for_failure(trace_block: str,
                            verdict_text: str) -> Dict:
    """Full record for one failing window (what fingerprint.json stores).

    Returns {"fingerprint", "trap_line" (verbatim), "oracle_sig",
    "signals"}. The verbatim trap line is kept alongside the hash so a
    reviewer can see the collision surface.
    """
    trap = extract_trap_line(trace_block)
    oracle = oracle_signature(verdict_text)
    signals = extract_signals(trace_block)
    return {
        "fingerprint": fingerprint(trap, oracle, signals),
        "trap_line": trap,
        "oracle_sig": oracle,
        "signals": signals,
    }
