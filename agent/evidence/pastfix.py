#!/usr/bin/env python3
"""Task B4: Past-fix memory (plan item 14, Wave B SEE MORE).

EvidencePack extension: retrieves similar past diagnoses from
``runs/*/meta.json`` by signal-name overlap.

Interfaces:
  tokenize(text) -> set of tokens
  jaccard(a, b) -> float in [0, 1]
  extract_candidate_tokens(meta) -> set of tokens
  diagnosis_snippet(meta, max_chars) -> str (stored snippet, "" if none)
  retrieve(query_signals, runs_dir, top_k) -> ranked hits
  format_prompt_section(results, max_chars) -> str (cited, "" if none)
  retrieve_for_prompt(query_signals, runs_dir, enable_pastfix, top_k)
    -> EvidencePack section dict

Query: a list of signal names (fault context from the caller, e.g.
suspect signals scraped from the failing trace). The runs directory
is ALWAYS passed by the caller -- this module never hardcodes
``runs`` or any other path.

Candidate signal/token sets are extracted from the stored attempt
records inside each ``meta.json``: every ``attempts[*]`` summary /
diagnosis / gate-signal string, plus the fault lines recorded
alongside them in the same bundle (``mutant_record`` before/after/
file and ``faults[*]`` entries). The top-level ``diagnosis`` field,
when present, is the PAYLOAD returned as the snippet -- it is not
scored, so a run is never ranked by its own snippet text.

Scoring: Jaccard overlap of the query token set vs the candidate
token set, rounded to 4dp. Candidates scoring 0.0 are dropped: an
unknown query returns [] instead of an invented hit. Ties break by
run-id (deterministic). Reported run-ids are directory names found
on disk -- never the in-file ``run_id`` field, never invented.

Default-off contract (Wave B header: every addition is additive +
default-off): retrieve_for_prompt with enable_pastfix=False (the
default) returns {"pastfix_enabled": False, "pastfix_hits": [],
"prompt_section": ""} WITHOUT listing any directory or reading any
file. The loop (agent/loop.py) is NOT edited by this task; prompt
builders opt in by passing enable_pastfix=True with a caller-built
signal list and runs dir.

Honesty contract (mirrors spec_rag.py: never invent evidence):
- An unknown query (zero token overlap with every bundle) returns
  [], never a best-effort guess. Every returned run-id names a
  directory holding a readable meta.json under the caller-passed
  runs dir.
- Unreadable / corrupt meta.json files are skipped, never guessed.
- An empty or missing runs dir returns [] (a miss, not a crash).
- format_prompt_section([]) is "" -- no hits, no section, no citation.
- enable_pastfix=True with an empty query or no runs dir raises
  ValueError instead of retrieving against nothing / anywhere.

Pure stdlib (os, re, json); no API, no keys, no network.
"""
import json
import os
import re

DEFAULT_TOP_K = 3
DEFAULT_SNIPPET_CHARS = 600

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def tokenize(text):
    """Lowercase alphanumeric/underscore token set (keeps RTL names)."""
    if not isinstance(text, str):
        raise ValueError("tokenize needs a string, got %r" % (text,))
    return set(_TOKEN_RE.findall(text.lower()))


def jaccard(a, b):
    """Jaccard similarity of two token sets; empty either side -> 0.0."""
    a = set(a)
    b = set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _strs(*values):
    out = []
    for v in values:
        if isinstance(v, str) and v.strip():
            out.append(v)
    return out


def extract_candidate_tokens(meta):
    """Signal/token set for one stored bundle (never raises on shape).

    Sources, all inside the passed meta dict: attempts[*] summary /
    diagnosis / diagnosis_text / gate_signal strings, plus the fault
    lines stored alongside them (mutant_record before/after/file and
    faults[*] before/after/file). Anything missing or misshapen
    contributes nothing -- an unscorable bundle simply scores 0.0
    against every query and is dropped by retrieve().
    """
    if not isinstance(meta, dict):
        return set()
    parts = []
    attempts = meta.get("attempts")
    if isinstance(attempts, list):
        for att in attempts:
            if not isinstance(att, dict):
                continue
            parts.extend(_strs(att.get("summary"),
                               att.get("diagnosis"),
                               att.get("diagnosis_text"),
                               att.get("gate_signal")))
    mr = meta.get("mutant_record")
    if isinstance(mr, dict):
        parts.extend(_strs(mr.get("before"), mr.get("after"),
                           mr.get("file")))
        faults = mr.get("faults")
        if isinstance(faults, list):
            for f in faults:
                if not isinstance(f, dict):
                    continue
                parts.extend(_strs(f.get("before"), f.get("after"),
                                   f.get("file")))
    return tokenize("\n".join(parts))


def diagnosis_snippet(meta, max_chars=DEFAULT_SNIPPET_CHARS):
    """Stored diagnosis snippet for one bundle, "" when none stored.

    Preference: top-level diagnosis / diagnosis_text, then the first
    non-empty attempts[*] diagnosis / diagnosis_text, then the first
    non-empty attempts[*] summary. Whitespace is squashed to one line;
    longer snippets are cut at max_chars with " [...]".
    """
    if max_chars is not None and max_chars <= 0:
        raise ValueError("max_chars must be positive, got %r"
                         % (max_chars,))
    if not isinstance(meta, dict):
        return ""
    cand = ""
    for key in ("diagnosis", "diagnosis_text"):
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            cand = v
            break
    if not cand:
        attempts = meta.get("attempts")
        if isinstance(attempts, list):
            for att in attempts:
                if not isinstance(att, dict):
                    continue
                for key in ("diagnosis", "diagnosis_text"):
                    v = att.get(key)
                    if isinstance(v, str) and v.strip():
                        cand = v
                        break
                if cand:
                    break
    if not cand:
        attempts = meta.get("attempts")
        if isinstance(attempts, list):
            for att in attempts:
                if not isinstance(att, dict):
                    continue
                v = att.get("summary")
                if isinstance(v, str) and v.strip():
                    cand = v
                    break
    one = re.sub(r"\s+", " ", str(cand or "")).strip()
    if not one:
        return ""
    if max_chars is not None and len(one) > max_chars:
        one = one[:max_chars].rstrip() + " [...]"
    return one


def _query_set(query_signals):
    if not query_signals:
        return set()
    try:
        items = list(query_signals)
    except TypeError:
        return set()
    return tokenize(" ".join(str(s) for s in items))


def retrieve(query_signals, runs_dir, top_k=DEFAULT_TOP_K):
    """Rank past bundles by signal-name overlap, best-first.

    query_signals: caller-built list of signal names (fault context).
    runs_dir: caller-passed runs directory (never hardcoded here).
    Returns [{run_id, score, diagnosis}] with score = round(Jaccard,
    4); zero-overlap bundles are dropped. run_id is the directory
    name found on disk. Empty query, missing/unreadable runs dir, or
    no overlap all return [] (a miss, never an invention).
    """
    if top_k is not None and (not isinstance(top_k, int)
                              or top_k <= 0):
        raise ValueError("top_k must be a positive int, got %r"
                         % (top_k,))
    qtokens = _query_set(query_signals)
    if not qtokens:
        return []
    if not isinstance(runs_dir, str) or not runs_dir:
        return []
    try:
        names = sorted(os.listdir(runs_dir))
    except OSError:
        return []
    hits = []
    for name in names:
        bundle = os.path.join(runs_dir, name)
        meta_path = os.path.join(bundle, "meta.json")
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        ctokens = extract_candidate_tokens(meta)
        score = jaccard(qtokens, ctokens)
        if score <= 0.0:
            continue
        hits.append({"run_id": name,
                     "score": round(score, 4),
                     "diagnosis": diagnosis_snippet(meta)})
    hits.sort(key=lambda h: (-h["score"], h["run_id"]))
    if top_k is not None:
        hits = hits[:top_k]
    return hits


def format_prompt_section(results, max_chars=None):
    """Render ranked hits as a cited prompt section, "" when empty.

    Every citation is a real run-id found on disk ([<run-id>]); the
    snippet is the verbatim stored diagnosis (squashed one-line by
    diagnosis_snippet). Empty results -> "" (no invented citations).
    """
    if not results:
        return ""
    if max_chars is not None and max_chars <= 0:
        raise ValueError("max_chars must be positive, got %r"
                         % (max_chars,))
    parts = ["Past-fix memory (Jaccard signal overlap, cited run-ids):"]
    for hit in results:
        parts.append("[%s] (score %.4f) :: %s"
                     % (hit.get("run_id", "?"),
                        float(hit.get("score", 0.0)),
                        hit.get("diagnosis", "")))
    text = "\n".join(parts)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + " [...]"
    return text


def retrieve_for_prompt(query_signals=None, runs_dir=None,
                        enable_pastfix=False, top_k=DEFAULT_TOP_K):
    """EvidencePack section dict with a default-off past-fix section.

    enable_pastfix=False (default): returns {"pastfix_enabled":
    False, "pastfix_hits": [], "prompt_section": ""} WITHOUT listing
    directories or reading files -- existing loop/prompt behavior is
    byte-identical.
    enable_pastfix=True: requires a non-empty caller-built
    query_signals list and a caller-passed runs_dir (ValueError
    otherwise); scans <runs_dir>/*/meta.json and returns ranked hits.
    """
    pack = {"pastfix_enabled": bool(enable_pastfix),
            "pastfix_hits": [], "prompt_section": ""}
    if not enable_pastfix:
        return pack
    if not _query_set(query_signals):
        raise ValueError("enable_pastfix=True needs a non-empty "
                         "query_signals list (fault signal names): "
                         "refusing to retrieve against nothing")
    if not isinstance(runs_dir, str) or not runs_dir.strip():
        raise ValueError("enable_pastfix=True needs a caller-passed "
                         "runs_dir path: refusing to fall back to a "
                         "hardcoded directory")
    hits = retrieve(query_signals, runs_dir, top_k=top_k)
    pack["pastfix_hits"] = hits
    pack["prompt_section"] = format_prompt_section(hits)
    return pack
