"""Fix-memory stub: Jaccard retrieval over runs/*/fingerprint.json.

Serves eval cases: given a query failure's signal set, rank past runs by
token-set Jaccard so the packer can cite similar greens. Retrieval
ADVISES -- re-sim always decides (scores logged, never asserted-on).

Read-only rule: recall/signature_index NEVER write. Old bundles are
never modified. The loop (later wiring task, NOT this module) writes
each NEW bundle's fingerprint.json via :func:`write_fingerprint`;
loop.py is untouched here.

fingerprint.json schema (written by write_fingerprint, read by recall):
  {"fingerprint": hex, "trap_line": str, "oracle_sig": str,
   "signals": [names...]}
Stdlib only.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple


def jaccard(a, b) -> float:
    """Token-set Jaccard in [0, 1]. Both empty -> 1.0, one empty -> 0.0."""
    sa = {str(x).lower() for x in (a or [])}
    sb = {str(x).lower() for x in (b or [])}
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def load_fingerprint(run_dir: str) -> Dict | None:
    """Load one bundle's fingerprint.json; None when missing/invalid."""
    path = os.path.join(run_dir, "fingerprint.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict) or not obj.get("fingerprint"):
        return None
    return obj


def write_fingerprint(run_dir: str, record: Dict) -> str:
    """Write a NEW bundle's fingerprint.json (loop wiring calls this later).

    Creates parent dirs as needed. Returns the written path. Raises
    ValueError when the record lacks a fingerprint hex. NEVER touches
    any other bundle directory.
    """
    fphex = str((record or {}).get("fingerprint", "")).strip()
    if not fphex:
        raise ValueError("write_fingerprint requires record['fingerprint']")
    os.makedirs(run_dir, exist_ok=True)
    payload = {
        "fingerprint": fphex,
        "trap_line": str(record.get("trap_line", "")),
        "oracle_sig": str(record.get("oracle_sig", "")),
        "signals": [str(s) for s in (record.get("signals", []) or [])],
    }
    # Preserve the normalized hash alongside the verbatim anchor (the
    # anti-collision rule): keep any extra diagnostic keys callers add.
    for key in ("normalized", "signals_norm"):
        if key in record:
            payload[key] = record[key]
    path = os.path.join(run_dir, "fingerprint.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2) + "\n")
    return path


def _is_green_diagnosed(runs_dir: str, name: str) -> bool:
    meta_path = os.path.join(runs_dir, name, "meta.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return False
    return meta.get("status") == "green" and bool(meta.get("diagnosed"))


def recall_similar(query_signals, runs_dir: str, k: int = 3,
                   exclude=()) -> List[Tuple[str, float, Dict]]:
    """Rank bundles by Jaccard(query, bundle signals), desc. Read-only.

    Bundles without a valid fingerprint.json are skipped. ``exclude``
    names run-ids that must never be returned (a run must not recall
    itself, e.g. a rerun reusing its run-id): a single id string or an
    iterable of ids. Returns at most k [(run_id, score, record)] tuples.
    Ties break by run_id.
    """
    if isinstance(exclude, str):
        excluded = {exclude}
    else:
        excluded = set(exclude or ())
    try:
        names = sorted(os.listdir(runs_dir))
    except OSError:
        return []
    scored = []
    for name in names:
        if name in excluded:
            continue
        rec = load_fingerprint(os.path.join(runs_dir, name))
        if rec is None:
            continue
        score = jaccard(query_signals, rec.get("signals", []))
        scored.append((name, score, rec))
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored[:max(0, int(k))]


def signature_index(runs_dir: str) -> Dict[str, List[str]]:
    """Fingerprint hex -> green diagnosed run-ids (read-only).

    Only bundles whose meta.json shows status green + diagnosed teach;
    reds and undiagnosed bundles never teach.
    """
    try:
        names = sorted(os.listdir(runs_dir))
    except OSError:
        return {}
    index: Dict[str, List[str]] = {}
    for name in names:
        rec = load_fingerprint(os.path.join(runs_dir, name))
        if rec is None:
            continue
        if not _is_green_diagnosed(runs_dir, name):
            continue
        index.setdefault(str(rec["fingerprint"]), []).append(name)
    return index
