"""Afterglow context pack v2 (prompt v2 {{CONTEXT}} slot).

What a senior engineer pulls before touching code -- all
machine-derived, all relevance-ranked under a FIXED token budget so the
full prompt still respects the collector cap (<=2500 tokens):

1. LOCALIZATION: suspect signal names scraped from the trace's anchor
   lines, ranked against the RTL tree by whole-word hits. Lexical, not a
   real cone-of-influence -- labeled as such. Measurable: top-1 file vs
   the case's true RTL file lands in meta for eval.
2. BLAME: `git blame` window around the excerpt center for files under
   version control. Fail-soft "" outside git (omit, never crash).
3. MEMORY: compact lines from our own past GREEN runs (diagnosis +
   patch size) -- few-shot from proven fixes, no external data.

Drop order under pressure: memory -> blame -> localization (narrowed).
Every section records provenance (counts + what was dropped) so a judge
can verify the model was grounded, not prompted with answers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Dict, List, Tuple

PACK_BUDGET_TOKENS = 900  # context sections share this; trace+verdict keep theirs
MAX_SIGNALS = 12
MAX_SIGNAL_HITS = 10  # per-file hit lines shown
BLAME_HALF = 7  # lines each side of center
MEMORY_K = 2  # past green runs remembered

_ANCHOR_RE = re.compile(r"MISCLASS|TRAP:|tohost:")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOPWORDS = {
    "the", "and", "for", "with", "cycle", "fail", "pass", "trap",
    "tohost", "expect", "mismatch", "error", "warning", "module",
    "begin", "end", "wire", "reg", "always",
}


def approx_tokens(text: str) -> int:
    """Whitespace-split token estimate (same ruler as the collector)."""
    return len(text.split())


def suspect_signals(trace_block: str) -> List[str]:
    """Signal-ish identifiers from anchor lines, most frequent first."""
    freq: Dict[str, int] = {}
    for line in trace_block.splitlines():
        if not _ANCHOR_RE.search(line):
            continue
        for tok in _IDENT_RE.findall(line):
            low = tok.lower()
            if low in _STOPWORDS or tok.isdigit():
                continue
            freq[tok] = freq.get(tok, 0) + 1
    ranked = sorted(freq, key=lambda t: (-freq[t], t))
    return ranked[:MAX_SIGNALS]


def localize(tree: str, rtl_files: List[str],
             signals: List[str]) -> List[Tuple[str, int, List[str]]]:
    """Rank tree files by whole-word hits of suspect signals.

    Returns [(rel, hit_count, [hit lines...])] sorted by hits desc.
    Missing/unreadable files rank zero (never crash collection).
    """
    ranked = []
    for rel in rtl_files:
        path = os.path.join(tree, rel)
        try:
            with open(path, "r", encoding="utf-8",
                      errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            ranked.append((rel, 0, []))
            continue
        hits = []
        for i, ln in enumerate(lines):
            for sig in signals:
                if re.search(r"\b%s\b" % re.escape(sig), ln):
                    hits.append("line %d: %s" % (i + 1, ln.strip()))
                    break
        ranked.append((rel, len(hits), hits[:MAX_SIGNAL_HITS]))
    ranked.sort(key=lambda r: (-r[1], r[0]))
    return ranked


def blame_snippet(tree: str, rel: str, center_line: int,
                  half: int = BLAME_HALF) -> str:
    """`git blame -L` window around center_line. "" outside git / on error."""
    start = max(1, center_line - half)
    end = center_line + half
    try:
        proc = subprocess.run(
            ["git", "-C", tree, "blame", "-L", "%d,%d" % (start, end),
             "--date=short", "--", rel],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def past_fixes(runs_dir: str, k: int = MEMORY_K) -> List[str]:
    """Compact lines from our own GREEN diagnosed runs, newest first.

    Only status green + diagnosed runs are remembered; reds never teach.
    """
    remembered = []
    try:
        names = sorted(os.listdir(runs_dir), reverse=True)
    except OSError:
        return []
    for name in names:
        meta_path = os.path.join(runs_dir, name, "meta.json")
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        if meta.get("status") != "green" or not meta.get("diagnosed"):
            continue
        diag = str(meta.get("diagnosis") or meta.get("diagnosis_text")
                   or "").strip().replace("\n", " ")[:200]
        attempts = meta.get("attempts") or []
        if not diag and attempts:
            diag = str(attempts[0].get("summary", ""))[:200]
        remembered.append("%s: %s [patch %s lines]" % (
            name, diag, meta.get("patch_lines", "?")))
        if len(remembered) >= k:
            break
    return remembered


def build_pack(trace_block: str, tree: str, rtl_files: List[str],
               runs_dir: str, center_line: int,
               true_file: str = "") -> Tuple[str, Dict]:
    """Assemble the budgeted context pack. Returns (pack_text, counts).

    counts: pack_tokens, dropped (none|memory|memory+blame),
    top1_file, top1_is_true, n_signals.
    """
    signals = suspect_signals(trace_block)
    ranked = localize(tree, rtl_files, signals)
    top1 = ranked[0][0] if ranked else ""
    loc_lines = ["LOCALIZATION (lexical signal-hit ranking, not a "
                 "cone-of-influence -- verify against the trace):"]
    for rel, hits, shown in ranked[:4]:
        loc_lines.append("  %s: %d hits" % (rel, hits))
        loc_lines.extend("    %s" % ln for ln in shown[:4])
    localization = "\n".join(loc_lines)

    blame = ""
    if top1:
        raw = blame_snippet(tree, top1, center_line)
        if raw:
            blame = ("BLAME (%s around line %d -- recent edits cause "
                     "most regressions):\n%s" % (top1, center_line, raw))

    memory_lines = past_fixes(runs_dir)
    memory = ""
    if memory_lines:
        memory = ("MEMORY (our own past GREEN fixes, newest first -- "
                  "patterns, not answers):\n" + "\n".join(
                      "  - %s" % ln for ln in memory_lines))

    dropped = "none"
    sections = [localization]
    if memory and approx_tokens("\n\n".join(sections + [memory])) \
            <= PACK_BUDGET_TOKENS:
        sections.append(memory)
    elif memory:
        dropped = "memory"
    if blame:
        if approx_tokens("\n\n".join(sections + [blame])) \
                <= PACK_BUDGET_TOKENS:
            sections.append(blame)
        else:
            dropped = (dropped + "+blame") if dropped != "none" else "blame"
    pack = "\n\n".join(sections)
    counts = {
        "pack_tokens": approx_tokens(pack),
        "dropped": dropped,
        "top1_file": top1,
        "top1_is_true": bool(true_file) and top1 == true_file,
        "n_signals": len(signals),
    }
    return pack, counts
