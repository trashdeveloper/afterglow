#!/usr/bin/env python3
"""Task C6: Spec-grounded fix (plan item 26, Wave C FIX BETTER).

Mechanical citation check: a patch must cite a spec chunk id from the
B2 corpus (``agent/evidence/spec_rag.py``). Uncited patches are
rejected in strict mode; invented citations (ids outside the corpus)
are rejected in BOTH modes.

Interface::

  extract_citations(text) -> [chunk ids in encounter order, deduped]
  check(patch_text, valid_ids, strict=True)
    -> {"ok": bool, "cited": [...], "unknown": [...], "reason": str}

A citation is ``[<id>]`` where ``<id>`` has the B2 chunk-id shape
``*.md#cNN`` (with optional ``~N`` dedup suffix, see
``spec_rag.chunk_markdown`` / ``build_corpus``). Bracket text in any
other shape (``[NOTE]``, ``[foo#c01]``) is NOT a spec citation and is
ignored by extraction.

``valid_ids`` is the B2 corpus id set: an iterable of chunk-id
strings, or of chunk dicts with an ``"id"`` key (the shape returned
by ``spec_rag.retrieve``), so callers pass B2 output directly. A bare
string counts as one id. Empty/None raises ValueError -- checking
against nothing would fabricate a verdict.

Verdicts:

- every citation in the corpus -> ok True, both modes.
- any citation outside the corpus -> ok False, BOTH modes (leniency
  covers MISSING citations, never fabricated ones).
- no citation + strict=True -> ok False (``UNCITED_STRICT``).
- no citation + strict=False -> ok True with warning
  (``UNCITED_LENIENT``).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``re`` only); no API, no keys, no network, no I/O.
"""

import re

# B2 chunk-id shape: "<source>.md#c<NN>" with optional "~N" dedup
# suffix (spec_rag ids: e.g. "AGENTS.md#c00",
# "2026-10-26-rtl-debug-framework-design.md#c03").
_CITATION = re.compile(
    r"\[([A-Za-z0-9_][A-Za-z0-9_.\-]*\.md#c\d+(?:~\d+)?)\]")

# Strict is the default: an uncited patch is rejected unless the
# caller explicitly opts into lenient mode.
STRICT_DEFAULT = True

# Pinned refusal / warning reasons (tests assert these verbatim).
UNCITED_STRICT = (
    "speccite: strict mode rejects uncited patch "
    "(no [*.md#cNN] citation found)")
UNCITED_LENIENT = (
    "speccite: non-strict mode passes uncited patch with warning "
    "(no [*.md#cNN] citation found)")

_UNKNOWN_PREFIX = (
    "speccite: patch cites unknown chunk id(s) not in corpus: ")
_CITED_PREFIX = "speccite: patch cites spec chunk id(s): "


def extract_citations(text):
    """Find B2-shaped ``[<id>]`` citations, encounter order, deduped.

    Non-string input raises TypeError; any other bracket shape is
    ignored (not a spec citation). Empty text has no citations -> [].
    """
    if not isinstance(text, str):
        raise TypeError(
            "speccite: text must be a string, got %r" % (text,))
    seen = set()
    found = []
    for m in _CITATION.finditer(text):
        cid = m.group(1)
        if cid not in seen:
            seen.add(cid)
            found.append(cid)
    return found


def _as_id_set(valid_ids):
    """Normalize valid_ids to a set of chunk-id strings or raise."""
    if isinstance(valid_ids, str):
        items = [valid_ids]
    elif isinstance(valid_ids, (list, tuple, set, frozenset)):
        items = list(valid_ids)
    else:
        raise TypeError(
            "speccite: valid_ids must be an iterable of chunk ids "
            "or chunk dicts, got %r" % (valid_ids,))
    ids = set()
    for item in items:
        if isinstance(item, dict):
            item = item.get("id")
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                "speccite: valid_ids holds a non-id entry %r: "
                "refusing to check against a half-built corpus"
                % (item,))
        ids.add(item)
    if not ids:
        raise ValueError(
            "speccite: valid_ids is empty: refusing to check "
            "against nothing")
    return ids


def check(patch_text, valid_ids, strict=STRICT_DEFAULT):
    """Mechanically verify a patch cites corpus spec chunk ids.

    Returns ``{"ok", "cited", "unknown", "reason"}`` where ``cited``
    holds corpus-known citations (encounter order), ``unknown`` holds
    citations outside the corpus, and ``reason`` is a pinned message.
    Empty/non-string patch or empty valid_ids raises ValueError;
    non-bool strict raises TypeError.
    """
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise ValueError(
            "speccite: patch_text must be a non-empty string, "
            "got %r" % (patch_text,))
    if not isinstance(strict, bool):
        raise TypeError(
            "speccite: strict must be a bool, got %r" % (strict,))
    corpus = _as_id_set(valid_ids)
    found = extract_citations(patch_text)
    cited = [c for c in found if c in corpus]
    unknown = [c for c in found if c not in corpus]
    if unknown:
        return {"ok": False, "cited": cited, "unknown": unknown,
                "reason": _UNKNOWN_PREFIX + ", ".join(unknown)}
    if cited:
        return {"ok": True, "cited": cited, "unknown": [],
                "reason": _CITED_PREFIX + ", ".join(cited)}
    if strict:
        return {"ok": False, "cited": [], "unknown": [],
                "reason": UNCITED_STRICT}
    return {"ok": True, "cited": [], "unknown": [],
            "reason": UNCITED_LENIENT}
