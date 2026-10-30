#!/usr/bin/env python3
"""Task E3: Temporal assertion miner (plan item 53, Wave E FIND BUGS).

Mines SVA-like temporal precedence rules from passing traces and
checks traces against a mined rule.

Trace format (fixture-level, in-memory only):
  trace = [sample, ...] where each sample is a {signal: 0/1} dict,
  one entry per cycle in order. A truthy value means the signal is
  asserted that cycle; a missing signal counts as deasserted.

Rule semantics (precedence ordering, e.g. ready->valid):
  rule = {"antecedent", "consequent", "support", "window"} means
  every cycle where `consequent` is asserted must have `antecedent`
  asserted at the same cycle or within `window` earlier cycles.
  A `valid` asserted with no `ready` in the lookback window is a
  violation ("valid-without-ready").

Interfaces:
  mine_rules(passing_traces, window=8, min_support=1) -> [rule]
    For each ordered signal pair (a, c), a != c: emit
    {"antecedent": a, "consequent": c, "support": n, "window": w}
    iff the precedence holds in EVERY passing trace and `c` is
    asserted in at least min_support traces (n = that trace count).
    Deterministic: sorted by (antecedent, consequent). [] for [].
  check_rule(rule, trace) -> True | {"rule", "violations"}
    True when the trace honors the rule; otherwise a dict with
    "violations": [{"cycle", "antecedent", "consequent", "reason"}],
    one per consequent assertion lacking a preceding antecedent.
    The reason names both signals, e.g. "valid-without-ready".

Honesty contract:
  - Bad corpus/trace/rule shapes raise loudly (TypeError/ValueError/
    KeyError) -- never mined as a rule or reported as a hold.
  - check_rule returns the identical True object only after scanning
    every cycle; a violation anywhere is reported, never hidden.

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts,
writes no files (pure in-memory lists/dicts), and no caller is wired.

Pure stdlib; no API, no keys, no network.
"""

__all__ = ["mine_rules", "check_rule"]

_DEFAULT_WINDOW = 8


def _require_window(window):
    if isinstance(window, bool) or not isinstance(window, int):
        raise TypeError(
            "window must be an int, got %r: refusing to mine with "
            "a non-window" % (window,))
    if window < 0:
        raise ValueError(
            "window must be >= 0, got %d: refusing to mine with "
            "a negative lookback" % (window,))
    return int(window)


def _require_trace(trace, what="trace"):
    if not isinstance(trace, list):
        raise TypeError(
            "%s must be a list of {signal: value} samples, got "
            "%r" % (what, trace))
    for idx, sample in enumerate(trace):
        if not isinstance(sample, dict):
            raise TypeError(
                "%s sample %d must be a {signal: value} dict, got "
                "%r" % (what, idx, sample))
        for signal in sample:
            if not isinstance(signal, str):
                raise TypeError(
                    "%s sample %d signal name must be a str, got "
                    "%r" % (what, idx, signal))
    return trace


def _asserted(sample, signal):
    """Truthy value = asserted; missing signal = deasserted."""
    return bool(sample.get(signal, 0))


def _require_rule(rule):
    if not isinstance(rule, dict):
        raise TypeError(
            "rule must be a dict, got %r: refusing to check a "
            "non-rule" % (rule,))
    try:
        antecedent = rule["antecedent"]
        consequent = rule["consequent"]
    except KeyError:
        raise KeyError(
            "rule must carry 'antecedent' and 'consequent': refusing "
            "to check an incomplete rule")
    if not isinstance(antecedent, str) or not antecedent:
        raise ValueError(
            "rule antecedent must be a non-empty str, got %r" % (
                antecedent,))
    if not isinstance(consequent, str) or not consequent:
        raise ValueError(
            "rule consequent must be a non-empty str, got %r" % (
                consequent,))
    if antecedent == consequent:
        raise ValueError(
            "rule antecedent and consequent must differ, got %r: "
            "a signal cannot precede itself" % (antecedent,))
    window = _require_window(rule.get("window", _DEFAULT_WINDOW))
    return antecedent, consequent, window


def _holds_in_trace(antecedent, consequent, trace, window):
    """True iff every consequent assertion has a preceding antecedent."""
    for idx, sample in enumerate(trace):
        if not _asserted(sample, consequent):
            continue
        start = max(0, idx - window)
        if not any(_asserted(trace[j], antecedent)
                   for j in range(start, idx + 1)):
            return False
    return True


def mine_rules(passing_traces, window=_DEFAULT_WINDOW, min_support=1):
    """Mine precedence rules that hold across all passing traces.

    passing_traces: list of traces (each a list of {signal: value}
      samples). window: lookback in cycles (default 8). min_support:
      consequent must be asserted in at least this many traces.
    Returns [rule] sorted by (antecedent, consequent); [] for [].
    """
    window = _require_window(window)
    if isinstance(min_support, bool) or not isinstance(min_support, int):
        raise TypeError(
            "min_support must be an int, got %r" % (min_support,))
    if min_support < 1:
        raise ValueError(
            "min_support must be >= 1, got %d" % (min_support,))
    if not isinstance(passing_traces, list):
        raise TypeError(
            "passing_traces must be a list of traces, got %r" % (
                passing_traces,))
    traces = [_require_trace(t, "passing_traces[%d]" % i)
              for i, t in enumerate(passing_traces)]
    if not traces:
        return []
    signals = sorted({sig for trace in traces for s in trace for sig in s})
    rules = []
    for antecedent in signals:
        for consequent in signals:
            if antecedent == consequent:
                continue
            support = sum(1 for trace in traces
                          if any(_asserted(s, consequent) for s in trace))
            if support < min_support:
                continue
            if all(_holds_in_trace(antecedent, consequent, trace, window)
                   for trace in traces):
                rules.append({"antecedent": antecedent,
                              "consequent": consequent,
                              "support": support,
                              "window": window})
    rules.sort(key=lambda r: (r["antecedent"], r["consequent"]))
    return rules


def check_rule(rule, trace):
    """Check one trace against a rule: True | {"rule", "violations"}."""
    antecedent, consequent, window = _require_rule(rule)
    trace = _require_trace(trace)
    violations = []
    for idx, sample in enumerate(trace):
        if not _asserted(sample, consequent):
            continue
        start = max(0, idx - window)
        if not any(_asserted(trace[j], antecedent)
                   for j in range(start, idx + 1)):
            violations.append({
                "cycle": idx,
                "antecedent": antecedent,
                "consequent": consequent,
                "reason": "%s-without-%s at cycle %d: consequent "
                          "asserted with no %s in the %d-cycle "
                          "lookback" % (consequent, antecedent, idx,
                                        antecedent, window),
            })
    if not violations:
        return True
    return {"rule": {"antecedent": antecedent,
                     "consequent": consequent,
                     "window": window},
            "violations": violations}
