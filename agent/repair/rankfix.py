#!/usr/bin/env python3
"""Task C4: Fix ranking (plan item 24, Wave C FIX BETTER).

Collect up to 3 candidate patches across attempts, resim each, ship
the smallest passing patch.

Interface::

  diff_size(patch) -> int
  collect_candidates(attempts, limit=3) -> list[str]
  rank_candidates(candidates, resim_fn, limit=3)
    -> {"winner": patch|None, "results": [...]}

``collect_candidates`` pulls patch texts out of loop-style attempt
records (``{"attempt": N, "patch": <diff>}``, the shape produced by
``loop.make_stack_entry``) in encounter order, skipping attempts with
no usable patch, capped at ``limit``. Raw diff strings are also
accepted and passed through. This is the "existing attempt machinery"
seam: it consumes the attempt-record shape, not a new protocol.

``rank_candidates`` resims EVERY considered candidate via
``resim_fn(patch)`` and ships the smallest passing patch. ``resim_fn``
may return a bool or a ``{"passed": bool}`` dict; a raised exception
is recorded as a fail (``passed False`` + ``error``), never a pass
and never an abort of the remaining candidates. Size is ``diff_size``
(new-side + old-side content lines, same convention as
``guard.count_patch_lines``); ties break by earliest attempt order.
With no passing candidate ``winner`` is None. Only the first
``limit`` candidates are considered/resimmed; extras are ignored.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib; no API, no keys, no network, no I/O.
"""

MAX_CANDIDATES = 3


def _require_limit(limit):
    """Validate the candidate cap; return it or raise."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError(
            "rankfix: limit must be an int in [1, %d], got %r"
            % (MAX_CANDIDATES, limit))
    if limit < 1 or limit > MAX_CANDIDATES:
        raise ValueError(
            "rankfix: limit must be in [1, %d], got %d"
            % (MAX_CANDIDATES, limit))
    return limit


def diff_size(patch):
    """Count +/- content lines, excluding ``+++``/``---`` headers.

    Same convention as ``guard.count_patch_lines`` (reimplemented here
    so this module stays decoupled from agent/guard.py): a smaller
    number means a smaller, more reviewable patch.
    """
    if not isinstance(patch, str) or not patch.strip():
        raise ValueError(
            "rankfix: patch must be a non-empty diff string, "
            "got %r" % (patch,))
    n = 0
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            n += 1
    return n


def collect_candidates(attempts, limit=MAX_CANDIDATES):
    """Collect up to ``limit`` patch texts from attempt records.

    ``attempts`` is a list/tuple of loop-style attempt dicts
    (``{"attempt": N, "patch": <diff>}``) or raw diff strings.
    Entries without a usable (non-empty string) patch are skipped;
    collection stops at ``limit`` entries. Returns the patches in
    encounter order (attempt order). No usable patch -> [].
    """
    limit = _require_limit(limit)
    if not isinstance(attempts, (list, tuple)):
        raise TypeError(
            "rankfix: attempts must be a list/tuple of attempt "
            "records or diff strings, got %r" % (attempts,))
    out = []
    for entry in attempts:
        if isinstance(entry, dict):
            patch = entry.get("patch")
        else:
            patch = entry
        if isinstance(patch, str) and patch.strip():
            out.append(patch)
        if len(out) >= limit:
            break
    return out


def _resim_passed(resim_fn, patch):
    """Run one resim; return (passed, error_or_None). Never raises."""
    try:
        result = resim_fn(patch)
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        return False, "%s: %s" % (type(exc).__name__, exc)
    if isinstance(result, dict):
        return bool(result.get("passed", False)), None
    return bool(result), None


def rank_candidates(candidates, resim_fn, limit=MAX_CANDIDATES):
    """Resim each of the first ``limit`` candidates; ship smallest pass.

    Returns ``{"winner": <patch or None>, "results": [...]}`` where each
    result row is ``{"patch", "size", "passed"}`` plus ``"error"`` when
    the resim itself raised. Every considered candidate is resimmed
    (no first-win short-circuit) so the winner is genuinely the
    smallest passing patch; ties break by input (attempt) order.
    """
    limit = _require_limit(limit)
    if callable(resim_fn) is False:
        raise TypeError(
            "rankfix: resim_fn must be callable, got %r" % (resim_fn,))
    if not isinstance(candidates, (list, tuple)) or not candidates:
        raise ValueError(
            "rankfix: candidates must be a non-empty list of diff "
            "strings, got %r" % (candidates,))
    considered = []
    for entry in candidates:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                "rankfix: candidates must be non-empty diff strings, "
                "got %r" % (entry,))
        considered.append(entry)
        if len(considered) >= limit:
            break
    results = []
    for patch in considered:
        passed, error = _resim_passed(resim_fn, patch)
        row = {"patch": patch, "size": diff_size(patch), "passed": passed}
        if error is not None:
            row["error"] = error
        results.append(row)
    passing = [row for row in results if row["passed"]]
    if not passing:
        winner = None
    else:
        best_size = min(row["size"] for row in passing)
        winner = next(
            row["patch"] for row in results
            if row["passed"] and row["size"] == best_size)
    return {"winner": winner, "results": results}
