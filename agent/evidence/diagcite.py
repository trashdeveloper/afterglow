#!/usr/bin/env python3
"""Task F8: Spec-cited diagnosis (plan item 68, Wave F EVIDENCE).

A diagnosis must cite a B2 spec chunk id, checked with the SAME
pass/reject contract as patches. This module consumes
``agent/repair/speccite.py`` (``extract_citations``, ``check``) and
applies it to free-text diagnoses instead of diffs:

Interface::

  check_diagnosis(text, valid_ids, strict=True)
    -> {"ok": bool, "cited": [...], "unknown": [...], "reason": str}

``valid_ids`` is the B2 corpus id set: an iterable of chunk-id
strings, or of chunk dicts with an ``"id"`` key (the shape returned
by ``spec_rag.retrieve``), so callers pass B2 output directly.

Verdicts (identical to speccite.check for patches):

- every citation in the corpus -> ok True, both modes.
- any citation outside the corpus -> ok False, BOTH modes (leniency
  covers MISSING citations, never fabricated ones).
- no citation + strict=True -> ok False (``UNCITED_STRICT``).
- no citation + strict=False -> ok True with warning
  (``UNCITED_LENIENT``).

``extract_citations`` is re-exported from speccite so diagnosis
callers extract with the exact same ``[*.md#cNN]`` shape.

Default-off contract (Wave F header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib + repo import (``agent/repair/speccite.py``); no API, no
keys, no network, no I/O.
"""

try:
    from agent.repair import speccite
except ImportError:  # pragma: no cover - fallback when agent/ is sys.path[0]
    from repair import speccite  # noqa: F401

# Re-use speccite extraction verbatim: a diagnosis citation is
# [<id>] with the B2 chunk-id shape *.md#cNN (optional ~N suffix).
extract_citations = speccite.extract_citations

# Strict is the default: an uncited diagnosis is rejected unless the
# caller explicitly opts into lenient mode (same contract as patches).
STRICT_DEFAULT = speccite.STRICT_DEFAULT


def check_diagnosis(text, valid_ids, strict=STRICT_DEFAULT):
    """Mechanically verify a diagnosis cites corpus spec chunk ids.

    Same pass/reject contract as ``speccite.check`` for patches:
    returns ``{"ok", "cited", "unknown", "reason"}``. Empty/non-string
    text or empty valid_ids raises ValueError; non-bool strict raises
    TypeError (all enforced by speccite.check).
    """
    return speccite.check(text, valid_ids, strict=strict)
