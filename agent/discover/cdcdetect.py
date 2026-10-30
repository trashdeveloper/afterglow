#!/usr/bin/env python3
"""Task E6: CDC detector (plan item 56, Wave E FIND BUGS).

Flags unsynchronized clock-domain crossings in multi-clock TBs.

Interfaces:
  classify_crossing(driver_clock, load_clock, has_sync=False)
    -> "sync" | "cdc-safe" | "cdc-risk".
    Same clock -> "sync" (no domain crossing at all).
    Different clocks + has_sync truthy -> "cdc-safe" (synchronized
    crossing, e.g. through a 2-flop synchronizer: not flagged).
    Different clocks + has_sync falsy -> "cdc-risk" (direct
    cross-clock: flagged).
  find_crossings(crossings) -> [risky...]
    crossings: list/tuple of dicts each with "signal",
    "driver_clock", "load_clock", and optional "has_sync"
    (absent has_sync defaults to False: fail-safe, an undescribed
    sync is assumed unsynchronized). Returns the sub-list whose
    classification is "cdc-risk", in input order.
  check_sync(crossing) -> bool
    True unless the crossing classifies as "cdc-risk". Accepts
    either a crossing dict or the triple
    (driver_clock, load_clock, has_sync).

Honesty contract:
  - Clocks must be non-empty strings; anything else raises loudly
    (never silently "sync" over an undescribed domain).
  - find_crossings entries must be dicts with a unique-signal
    attribution: missing "signal"/clock keys raise loudly (never
    silently passed or skipped); a non-list portfolio raises
    TypeError.
  - Missing has_sync is fail-safe False (assumed unsynchronized),
    never fail-open.
  - find_crossings never mutates its input (returns refs in input
    order; the input list itself is untouched).

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts,
writes no files (returns str/bool/lists only), and no caller is wired.

Pure stdlib; no API, no keys, no network.
"""

__all__ = ["classify_crossing", "find_crossings", "check_sync"]

SYNC = "sync"
CDC_SAFE = "cdc-safe"
CDC_RISK = "cdc-risk"


def _require_clock(value, name):
    """Validate one clock-domain label; returns the label."""
    if not isinstance(value, str) or not value:
        raise ValueError(
            "%s must be a non-empty str, got %r: refusing to judge "
            "a crossing on an undescribed domain" % (name, value))
    return value


def classify_crossing(driver_clock, load_clock, has_sync=False):
    """Classify one driver->load clock-domain crossing.

    driver_clock/load_clock: non-empty str domain labels.
    has_sync: truthy iff a synchronizer sits on the path
      (2-flop sync, handshake, async FIFO boundary, ...).

    Returns "sync" (same domain), "cdc-safe" (cross-domain with
    sync), or "cdc-risk" (cross-domain without sync).
    """
    _require_clock(driver_clock, "driver_clock")
    _require_clock(load_clock, "load_clock")
    if driver_clock == load_clock:
        return SYNC
    if has_sync:
        return CDC_SAFE
    return CDC_RISK


def _classify_entry(entry):
    """Classify one portfolio dict; validates shape loudly."""
    if not isinstance(entry, dict):
        raise TypeError(
            "each crossing must be a dict, got %r: refusing to run "
            "an undescribed crossing" % (entry,))
    for key in ("signal", "driver_clock", "load_clock"):
        if key not in entry:
            raise KeyError(
                "each crossing needs a %r key, got %r: refusing to "
                "attribute a nameless crossing" % (key, entry))
    signal = entry["signal"]
    if not isinstance(signal, str) or not signal:
        raise ValueError(
            "crossing signal must be a non-empty str, got %r: refusing "
            "to attribute a nameless crossing" % (signal,))
    return classify_crossing(entry["driver_clock"], entry["load_clock"],
                             entry.get("has_sync", False))


def find_crossings(crossings):
    """Return exactly the unsynchronized ("cdc-risk") crossings.

    crossings: list/tuple of {"signal", "driver_clock",
      "load_clock", "has_sync"?} dicts. A synchronized crossing
      ("cdc-safe") or same-clock pair ("sync") is NOT flagged.
    Returns the risky entries in input order; the input list is
    never mutated.
    """
    if not isinstance(crossings, (list, tuple)):
        raise TypeError(
            "crossings must be a list/tuple of dicts, got %r: refusing "
            "to scan without a portfolio" % (crossings,))
    risky = []
    for entry in crossings:
        if _classify_entry(entry) == CDC_RISK:
            risky.append(entry)
    return risky


def check_sync(crossing, load_clock=None, has_sync=False):
    """Check one crossing: True unless it is "cdc-risk".

    Accepts either a crossing dict (with driver_clock/load_clock/
    optional has_sync keys) or the triple
    check_sync(driver_clock, load_clock, has_sync).
    """
    if isinstance(crossing, dict):
        return _classify_entry(crossing) != CDC_RISK
    if isinstance(crossing, str) and isinstance(load_clock, str):
        return classify_crossing(crossing, load_clock,
                                 has_sync) != CDC_RISK
    raise TypeError(
        "check_sync needs a crossing dict or "
        "(driver_clock, load_clock[, has_sync]), got %r: refusing "
        "to pass an undescribed crossing" % (crossing,))
