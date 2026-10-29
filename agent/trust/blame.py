"""Blameless RED attribution classifier (Task D9-TRUE, plan item 39, Wave D).

Every RED (still-red after retries) is attributed to a system
component -- never to a person -- so the eval board can show honest
reds with a pinned cause. The classifier reads a RED bundle's
evidence (verdict_before, resim/exit codes, guard verdict, API error
markers, trace shape) and returns one of {tool, tb, model, infra} with
reasons, written to meta.

Interface::

  classify_red(evidence) -> {"attribution", "reasons"}
  record_blame(meta, evidence) -> same report; sets meta["blame"]

Pinned mapping:
- transport/API empty errors -> "infra" (nothing to diagnose; the
  harness/API layer delivered no usable response).
- guard-rejected patch -> "tool" (our own tooling refused the patch;
  the model never got a fair resim).
- resim PASS/FAIL mismatch vs oracle -> "tb" (the testbench cannot
  tell pass from fail; its verdict is untrustworthy).
- unparseable model output after retries -> "model" (the model never
  produced a usable diagnosis/patch).

Precedence on combined signals: infra > tool > tb > model. A
transport failure poisons everything downstream; a guard rejection
means resim evidence is stale; a TB mismatch outranks a parse
complaint. Empty/unknown evidence defaults to "model": the RED stands
and the model is the last writer.

Evidence keys (all optional; missing means unknown):
  api_error / transport_error / api_empty_response: truthy on
    transport or API failure (message string or True).
  api_error_markers: non-empty list/tuple/set/str (or True) of API
    error markers such as EMPTY_BODY / HTTP_503.
  trace_empty: True when the sim produced zero trace lines.
  guard / guard_verdict / guard_rejected: guard dict with
    accepted/ok/passed False, verdict string "rejected" (any case),
    or guard_rejected True.
  resim_tohost / oracle_tohost / expected_tohost, or resim / oracle /
    expected dicts with a "tohost" entry: PASS/FAIL pair mismatch.
  resim_mismatch: True on explicit resim-vs-oracle disagreement.
  exit_code + resim_tohost contradiction: nonzero exit with PASS
    tohost (harness/TB exit contract broken) -> tb.
  parse_ok False / model_unparseable True / retries_exhausted True:
    model output never parsed after the retry budget.
  verdict_before / exit_code / trace_lines / retries / max_retries:
    context echoed into reasons, never a primary trigger.

Validation: non-dict evidence raises ValueError (never invent an
attribution from junk). record_blame requires a dict meta and never
half-writes it: the report is computed first, meta is touched only on
success.

Default-off contract (Wave D header: additive + default-off): this
module runs ONLY on explicit call. It is imported by nothing in
agent/loop.py, agent/guard.py, or eval/score.py -- those files are
untouched.

Pure stdlib (no imports at all): no API, no keys, no network, no
file or device use.
"""

ATTRIBUTIONS = ("tool", "tb", "model", "infra")

_REJECT_WORDS = {"rejected", "reject", "fail", "failed"}


def _truthy(value):
    """True for a present failure signal (message, True, non-empty)."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) > 0
    return True


def _norm_tohost(value):
    """Normalize a tohost verdict to PASS/FAIL, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    if "PASS" in text:
        return "PASS"
    if "FAIL" in text:
        return "FAIL"
    return None


def _get_tohost(evidence, *names):
    """First normalizable tohost found under any of the key names."""
    for name in names:
        value = evidence.get(name)
        if isinstance(value, dict):
            value = value.get("tohost")
        norm = _norm_tohost(value) if not isinstance(value, dict) else None
        if norm is not None:
            return norm, name
    return None, None


def _guard_rejected(evidence):
    """Return a reason string if the guard rejected the patch, else None."""
    if evidence.get("guard_rejected") is True:
        return "guard_rejected flag set: tooling refused the patch"
    verdict = evidence.get("guard_verdict")
    if isinstance(verdict, str) and verdict.strip().lower() in _REJECT_WORDS:
        return "guard_verdict=%r: tooling rejected the patch" % (verdict,)
    guard = evidence.get("guard")
    if isinstance(guard, dict):
        for key in ("accepted", "ok", "passed", "pass"):
            if key in guard and guard[key] is False:
                detail = guard.get("reason") or guard.get("detail") or ""
                suffix = (": %s" % (detail,)) if detail else ""
                return "guard[%s]=False%s: tooling rejected the patch" % (key, suffix)
    return None


def _infrastructure_hit(evidence):
    """Return a reason string for transport/API failure, else None."""
    for key in ("api_error", "transport_error", "api_empty_response"):
        if _truthy(evidence.get(key)):
            return "%s=%r: transport/API delivered no usable response" % (
                key, evidence.get(key))
    markers = evidence.get("api_error_markers")
    if _truthy(markers):
        return "api_error_markers=%r: API error markers present" % (markers,)
    if evidence.get("trace_empty") is True:
        return "trace_empty: sim produced zero trace lines (harness/infra)"
    return None


def _tb_hit(evidence):
    """Return a reason string for testbench untrustworthiness, else None."""
    if evidence.get("resim_mismatch") is True:
        return "resim_mismatch: resim PASS/FAIL disagrees with oracle"
    resim, resim_key = _get_tohost(evidence, "resim_tohost", "resim")
    oracle, oracle_key = _get_tohost(
        evidence, "oracle_tohost", "expected_tohost", "oracle", "expected")
    if resim is not None and oracle is not None and resim != oracle:
        return ("resim %s=%s vs oracle %s=%s: TB verdict "
                "contradicts oracle" % (resim_key, resim, oracle_key, oracle))
    exit_code = evidence.get("exit_code")
    if (isinstance(exit_code, int) and not isinstance(exit_code, bool)
            and exit_code != 0 and resim == "PASS"):
        return ("exit_code=%d with resim %s=PASS: harness/TB exit "
                "contract broken" % (exit_code, resim_key))
    return None


def _model_hit(evidence):
    """Return a reason string for unusable model output, else None."""
    if evidence.get("model_unparseable") is True:
        return "model_unparseable: model output never parsed"
    if evidence.get("parse_ok") is False:
        retries = evidence.get("retries")
        budget = evidence.get("max_retries")
        if retries is not None or budget is not None:
            return ("parse_ok=False after retries=%r/%r: model output "
                    "unparseable within budget" % (retries, budget))
        return "parse_ok=False: model output unparseable"
    if evidence.get("retries_exhausted") is True:
        return "retries_exhausted: retry budget spent without a usable patch"
    return None


def _context_reasons(evidence):
    """Echo supporting bundle context into reasons (never decisive)."""
    extra = []
    if "verdict_before" in evidence and evidence["verdict_before"] is not None:
        extra.append("verdict_before=%r" % (evidence["verdict_before"],))
    if "exit_code" in evidence and evidence["exit_code"] is not None:
        extra.append("exit_code=%r" % (evidence["exit_code"],))
    if "trace_lines" in evidence and evidence["trace_lines"] is not None:
        extra.append("trace_lines=%r" % (evidence["trace_lines"],))
    return extra


def classify_red(evidence):
    """Attribute a RED bundle's evidence to {tool, tb, model, infra}.

    Returns {"attribution", "reasons"} with a non-empty reasons list;
    the first reason names the deciding signal. Raises ValueError on
    non-dict evidence.
    """
    if not isinstance(evidence, dict):
        raise ValueError(
            "blame: evidence must be a dict, got %r" % type(evidence))

    hit = _infrastructure_hit(evidence)
    if hit is not None:
        return {"attribution": "infra",
                "reasons": [hit] + _context_reasons(evidence)}

    hit = _guard_rejected(evidence)
    if hit is not None:
        return {"attribution": "tool",
                "reasons": [hit] + _context_reasons(evidence)}

    hit = _tb_hit(evidence)
    if hit is not None:
        return {"attribution": "tb",
                "reasons": [hit] + _context_reasons(evidence)}

    hit = _model_hit(evidence)
    if hit is not None:
        return {"attribution": "model",
                "reasons": [hit] + _context_reasons(evidence)}

    reasons = ["no infra/tool/tb signal: RED stands with model as last writer"]
    return {"attribution": "model",
            "reasons": reasons + _context_reasons(evidence)}


def record_blame(meta, evidence):
    """Classify RED evidence and write the report to meta["blame"].

    Returns the report. meta must be a dict; meta is touched only
    after the report computes cleanly (never half-written).
    """
    if not isinstance(meta, dict):
        raise ValueError("blame: meta must be a dict, got %r" % type(meta))
    report = classify_red(evidence)
    meta["blame"] = report
    return report
