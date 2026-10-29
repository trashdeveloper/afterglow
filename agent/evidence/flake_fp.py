#!/usr/bin/env python3
"""Task B8: Flake fingerprinting (plan item 18, Wave B SEE MORE).

Classifies transport/API failures by signature (EMPTY vs timeout vs
truncation vs rate-limit vs unknown) from a caller-passed raw exception
string / response dump. Input is TEXT the caller already holds (an
exception message, an error field, a raw JSON dump); this module never
calls the network, opens no files, and holds no API keys.

Interface::

  classify_flake(raw) -> dict with keys:
    kind ("empty" | "timeout" | "truncation" | "rate_limit" | "unknown"),
    signature (the exact matched marker substring, or "" for unknown),
    retryable (bool: is an immediate-or-delayed retry sensible?),
    suggested_wait_s (float seconds to wait before retrying).

Signatures (shapes seen in this repo's history, else disclosed
synthetic constructions of known format strings):
  EMPTY: "EMPTY content" / "empty content" (agent/loop.py text_call,
    e.g. runs/opt-live-04), "reasoning-only" (reasoning with no usable
    content), or an empty choices array ("choices": []).
  TIMEOUT: "down/timeout" (agent/loop.py LiveAPIError format),
    "timed out" / "timeout" / "TimeoutExpired" / "ETIMEDOUT" /
    "deadline exceeded" (e.g. agent/evidence/vcd_slice.py
    "trace run timed out after 300s").
  TRUNCATION: a max_tokens cut -- "finish_reason": "length"
    (e.g. runs/mutbigN-v4-12/api/text_response.json raw dump),
    "salvaged": true (loop salvage repaired a cut body),
    "salvage failed" / "not a JSON object" / "JSON parse failed" /
    "Unexpected end" / "Unterminated" / cut-off markers
    (e.g. runs/opt-live-02, runs/mutbigN-v3-21 attempt-2).
  RATE_LIMIT: "429" / "too many requests" / "rate limit"
    (no 429 exists in runs/ history yet; standard payload shape).

Priority on overlap: rate_limit > empty > timeout > truncation.
A bare '{"error": "no successful text call", "attempts": []}' wrapper
(e.g. runs/mutpair-v8-05/api/text_response.json) says nothing about
WHY the calls failed, so it is honestly "unknown", not invented.

Wait policy (pinned, tests assert exact values): empty 5.0s
(transport blip, short backoff), timeout 15.0s (server/network
recovery), truncation 0.0s (retryable at once -- no cooldown helps a
max_tokens cut; caller should salvage or raise the token budget),
rate_limit 60.0s (server asked us to back off), unknown 0.0s and NOT
retryable (unrecognized: caller must decide, never blind-retry).

Default-off contract (Wave B header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py and no caller is wired: agent/loop.py is untouched.

Pure stdlib (re); no API, no keys, no network.
"""
import re

_VERSION = "1"

# (kind, suggested_wait_s, [marker regexes]); first kind whose any
# marker matches wins. Marker text is returned verbatim as signature.
_RULES = (
    ("rate_limit", 60.0, [
        r"429",
        r"too many requests",
        r"rate[ _-]?limit(?:ed|s)?\b",
    ]),
    ("empty", 5.0, [
        r"EMPTY content",
        r"reasoning-only",
        r'"choices"\s*:\s*\[\]',
        r'"content"\s*:\s*null\b',
        r"empty content",
    ]),
    ("timeout", 15.0, [
        r"down/timeout",
        r"TimeoutExpired",
        r"ETIMEDOUT",
        r"timed?\s*-?\s*out",
        r"deadline exceeded",
        r"socket\.timeout",
    ]),
    ("truncation", 0.0, [
        r'"finish_reason"\s*:\s*"length"',
        r'"salvaged"\s*:\s*true\b',
        r"salvage failed",
        r"not a JSON object",
        r"JSON parse failed",
        r"Unexpected end",
        r"Unterminated",
        r"cut[- ]off",
        r"cut short",
    ]),
)

_COMPILED = tuple(
    (kind, wait, [re.compile(p, re.IGNORECASE) for p in pats])
    for kind, wait, pats in _RULES
)


def classify_flake(raw):
    """Fingerprint one transport/API failure string -> result dict.

    raw: exception message / error field / response dump (str).
    Non-string input raises ValueError (caller bug, never a guess).
    Empty/whitespace or unrecognized text -> "unknown", not retryable.
    """
    if not isinstance(raw, str):
        raise ValueError(
            "flake_fp: raw failure must be a string, got %r"
            % (type(raw),))
    for kind, wait, patterns in _COMPILED:
        for pat in patterns:
            match = pat.search(raw)
            if match:
                return {"kind": kind, "signature": match.group(0),
                        "retryable": True, "suggested_wait_s": wait}
    return {"kind": "unknown", "signature": "",
            "retryable": False, "suggested_wait_s": 0.0}
