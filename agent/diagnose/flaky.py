#!/usr/bin/env python3
"""Task F3: Flaky-triage classifier (plan item 63).

Triage a batch of failing attempts into REAL (genuine deterministic
bug), FLAKE (empty-output / transport errors dominate), or SUSPECT
(mixed evidence, needs more runs).

Interface::

  classify(attempts) -> {"verdict", "confidence", "reasons", "loud"}

``attempts`` is a list of dicts, one per failing attempt. Each
attempt may carry:

  checksum / sha256 : hex digest of the failing output (a usable,
    non-blank checksum marks a signal-bearing attempt).
  output           : captured output text; "" / blank means empty output.
  error            : transport/API error marker (any truthy value, e.g.
    "live text call returned EMPTY content (...)" from the wave-2
    05-storm precedent); truthy always marks the attempt a flake.
  empty            : explicit True marks an empty-output attempt a flake.
  status           : optional string; "empty" / "error" / "transport" /
    "timeout" / "flake" marks the attempt a flake.

Verdict rules:

  REAL   : every attempt carries a usable checksum and all checksums
    are identical (deterministic failure, not a flake).
  FLAKE  : flake attempts are a strict majority (> 1/2) of the batch.
    A unanimous flake batch (e.g. triple-EMPTY) is a LOUD flake:
    ``loud`` is True and the reasons say so (fail loud, never
    invent a diagnosis off empty output).
  SUSPECT: anything else (mixed checksums, or flakes at/below half).

``confidence`` is the fraction agreeing with the verdict: 1.0 for a
unanimous REAL / unanimous FLAKE, else flakes/n for FLAKE, else the
largest agreeing group (checksum or flake share) over n for SUSPECT.
``reasons`` is a non-empty list of human-readable evidence strings.
``loud`` is True only for a unanimous FLAKE batch.

Errors are loud: a non-list ``attempts``, an empty batch, or a
non-dict element raises (TypeError / ValueError). An attempt with no
usable checksum and no flake marker corroborates nothing, so it
counts as a flake (reason: "no usable checksum").

Default-off contract: this module runs ONLY on explicit call. It
imports nothing from agent/loop.py and no caller is wired:
agent/loop.py is untouched.

Pure stdlib (no imports at all); no API, no keys, no network, no I/O.
"""

_VERSION = "1"

_FLAKE_STATUSES = frozenset((
    "empty", "error", "transport", "timeout", "flake", "flaked", "infra",
))


def _usable_checksum(attempt):
    """Return the stripped checksum string, or "" when unusable."""
    for key in ("checksum", "sha256"):
        value = attempt.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_flake(attempt):
    """True when the attempt is an empty-output / transport failure."""
    if attempt.get("error"):
        return True
    if attempt.get("empty"):
        return True
    status = attempt.get("status")
    if isinstance(status, str) and status.strip().lower() in _FLAKE_STATUSES:
        return True
    if not _usable_checksum(attempt):
        return True
    return False


def classify(attempts):
    """Triage failing attempts -> verdict dict."""
    if not isinstance(attempts, list):
        raise TypeError(
            "flaky: attempts must be a list of dicts, got %r"
            % (type(attempts),))
    if not attempts:
        raise ValueError("flaky: attempts must not be empty (nothing to triage)")
    for i, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            raise TypeError(
                "flaky: attempt %d must be a dict, got %r" % (i, type(attempt)))

    total = len(attempts)
    flake_set = set(i for i, a in enumerate(attempts) if _is_flake(a))
    flakes = len(flake_set)
    groups = {}
    for i, attempt in enumerate(attempts):
        if i in flake_set:
            continue
        checksum = _usable_checksum(attempt)
        groups.setdefault(checksum, []).append(i)

    if flakes == 0 and len(groups) == 1:
        checksum = next(iter(groups))
        return {
            "verdict": "REAL",
            "confidence": 1.0,
            "reasons": [
                "all %d attempt(s) fail with the same checksum %s"
                % (total, checksum),
            ],
            "loud": False,
        }

    if flakes > total / 2:
        confidence = flakes / total
        loud = flakes == total
        if loud:
            reasons = [
                "loud FLAKE: all %d attempt(s) are empty-output/transport "
                "failures (triple-EMPTY precedent: fail loud, no diagnosis "
                "invented)" % total,
            ]
        else:
            reasons = [
                "FLAKE: %d of %d attempt(s) are empty-output/transport "
                "failures (majority)" % (flakes, total),
            ]
        return {
            "verdict": "FLAKE",
            "confidence": confidence,
            "reasons": reasons,
            "loud": loud,
        }

    shares = [len(idx) for idx in groups.values()] + ([flakes] if flakes else [])
    best = max(shares) if shares else 0
    parts = ["%d attempt(s) share checksum %s" % (len(idx), cs)
             for cs, idx in sorted(groups.items())]
    if flakes:
        parts.append("%d attempt(s) are empty-output/transport failures"
                     % flakes)
    return {
        "verdict": "SUSPECT",
        "confidence": best / total,
        "reasons": ["SUSPECT: mixed evidence -- " + "; ".join(parts)],
        "loud": False,
    }
