#!/usr/bin/env python3
"""Per-bug cost ledger rollup (Task D5-TRUE, plan item 35, Wave D).

Rolls a list of per-run cost records up to per-bug (case id) totals:
attempts, tokens in/out, cost, greens, time-to-green.

Interface::

  rollup_by_bug(records) -> {case_id: {case_id, attempts, greens,
      text_input_tokens, text_output_tokens, text_tokens,
      image_calls, est_usd, time_to_green_s}}

A per-run record reuses the `afterglow_core.cost.compute_run_cost()`
field names (`text_input_tokens`, `text_output_tokens`,
`text_tokens`, `image_calls`, `est_usd`) plus:

  case id:          `case_id` (also accepted as `case` / `bug_id`;
                    required, non-blank string)
  outcome:          `green` -- True/False, 1/0, or one of
                    green/red, pass/fail(passed/failed), true/false,
                    1/0, yes/no (case-insensitive); missing means
                    not green
  time-to-green:    `time_to_green_s` -- non-negative seconds
                    (int/float, bools rejected); missing/None means
                    unknown

Totals per bug: `attempts` counts rows; token/cost/image fields sum
(`est_usd` is summed verbatim and rounded to 6 places -- this module
never reprices, so pricing stays owned by `afterglow_core.cost` and
no cost logic is forked here); `greens` counts green rows;
`time_to_green_s` is the minimum green-row time (first-green time),
or None when no green row carries a time.

Missing numeric cost fields tolerate to zero (mirroring
`afterglow_core.cost` tolerance for incomplete bundles). Junk --
non-list input, non-dict rows, missing/blank case id, unrecognized
outcome flag, negative/non-numeric time -- raises ValueError; the
rollup never invents data.

Default-off contract (Wave D header: additive + default-off): this
module runs ONLY on explicit call. It is imported by nothing in
agent/loop.py, agent/guard.py, or eval/score.py -- those files are
untouched.

Pure stdlib (typing only): no API, no keys, no network, no file or
device use.
"""

from __future__ import annotations

from typing import Any, Dict, List

_VERSION = "1"

_CASE_KEYS = ("case_id", "case", "bug_id")

_GREEN_TRUE = {"green", "pass", "passed", "true", "1", "yes"}
_GREEN_FALSE = {"red", "fail", "failed", "false", "0", "no"}


def _case_id(rec: Dict[str, Any]) -> str:
    """Return the record's stripped case id, or raise ValueError."""
    for key in _CASE_KEYS:
        value = rec.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(
        "bugcost: record needs a non-blank case id under one of %r, "
        "got %r" % (_CASE_KEYS, rec))


def _count(rec: Dict[str, Any], key: str) -> int:
    """Coerce a token/image count: junk/negative tolerates to zero."""
    try:
        number = int(rec.get(key, 0))
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def _usd(rec: Dict[str, Any]) -> float:
    """Coerce est_usd: junk/negative tolerates to zero (never repriced)."""
    try:
        number = float(rec.get("est_usd", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return max(number, 0.0)


def _green(rec: Dict[str, Any]) -> bool:
    """Coerce the outcome flag; missing means not green."""
    value = rec.get("green", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in _GREEN_TRUE:
        return True
    if isinstance(value, str) and value.strip().lower() in _GREEN_FALSE:
        return False
    raise ValueError(
        "bugcost: green must be True/False, 1/0, or green/red "
        "(pass/fail), got %r" % (value,))


def _ttg(rec: Dict[str, Any]) -> float | None:
    """Coerce time_to_green_s; missing/None means unknown."""
    value = rec.get("time_to_green_s")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "bugcost: time_to_green_s must be non-negative seconds, "
            "got %r" % (value,))
    if not float(value) >= 0.0:
        raise ValueError(
            "bugcost: time_to_green_s must be non-negative seconds, "
            "got %r" % (value,))
    return float(value)


def rollup_by_bug(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Roll per-run cost records up to per-bug totals keyed by case id."""
    if isinstance(records, bool) or not isinstance(records, (list, tuple)):
        raise ValueError(
            "bugcost: records must be a list of per-run dicts, "
            "got %r" % (records,))
    out: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            raise ValueError(
                "bugcost: each record must be a dict, got %r" % (rec,))
        case = _case_id(rec)
        green = _green(rec)
        ttg = _ttg(rec)
        row = out.setdefault(case, {
            "case_id": case,
            "attempts": 0,
            "greens": 0,
            "text_input_tokens": 0,
            "text_output_tokens": 0,
            "text_tokens": 0,
            "image_calls": 0,
            "est_usd": 0.0,
            "time_to_green_s": None,
        })
        row["attempts"] += 1
        row["greens"] += 1 if green else 0
        row["text_input_tokens"] += _count(rec, "text_input_tokens")
        row["text_output_tokens"] += _count(rec, "text_output_tokens")
        row["text_tokens"] += _count(rec, "text_tokens")
        row["image_calls"] += _count(rec, "image_calls")
        row["est_usd"] = round(row["est_usd"] + _usd(rec), 6)
        if green and ttg is not None and (
                row["time_to_green_s"] is None
                or ttg < row["time_to_green_s"]):
            row["time_to_green_s"] = ttg
    return out
