#!/usr/bin/env python3
"""Flake-adjusted scoring (Task H8, plan item 88, Wave H).

A SEPARATE report that adjusts a raw pass-count score by the number of
proven flakes. It consumes ``agent/evidence/flake_fp.py`` flake classes
read-only (``classify_flake`` is called, never modified) and never edits
``eval/score.py``.

API::

  adjusted_score(raw_rows, flake_evidence) -> {"score": int, "deductions": [...]}

``raw_rows`` is a sequence of ``{"case": str, "pass": bool}`` dicts; the
raw score is the count of passing rows. ``flake_evidence`` is a sequence
of dicts, each either carrying a ``"raw"`` failure string (classified
via ``flake_fp.classify_flake``) or a pre-classified ``"kind"``
(``empty`` | ``timeout`` | ``truncation`` | ``rate_limit`` |
``unknown``) with an optional ``"signature"``.

A flake is PROVEN when its kind is not ``"unknown"`` (i.e. retryable per
flake_fp). Each proven flake whose case matches a FAILING row grants one
credit: ``score = raw + proven_count`` and one entry is appended to
``deductions``. Proven flakes on passing rows, unknown flakes, and
duplicate evidence for the same case grant nothing (first evidence per
case wins). Every credit carries an evidence ``link`` of the form
``"flake:<case>:<kind>:<signature>"``.

Exit contract (CLI ``python3 agent/trust/scoreadjust.py``): prints a
demo-free usage line; real work happens via the ``adjusted_score``
function. Exit 2 on usage error.

stdlib imports only; the one repo dependency
(agent/evidence/flake_fp.py) is loaded read-only by file path, never
modified. No API calls, no keys, no network.
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if ROOT not in sys.path:
    # Standalone-CLI support: `python3 agent/trust/scoreadjust.py`
    # runs with agent/trust/ (not the repo root) on sys.path, so
    # bootstrap the root for absolute imports elsewhere.
    sys.path.insert(0, ROOT)


def _load_flake_fp():
    """Load agent/evidence/flake_fp.py read-only by file path.

    A direct ``from agent.evidence import flake_fp`` is unusable here:
    ``agent/evidence/__init__.py`` re-exports via bare ``from evidence
    ...`` imports that require ``agent/`` itself to be on sys.path
    (they fail under plain repo-root invocation). Loading the module
    file by path consumes exactly the flake classes and nothing else;
    existing files are untouched (new-files-only scope).
    """
    path = os.path.join(ROOT, "agent", "evidence", "flake_fp.py")
    spec = importlib.util.spec_from_file_location(
        "agent.trust._flake_fp", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


flake_fp = _load_flake_fp()

KNOWN_KINDS = ("empty", "timeout", "truncation", "rate_limit", "unknown")


def _checked_rows(raw_rows) -> list:
    """Normalize raw_rows to a list of (case, pass) pairs, or raise."""
    if isinstance(raw_rows, (str, bytes)) or not hasattr(raw_rows, "__iter__"):
        raise ValueError(
            "scoreadjust: raw_rows must be a sequence of "
            "{'case', 'pass'} dicts, got %r" % (raw_rows,))
    pairs = []
    for row in raw_rows:
        if not isinstance(row, dict):
            raise ValueError(
                "scoreadjust: each row must be a {'case', 'pass'} "
                "dict, got %r" % (row,))
        case = row.get("case")
        if not isinstance(case, str) or not case:
            raise ValueError(
                "scoreadjust: each row needs a non-empty str 'case', "
                "got %r" % (row,))
        passed = row.get("pass")
        if not isinstance(passed, bool):
            raise ValueError(
                "scoreadjust: row 'pass' must be True/False, got %r"
                % (row,))
        pairs.append((case, passed))
    return pairs


def _classify_evidence(flake_evidence) -> list:
    """Normalize flake_evidence to (case, kind, signature) triples."""
    if isinstance(flake_evidence, (str, bytes)) or not hasattr(
            flake_evidence, "__iter__"):
        raise ValueError(
            "scoreadjust: flake_evidence must be a sequence of dicts, "
            "got %r" % (flake_evidence,))
    triples = []
    for ev in flake_evidence:
        if not isinstance(ev, dict):
            raise ValueError(
                "scoreadjust: each evidence must be a dict with 'case' "
                "plus 'raw' or 'kind', got %r" % (ev,))
        case = ev.get("case")
        if not isinstance(case, str) or not case:
            raise ValueError(
                "scoreadjust: each evidence needs a non-empty str "
                "'case', got %r" % (ev,))
        if "raw" in ev:
            raw = ev["raw"]
            if not isinstance(raw, str):
                raise ValueError(
                    "scoreadjust: evidence 'raw' must be a string, "
                    "got %r" % (ev,))
            found = flake_fp.classify_flake(raw)
            triples.append((case, found["kind"], found["signature"]))
        elif "kind" in ev:
            kind = ev["kind"]
            if kind not in KNOWN_KINDS:
                raise ValueError(
                    "scoreadjust: evidence 'kind' must be one of %s, "
                    "got %r" % (KNOWN_KINDS, ev))
            sig = ev.get("signature", "")
            if not isinstance(sig, str):
                raise ValueError(
                    "scoreadjust: evidence 'signature' must be a "
                    "string, got %r" % (ev,))
            triples.append((case, kind, sig))
        else:
            raise ValueError(
                "scoreadjust: each evidence needs 'raw' or 'kind', "
                "got %r" % (ev,))
    return triples


def adjusted_score(raw_rows, flake_evidence) -> dict:
    """Adjust a raw pass-count score by the proven-flake count.

    Returns ``{"score": int, "deductions": [...]}`` where each deduction
    is ``{"case", "kind", "signature", "link", "credit": 1}``. Proven
    flakes (kind != "unknown") on failing rows each add exactly 1;
    everything else is ignored. Never raises on scoring content --
    only on malformed input (ValueError).
    """
    pairs = _checked_rows(raw_rows)
    triples = _classify_evidence(flake_evidence)
    failed = {case for case, passed in pairs if not passed}
    raw = sum(1 for _, passed in pairs if passed)
    seen = set()
    deductions = []
    for case, kind, sig in triples:
        if kind == "unknown":
            continue  # unproven: no credit
        if case not in failed:
            continue  # flake on a passing row: no credit
        if case in seen:
            continue  # first evidence per case wins
        seen.add(case)
        deductions.append({
            "case": case,
            "kind": kind,
            "signature": sig,
            "link": "flake:%s:%s:%s" % (case, kind, sig),
            "credit": 1,
        })
    return {"score": raw + len(deductions), "deductions": deductions}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        print("usage: scoreadjust.py (library only: adjusted_score())",
              file=sys.stderr)
        return 2
    print("scoreadjust: library only; call adjusted_score(raw_rows, "
          "flake_evidence)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
