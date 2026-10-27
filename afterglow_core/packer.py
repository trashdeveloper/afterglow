"""Scored packer: v3 collector output (fingerprint + cone + verdict).

Serves eval cases: assembles the deterministic HARVEST block the v3
prompt consumes via {{HARVEST}}. Every section carries a provenance
header with exact counts so a judge can verify grounding.

Sections (fixed scores, greedy fill under budget):
  fingerprint  1.0  (mandatory-rank first, never dropped unless verdict
                     alone exceeds budget -- verdict always wins)
  cone_slice   0.9
  verdict      mandatory (never dropped; the collector discipline
                     requires trap/tohost context)

Caps (fail loudly, like agent/collect.py):
  pack <= MAX_PACK_LINES (200) lines, <= MAX_PACK_TOKENS (2500) tokens.
  Budget for scored merge defaults to PACK_BUDGET_TOKENS (900) but the
  hard caps always hold; build_v3_pack asserts both.

Counts returned: pack_lines, pack_tokens, sections, dropped,
fingerprint, cone_lines, verdict_tokens, budget_tokens.
Stdlib only.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

MAX_PACK_LINES = 200
MAX_PACK_TOKENS = 2500
PACK_BUDGET_TOKENS = 900

# Fixed priority scores (harvest plan Task 5 background).
SCORE_FINGERPRINT = 1.0
SCORE_CONE = 0.9
SCORE_VERDICT = 0.7  # informational: verdict is mandatory, not scored


def approx_tokens(text: str) -> int:
    """Whitespace-split token estimate (same ruler as the collector)."""
    return len(text.split())


def score_sections(
        sections: List[Tuple[str, str, float]]) -> List[Tuple[str, str, float]]:
    """Sort (name, text, score) triples descending by score (stable)."""
    return sorted(sections, key=lambda s: -s[2])


def merge_scored(scored: List[Tuple[str, str, float]],
                 budget_tokens: int = PACK_BUDGET_TOKENS
                 ) -> Tuple[str, List[str]]:
    """Greedy fill in score order; returns (text, dropped_names)."""
    kept: List[str] = []
    dropped: List[str] = []
    used = 0
    # Pre-tokenize each section once (sections already sorted by score).
    tokenized = [(name, text, approx_tokens(text)) for name, text, _ in scored]
    for name, text, tok in tokenized:
        sep = len(kept)  # "\n\n" separators add (n-1) tokens-ish; count exactly
        candidate = "\n\n".join(kept + [text]) if kept else text
        if approx_tokens(candidate) <= budget_tokens:
            kept.append(text)
            used = approx_tokens(candidate)
        else:
            dropped.append(name)
    _ = used
    return "\n\n".join(kept), dropped


def _fp_section(rec: Dict) -> str:
    fphex = str(rec.get("fingerprint", ""))
    trap = str(rec.get("trap_line", ""))
    oracle = str(rec.get("oracle_sig", ""))
    sigs = list(rec.get("signals", []))
    body = ("fingerprint: %s\ntrap: %s\noracle: %s\nsignals: %s"
            % (fphex, trap, oracle, ", ".join(sigs)))
    header = ("### FINGERPRINT [sha256: %s | signals: %d | tokens: %d]"
              % (fphex[:12] if fphex else "none",
                 len(sigs), approx_tokens(body)))
    return header + "\n" + body


def _cone_section(cone_lines: List[str], report: Dict) -> str:
    body_lines = list(cone_lines or [])
    body = "\n".join(body_lines) if body_lines else "(empty cone)"
    header = ("### CONE_SLICE [seed: %s | files: %d | lines: %d | "
              "coverage: %s/%s resolved | unparsed: %s | tokens: %d]"
              % (report.get("seed", "?"),
                 len(report.get("files", [])),
                 len(body_lines),
                 report.get("resolved", 0), report.get("sought", 0),
                 report.get("unparsed", 0), approx_tokens(body)))
    return header + "\n" + body


def _verdict_section(verdict_text: str) -> str:
    body = (verdict_text or "").strip() or "(no verdict)"
    header = "### VERDICT [source: verdict_before.json | tokens: %d]" % (
        approx_tokens(body),)
    return header + "\n" + body


def build_v3_pack(fingerprint_record: Dict,
                  cone_lines: List[str], cone_report: Dict,
                  verdict_text: str,
                  budget_tokens: int = PACK_BUDGET_TOKENS) -> Tuple[str, Dict]:
    """Assemble the v3 HARVEST block. Returns (pack_text, counts).

    Verdict is mandatory and always present; fingerprint (1.0) and cone
    (0.9) fill the remaining budget greedily. Hard caps (200 lines /
    2500 tokens) are asserted -- oversized output fails loudly, never
    silently truncates.
    """
    fp_text = _fp_section(fingerprint_record or {})
    cone_text = _cone_section(cone_lines or [], cone_report or {})
    verdict_section = _verdict_section(verdict_text)
    scored = score_sections([
        ("fingerprint", fp_text, SCORE_FINGERPRINT),
        ("cone_slice", cone_text, SCORE_CONE),
    ])
    merged, dropped = merge_scored(scored, budget_tokens=budget_tokens)
    parts = ([merged, verdict_section] if merged else [verdict_section])
    pack = "\n\n".join(parts)
    counts = {
        "pack_lines": len(pack.splitlines()),
        "pack_tokens": approx_tokens(pack),
        "sections": ([s for s in ["fingerprint", "cone_slice"]
                       if s not in dropped] + ["verdict"]),
        "dropped": list(dropped),
        "fingerprint": str((fingerprint_record or {}).get("fingerprint", "")),
        "cone_lines": len(cone_lines or []),
        "verdict_tokens": approx_tokens((verdict_text or "").strip()),
        "budget_tokens": budget_tokens,
    }
    assert counts["pack_lines"] <= MAX_PACK_LINES, (
        "v3 pack %d lines exceeds cap %d"
        % (counts["pack_lines"], MAX_PACK_LINES))
    assert counts["pack_tokens"] <= MAX_PACK_TOKENS, (
        "v3 pack %d tokens exceeds cap %d"
        % (counts["pack_tokens"], MAX_PACK_TOKENS))
    return pack, counts
