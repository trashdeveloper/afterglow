#!/usr/bin/env python3
"""Task E5: Reset-domain explorer (plan item 55, Wave E FIND BUGS).

Enumerates reset stimulus shapes (async / sync / multi-cycle) and
runs each shape against a tree+case via an injected runner,
attributing the exposure (RED) to exactly the shapes whose result
is failing.

Interfaces:
  enumerate_resets() -> [sequence]
    Deterministic, zero-arg, no I/O. Each sequence is a dict
    {"name", "kind", "assert_cycles", "release", "description"}.
    Covers: async short release (mid-cycle), sync single-cycle
    (on-edge), sync multi-cycle hold, async multi-cycle hold.
  explore(tree, case, sequences, run_fn)
    -> {"exposed", "passed", "details", "total"}
    Runs sequences in the given order: run_fn(tree, case, sequence)
    -> result. A result is exposing (RED) iff _is_red(result).
    Stops at nothing: every sequence runs exactly once so the
    board shows which shapes pass and which expose. exposed/passed
    hold sequence names; details maps name -> raw result; total
    counts sequences attempted.

Honesty contract:
  - run_fn must be callable; sequences must be a list/tuple of
    dicts each with a unique non-empty str "name" -- anything
    else raises loudly (never silently passed or skipped).
  - run_fn exceptions propagate loudly; an errored shape is NEVER
    recorded as passed (no fake green over sim errors).
  - An uninterpretable result type raises TypeError (never a
    silent pass): accepted results are bool, str (FAIL/RED/
    MISMATCH verdict lines), or dict with a red/fail/exposed/
    mismatch key.
  - enumerate_resets returns a FRESH list of fresh dicts per call;
    callers may mutate the return without affecting later calls.

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts,
writes no files (returns dicts/lists only), and no caller is wired.

Pure stdlib; no API, no keys, no network.
"""

__all__ = ["enumerate_resets", "explore"]


def enumerate_resets():
    """Return the pinned reset-shape portfolio (fresh objects)."""
    return [
        {
            "name": "async_short",
            "kind": "async",
            "assert_cycles": 1,
            "release": "mid-cycle",
            "description": "async assert 1 cycle, release mid-cycle "
                           "(off-edge: exposes reset-release skew)",
        },
        {
            "name": "sync_single",
            "kind": "sync",
            "assert_cycles": 1,
            "release": "on-edge",
            "description": "sync assert 1 cycle, release on clock edge",
        },
        {
            "name": "sync_multi",
            "kind": "sync",
            "assert_cycles": 4,
            "release": "on-edge",
            "description": "sync assert 4 cycles, release on clock edge",
        },
        {
            "name": "async_multi",
            "kind": "async",
            "assert_cycles": 4,
            "release": "on-edge",
            "description": "async assert 4 cycles, release on clock edge",
        },
    ]


def _is_red(result):
    """Decide whether one run_fn result is exposing (RED)."""
    if isinstance(result, bool):
        return result
    if isinstance(result, str):
        probe = result.upper()
        return ("FAIL" in probe or probe.strip() == "RED"
                or "MISMATCH" in probe)
    if isinstance(result, dict):
        for key in ("red", "fail", "exposed", "mismatch"):
            if key in result:
                return bool(result[key])
        raise TypeError(
            "result dict has no red/fail/exposed/mismatch key, got %r: "
            "refusing to judge an exposure without a verdict" % (result,))
    raise TypeError(
        "run_fn result must be bool, str, or verdict dict, got %r: "
        "refusing to judge an exposure without a verdict"
        % (type(result).__name__,))


def _require_sequences(sequences):
    """Validate the sequence portfolio; returns a list copy."""
    if not isinstance(sequences, (list, tuple)):
        raise TypeError(
            "sequences must be a list/tuple of dicts, got %r: refusing "
            "to explore without a portfolio" % (sequences,))
    checked = []
    seen = set()
    for seq in sequences:
        if not isinstance(seq, dict):
            raise TypeError(
                "each sequence must be a dict, got %r: refusing to run "
                "an undescribed reset shape" % (seq,))
        if "name" not in seq:
            raise KeyError(
                "each sequence needs a 'name' key, got %r: refusing to "
                "attribute a nameless shape" % (seq,))
        name = seq["name"]
        if not isinstance(name, str) or not name:
            raise ValueError(
                "sequence name must be a non-empty str, got %r: refusing "
                "to attribute a nameless shape" % (name,))
        if name in seen:
            raise ValueError(
                "duplicate sequence name %r: refusing an ambiguous "
                "portfolio" % (name,))
        seen.add(name)
        checked.append(seq)
    return checked


def explore(tree, case, sequences, run_fn):
    """Run every reset shape; attribute exposures by name.

    tree/case: opaque handles passed straight through to run_fn
      (fixture strings in tests; real trees at call sites).
    sequences: list/tuple of {"name", ...} dicts (e.g. from
      enumerate_resets()).
    run_fn(tree, case, sequence) -> result (injected sim call;
      exceptions propagate loudly and are never recorded as
      passed).

    Returns {"exposed", "passed", "details", "total"}.
    """
    if not callable(run_fn):
        raise TypeError(
            "run_fn must be callable, got %r: refusing to explore "
            "reset shapes without a runner" % (run_fn,))
    checked = _require_sequences(sequences)
    exposed = []
    passed = []
    details = {}
    for seq in checked:
        result = run_fn(tree, case, seq)  # raises loudly: never a pass
        details[seq["name"]] = result
        if _is_red(result):  # raises loudly on uninterpretable results
            exposed.append(seq["name"])
        else:
            passed.append(seq["name"])
    return {"exposed": exposed, "passed": passed,
            "details": details, "total": len(checked)}
