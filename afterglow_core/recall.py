"""Past-fix recall: read-only retriever over runs/ bundles.

Given a fresh RED (trace excerpt + verdict text + the RTL file under
repair), rank past GREEN bundles by similarity and return the top-1
past fix as a small advisory context block (``PAST_FIX_RECALL``) for
the diagnosis prompt.

Scoring (one sentence): score = 0.5 * file-basename Jaccard + 0.5 *
signal-name Jaccard, both sets lowercased, rounded to 4dp.

Rules enforced here (mirroring agent/loop.py harvest hygiene):
- Retrieval is READ-ONLY over runs/: this module only ever opens
  files for reading and lists directories; it never writes bundles
  (no fingerprint.json backfill, no meta.json edits).
- A recall miss (nothing at/above threshold, or no eligible past
  fix) returns an empty block; the caller logs ``recall: MISS`` and
  leaves the prompt byte-identical.
- No model calls, no new dependencies: pure stdlib + the existing
  afterglow_core.fingerprint signal extractor.
"""

import json
import os
import re

from afterglow_core.fingerprint import extract_signals

# Eligibility + ranking knobs (defaults; tests override per-case).
FILE_WEIGHT = 0.5
SIGNAL_WEIGHT = 0.5
DEFAULT_THRESHOLD = 0.20
# Advisory block budget: small vs the 2500-token prompt cap so the
# recalled fix can never crowd out the failing window.
DEFAULT_BLOCK_TOKENS = 150
DEFAULT_LESSON_CHARS = 140
# Cap on the number of hunk spans named in one fix reference.
MAX_FIX_SPANS = 3


def approx_tokens(text: str) -> int:
    """Whitespace token ruler (same rule as agent/collect.py).

    Lives here (instead of importing agent.collect) so afterglow_core
    stays dependency-free toward agent/.
    """
    return len(str(text or "").split())


def _basename_set(paths) -> set:
    out = set()
    for p in paths or []:
        base = os.path.basename(str(p).strip()).lower()
        if base:
            out.add(base)
    return out


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def score_pair(query_files, query_signals, cand_files, cand_signals) -> float:
    """Deterministic file+signal overlap score in [0, 1].

    score = FILE_WEIGHT * Jaccard(file basenames, lowercased)
          + SIGNAL_WEIGHT * Jaccard(signal names, lowercased).
    Empty-vs-anything on either side contributes 0 for that side.
    """
    file_j = _jaccard(_basename_set(query_files),
                      _basename_set(cand_files))
    sig_j = _jaccard({str(s).lower() for s in (query_signals or [])},
                     {str(s).lower() for s in (cand_signals or [])})
    return round(FILE_WEIGHT * file_j + SIGNAL_WEIGHT * sig_j, 4)


_DIFF_PLUS_RE = re.compile(r"^\+\+\+\s+(?:b/)?(\S+)\s*$")
_DIFF_GIT_RE = re.compile(r"^diff --git\s+\S+\s+(?:b/)?(\S+)\s*$")
_HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@")
_DIAG_FENCE_RE = re.compile(r"## Diagnosis\s+```(?:\w*)\n(.*?)```",
                            re.DOTALL)


def candidate_files_from_diff(patch_diff: str) -> list:
    """RTL paths touched by a unified diff (read-only parse)."""
    files: list = []
    for line in (patch_diff or "").splitlines():
        m = _DIFF_PLUS_RE.match(line.strip())
        if m:
            path = m.group(1).strip()
            if path != "/dev/null" and path not in files:
                files.append(path)
            continue
        g = _DIFF_GIT_RE.match(line.strip())
        if g:
            path = g.group(1).strip()
            if path not in files:
                files.append(path)
    return files


def fix_lines_from_diff(patch_diff: str,
                        max_spans: int = MAX_FIX_SPANS) -> list:
    """New-file hunk spans per file: ``["path:start-end", ...]``.

    Single-line hunks collapse to ``path:line``. Capped at
    ``max_spans`` spans (first hunks win, deterministic).
    """
    spans: list = []
    cur = ""
    for line in (patch_diff or "").splitlines():
        s = line.strip()
        m = _DIFF_PLUS_RE.match(s)
        if m:
            cur = m.group(1).strip()
            continue
        h = _HUNK_RE.match(s)
        if h and cur and cur != "/dev/null":
            start = int(h.group(1))
            count = int(h.group(2)) if h.group(2) else 1
            if count <= 1:
                spans.append("%s:%d" % (cur, start))
            else:
                spans.append("%s:%d-%d" % (cur, start, start + count - 1))
            if len(spans) >= max_spans:
                break
    return spans


def _squash_one_line(text: str, limit: int) -> str:
    one = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(one) > limit:
        one = one[:limit].rstrip() + " ..."
    return one


def lesson_from_bundle(bundle_dir: str,
                       limit: int = DEFAULT_LESSON_CHARS) -> str:
    """One-line lesson: model diagnosis first, report.md fallback.

    Prefers ``api/text_response.json`` (``diagnosis`` field, verbatim
    model output) then the ``## Diagnosis`` fenced block in
    ``report.md``. Returns "" when neither yields a line.
    """
    try:
        with open(os.path.join(bundle_dir, "api", "text_response.json"),
                  "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        diag = str(payload.get("diagnosis") or "").strip()
        if diag:
            first = diag.splitlines()[0].strip() or diag
            return _squash_one_line(first, limit)
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(bundle_dir, "report.md"),
                  "r", encoding="utf-8") as fh:
            report = fh.read()
        m = _DIAG_FENCE_RE.search(report)
        if m:
            for line in m.group(1).splitlines():
                if line.strip():
                    return _squash_one_line(line, limit)
    except OSError:
        pass
    return ""


def _meta_allows(meta: dict) -> bool:
    """Harvest-style hygiene: no human-touched bundles as teachers."""
    if not isinstance(meta, dict):
        return False
    try:
        return int(meta.get("human_touches", 0) or 0) == 0
    except (TypeError, ValueError):
        return False


def load_past_fix(bundle_dir: str) -> dict | None:
    """Read-only load of one bundle's teachable fix (None if unusable).

    Requires a non-empty ``patch.diff``, harvest-style hygiene in
    ``meta.json`` (zero human touches), and a non-empty lesson.
    Never writes.
    """
    try:
        with open(os.path.join(bundle_dir, "patch.diff"),
                  "r", encoding="utf-8") as fh:
            diff = fh.read()
    except OSError:
        return None
    if not diff.strip():
        return None
    try:
        with open(os.path.join(bundle_dir, "meta.json"),
                  "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    if not _meta_allows(meta):
        return None
    files = candidate_files_from_diff(diff)
    if not files:
        rtl = str(meta.get("rtl_file") or "").strip()
        if rtl:
            files = [rtl]
    if not files:
        return None
    lesson = lesson_from_bundle(bundle_dir)
    if not lesson:
        return None
    signals: set = set()
    try:
        with open(os.path.join(bundle_dir, "fingerprint.json"),
                  "r", encoding="utf-8") as fh:
            fp = json.load(fh)
        signals = {str(s).lower() for s in (fp.get("signals") or [])}
    except (OSError, ValueError):
        signals = set()
    return {
        "case_id": str(meta.get("case_id") or ""),
        "files": files,
        "signals": sorted(signals),
        "fix_ref": "; ".join(fix_lines_from_diff(diff)) or files[0],
        "lesson": lesson,
    }


def rank_red(trace_excerpt: str, verdict_text: str, query_files,
             runs_dir: str, exclude_run_ids=(), exclude_case_ids=(),
             k: int = 3,
             threshold: float = DEFAULT_THRESHOLD) -> list:
    """Rank eligible past fixes for a fresh RED (read-only scan).

    Query signals come from the existing fingerprint extractor over
    ``trace_excerpt + verdict_text``. Only bundles with
    ``load_past_fix`` usable, not in the exclusion sets, and scoring
    at/above ``threshold`` are returned, best-first, up to ``k``.
    """
    excluded_runs = set(exclude_run_ids or ())
    excluded_cases = set(exclude_case_ids or ())
    query_signals = {s.lower() for s in
                     extract_signals("%s\n%s" % (trace_excerpt or "",
                                                 verdict_text or ""))}
    try:
        run_ids = sorted(os.listdir(runs_dir or ""))
    except OSError:
        return []
    scored: list = []
    for run_id in run_ids:
        if run_id in excluded_runs:
            continue
        bundle = os.path.join(runs_dir, run_id)
        fix = load_past_fix(bundle)
        if fix is None:
            continue
        if fix["case_id"] and fix["case_id"] in excluded_cases:
            continue
        score = score_pair(query_files, query_signals,
                           fix["files"], fix["signals"])
        if score < threshold:
            continue
        scored.append({"run_id": run_id, "score": score,
                       "case_id": fix["case_id"],
                       "fix_ref": fix["fix_ref"],
                       "lesson": fix["lesson"]})
    scored.sort(key=lambda e: (-e["score"], e["run_id"]))
    return scored[:max(0, int(k))]


def build_block(entry: dict | None,
                max_tokens: int = DEFAULT_BLOCK_TOKENS) -> str:
    """Render the PAST_FIX_RECALL advisory block ("" when no entry).

    The lesson is shortened until the block fits ``max_tokens``; when
    even the bare reference cannot fit, "" is returned (a miss, not
    an overflow).
    """
    if not entry:
        return ""
    lesson = str(entry.get("lesson") or "")
    run_id = str(entry.get("run_id") or "unknown")
    fix_ref = str(entry.get("fix_ref") or "unknown")
    score = entry.get("score", 0.0)
    header = ("### PAST_FIX_RECALL [from %s score=%.2f -- advisory past "
              "GREEN fix; patterns, not answers; re-sim decides]"
              % (run_id, float(score)))
    body = "- fix %s :: %s" % (fix_ref, lesson)
    block = header + "\n" + body
    while approx_tokens(block) > max_tokens:
        if len(lesson) <= 20:
            # Even a stub lesson cannot fit: a miss, never an
            # overflow.
            return ""
        lesson = lesson[:-20].rstrip() + " ..."
        body = "- fix %s :: %s" % (fix_ref, lesson)
        block = header + "\n" + body
    return block


def recall_for_red(trace_excerpt: str, verdict_text: str, query_files,
                   runs_dir: str, exclude_run_ids=(),
                   exclude_case_ids=(), k: int = 1,
                   threshold: float = DEFAULT_THRESHOLD,
                   max_tokens: int = DEFAULT_BLOCK_TOKENS) -> tuple:
    """Top-1 recall for a fresh RED: (block, info).

    ``info`` always carries provenance (``run_id``/``score``/
    ``fix_ref``/``status``/``reason``) so the caller can log it and
    record it in meta.json. ``block`` is "" on any miss.
    """
    ranked = rank_red(trace_excerpt, verdict_text, query_files,
                      runs_dir, exclude_run_ids, exclude_case_ids,
                      k=k, threshold=threshold)
    if not ranked:
        return "", {"status": "MISS", "run_id": "",
                    "score": 0.0, "fix_ref": "",
                    "reason": "no past fix at/above threshold %.2f"
                              % threshold}
    top = ranked[0]
    block = build_block(top, max_tokens=max_tokens)
    if not block:
        return "", {"status": "MISS", "run_id": top["run_id"],
                    "score": top["score"], "fix_ref": top["fix_ref"],
                    "reason": "top-1 block exceeds %d-token cap"
                              % max_tokens}
    return block, {"status": "HIT", "run_id": top["run_id"],
                   "score": top["score"], "fix_ref": top["fix_ref"],
                   "reason": "top-1 of %d ranked" % len(ranked)}


def apply_to_prompt(prompt: str, block: str,
                    max_prompt_tokens: int = 2500) -> tuple:
    """Append ``block`` to ``prompt`` iff it fits the prompt cap.

    Returns (new_prompt, applied, reason). Empty blocks and cap
    overflows leave the prompt byte-identical with applied=False.
    """
    if not block:
        return prompt, False, "recall: MISS"
    candidate = str(prompt or "") + "\n" + block
    if approx_tokens(candidate) > max_prompt_tokens:
        return prompt, False, "recall: MISS (block exceeds prompt cap)"
    return candidate, True, ("recall: HIT %s"
                             % block.splitlines()[0][:120])
