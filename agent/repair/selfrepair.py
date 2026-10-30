#!/usr/bin/env python3
"""Task G7: Fixer self-repair (plan item 77).

Produces::

  fallback_for(attribution) -> strategy
  retry_with(failure, fallback_fn) -> {"strategy", "result"}

When a fixer fails, the failure is attributed with one of
``agent/trust/blame.py::classify_red``'s failure classes ("tool",
"tb", "model", "infra") and each class maps to a pinned fallback
strategy:

  model -> "re-ask"            (unparseable model output: re-ask with a
                                stricter output format)
  tool  -> "shrink-patch"      (guard rejected our patch: shrink/re-shape
                                it before retrying)
  tb    -> "strengthen-check"  (testbench untrustworthy: strengthen the
                                oracle/check before trusting a retry)
  infra -> "backoff-retry"     (transport/API delivered nothing: back
                                off and retry unchanged)

``fallback_for`` accepts either the bare attribution string or a
classify_red report dict ``{"attribution", "reasons"}``. It is a
standalone reader of that format (mirroring
``agent/repair/bisectreg.py``'s no-regguard-import precedent): this
module imports nothing from agent/ -- raw blame *evidence* dicts are
refused loudly so the caller must classify first via
``blame.classify_red`` (never guess a fallback from unclassified
signals).

``retry_with(failure, fallback_fn)`` resolves the strategy via
``fallback_for`` from ``failure["attribution"]`` (a bare attribution
string is also accepted as ``failure``), then calls
``fallback_fn(failure_dict)`` and returns
``{"strategy": <strategy>, "result": <fallback return>}``. The
failure dict is passed through untouched (never mutated).
``fallback_fn`` exceptions propagate unchanged -- never caught, never
a verdict. Unknown/empty attributions, non-dict failures, and
non-callable fallbacks raise TypeError/ValueError loudly before any
verdict -- never an invented fallback.

Honesty contract (mirrors ``agent/repair/repairrace.py``: vacuous
input raises, probe errors propagate, deterministic -- adapted from
strategy-race to single-fallback because a failed fixer has exactly
one attributed cause, so the graded shape here is "unparseable-output
-> re-ask and the retry greens on a fixture" instead of first-green
portfolio order).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py, agent/guard.py, or agent/trust/*, never changes guard
verdicts, and no caller is wired: agent/loop.py and agent/guard.py
are untouched.

Pure stdlib (no imports); no API, no keys, no network, no I/O.
"""

_FALLBACKS = {
    "model": "re-ask",
    "tool": "shrink-patch",
    "tb": "strengthen-check",
    "infra": "backoff-retry",
}


def _resolve_attribution(attribution):
    """Extract and validate a blame attribution string."""
    if isinstance(attribution, dict):
        if "attribution" not in attribution:
            raise ValueError(
                "selfrepair: dict input has no 'attribution' key "
                "(classify it first with blame.classify_red, "
                "got %r)" % (attribution,))
        return _resolve_attribution(attribution["attribution"])
    if not isinstance(attribution, str):
        raise TypeError(
            "selfrepair: attribution must be a blame failure-class "
            "string or classify_red report dict, got %r" % (attribution,))
    if attribution not in _FALLBACKS:
        raise ValueError(
            "selfrepair: unknown attribution %r: must be one of %s "
            "(blame.classify_red failure classes)" % (
                attribution, sorted(_FALLBACKS)))
    return attribution


def fallback_for(attribution):
    """Map a blame failure class to its pinned fallback strategy.

    Accepts a bare attribution string ("tool"|"tb"|"model"|"infra")
    or a classify_red report dict. Returns the strategy name.
    Raises TypeError/ValueError on anything else.
    """
    return _FALLBACKS[_resolve_attribution(attribution)]


def retry_with(failure, fallback_fn):
    """Retry a failed fix with its attribution's fallback strategy.

    ``failure`` is a dict carrying an "attribution" entry (bare
    blame-class string or nested classify_red report), or the bare
    attribution string itself. ``fallback_fn(failure_dict)`` is the
    injected fallback probe (all simulated, no live API). Returns
    ``{"strategy": <pinned strategy>, "result": <probe return>}``.
    Probe exceptions propagate; vacuous input raises loudly.
    """
    if isinstance(failure, str):
        resolved = failure
        failure_dict = {"attribution": failure}
    elif isinstance(failure, dict):
        if "attribution" not in failure:
            raise ValueError(
                "selfrepair: failure dict has no 'attribution' key "
                "(classify it first with blame.classify_red, "
                "got %r)" % (failure,))
        resolved = failure["attribution"]
        failure_dict = failure
    else:
        raise TypeError(
            "selfrepair: failure must be a dict with 'attribution' "
            "or a blame failure-class string, got %r" % (failure,))
    strategy = fallback_for(resolved)
    if not callable(fallback_fn):
        raise TypeError(
            "selfrepair: fallback_fn must be a callable probe taking "
            "the failure dict, got %r" % (fallback_fn,))
    result = fallback_fn(failure_dict)  # raises propagate, never a verdict
    return {"strategy": strategy, "result": result}
