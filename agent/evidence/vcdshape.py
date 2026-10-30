#!/usr/bin/env python3
"""Task F9: Waveform-shape features (plan item 69, Wave F EVIDENCE).

VCD shape features (toggle density, first-change cycle) map to fault
classes, so a diagnosis can cite waveform shape as evidence.

Interfaces:
  features(signal_trace, has_driver=True) -> dict with at least
      {"toggle_density", "first_change"} (plan contract), plus
      {"transitions", "samples", "has_driver"} informational keys.
  classify(feats) -> "stuck-at" | "wrong-constant" | "unknown".

signal_trace: a list/tuple of per-sample signal values (ints such as
0/1 or wider bit-vectors, bools, or '0'/'1'/'x'/'z' style strings --
anything compared with !=). Must be non-empty. Floats, None, and
nested values are caller bugs (ValueError): VCD samples are
bit-vectors, and NaN-style non-reflexive values would silently corrupt
transition counts.

Definitions:
  transitions    = number of adjacent unequal sample pairs.
  toggle_density = transitions / (samples - 1); 0.0 for one sample.
  first_change   = 0-based index of the first sample differing from
                   its predecessor, or None when nothing ever changes.
  has_driver     = caller-provided: does a driver exist for this
                   signal? Defaults True (a driven net is the common
                   case); pass False for a known-undriven net.

Mapping (pinned heuristic, deterministic):
  density == 0 + has_driver True  -> "stuck-at" (driven net never moves).
  density == 0 + has_driver False -> "unknown" (flatline with no driver
      cannot be attributed: undriven, unconnected, or genuinely stuck --
      never invent the distinction).
  0 < density < 0.5               -> "wrong-constant" (mostly-constant
      net with some activity: a wrong constant sampled/latched once,
      not a net frozen by a stuck fault).
  density >= 0.5                  -> "unknown" (busy net: clock-like
      activity gives shape alone no evidence for either class).

_WRONG_CONSTANT_MAX_DENSITY = 0.5 is the pinned cut between
"mostly-constant" and "busy". Boundary (exactly 0.5) goes "unknown":
withholding a diagnosis beats inventing one.

Fail-safe rule: a feats dict with no "has_driver" key is treated as
undriven (-> "unknown" when flat). Only an explicit True yields
"stuck-at". Absence of driver information is not evidence of a driver.

Honesty contract (mirrors pastfix.py: never invent evidence):
  - Inconsistent feats (density == 0 with a first_change, or density
    > 0 with first_change None) raise ValueError: caller bug, never a
    guess.
  - Misshapen inputs (empty trace, bad sample types, bad feats shape)
    raise ValueError loudly.
  - classify() never returns a class the shape does not support;
    "unknown" is the default, not a failure.

Default-off contract (Wave F header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing and no caller
is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (no imports); no API, no keys, no network, no I/O.
"""

_WRONG_CONSTANT_MAX_DENSITY = 0.5

_STUCK_AT = "stuck-at"
_WRONG_CONSTANT = "wrong-constant"
_UNKNOWN = "unknown"


def _is_sample(value):
    """True for honest VCD sample scalars: int/bool/str (not float)."""
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return True
    return isinstance(value, str)


def features(signal_trace, has_driver=True):
    """Extract waveform-shape features from a sampled signal trace.

    Returns a NEW dict {"toggle_density", "first_change",
    "transitions", "samples", "has_driver"}. Inputs unmodified.
    Raises ValueError on empty/non-sequence traces, bad sample types,
    or non-bool has_driver.
    """
    if isinstance(signal_trace, (str, bytes)):
        raise ValueError("signal_trace must be a list/tuple of samples, "
                         "got a bare string/bytes")
    if not isinstance(signal_trace, (list, tuple)):
        raise ValueError("signal_trace must be a list or tuple of "
                         "samples, got %r" % (type(signal_trace).__name__,))
    if len(signal_trace) == 0:
        raise ValueError("signal_trace must be non-empty (no shape "
                         "without samples)")
    if not isinstance(has_driver, bool):
        raise ValueError("has_driver must be a bool, got %r"
                         % (has_driver,))
    for value in signal_trace:
        if not _is_sample(value):
            raise ValueError("signal_trace samples must be int/bool/str "
                             "(VCD bit-vector values), got %r" % (value,))

    transitions = 0
    first_change = None
    for index in range(1, len(signal_trace)):
        if signal_trace[index] != signal_trace[index - 1]:
            transitions += 1
            if first_change is None:
                first_change = index

    gaps = len(signal_trace) - 1
    density = (transitions / gaps) if gaps > 0 else 0.0

    return {"toggle_density": density,
            "first_change": first_change,
            "transitions": transitions,
            "samples": len(signal_trace),
            "has_driver": has_driver}


def _as_density(value):
    """toggle_density as a float in [0, 1]; ValueError otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("feats['toggle_density'] must be a number "
                         "in [0, 1] (not bool), got %r" % (value,))
    density = float(value)
    if not 0.0 <= density <= 1.0:
        raise ValueError("feats['toggle_density'] must be in [0, 1], "
                         "got %r" % (value,))
    return density


def _as_first_change(value):
    """first_change as None or a non-negative int; ValueError otherwise."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("feats['first_change'] must be None or a "
                         "non-negative int, got %r" % (value,))
    return value


def classify(feats):
    """Map shape features to a fault class (never invents one).

    feats: dict with "toggle_density" (number in [0, 1]) and
    "first_change" (None or non-negative int); optional "has_driver"
    (bool; missing/False means undriven -> flat is "unknown"). Extra
    keys (e.g. "transitions", "samples" from features()) are ignored.
    Returns "stuck-at" | "wrong-constant" | "unknown". Raises
    ValueError on misshapen or internally inconsistent feats.
    """
    if not isinstance(feats, dict):
        raise ValueError("feats must be a dict "
                         "{'toggle_density', 'first_change', ...}, got %r"
                         % (type(feats).__name__,))
    if "toggle_density" not in feats or "first_change" not in feats:
        raise ValueError("feats needs 'toggle_density' and 'first_change' "
                         "keys, got %r" % (sorted(feats.keys()),))
    density = _as_density(feats["toggle_density"])
    first_change = _as_first_change(feats["first_change"])

    if "has_driver" in feats:
        if not isinstance(feats["has_driver"], bool):
            raise ValueError("feats['has_driver'] must be a bool, got %r"
                             % (feats["has_driver"],))
        has_driver = feats["has_driver"]
    else:
        has_driver = False  # fail-safe: no provenance -> not driven

    if density == 0.0 and first_change is not None:
        raise ValueError("inconsistent feats: toggle_density 0.0 but "
                         "first_change %r" % (first_change,))
    if density > 0.0 and first_change is None:
        raise ValueError("inconsistent feats: toggle_density %r but "
                         "first_change None" % (feats["toggle_density"],))

    if density == 0.0:
        return _STUCK_AT if has_driver else _UNKNOWN
    if density < _WRONG_CONSTANT_MAX_DENSITY:
        return _WRONG_CONSTANT
    return _UNKNOWN
