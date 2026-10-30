#!/usr/bin/env python3
"""Task E2: Seed portfolio scheduler (plan item 52, Wave E FIND BUGS).

Orders sim seeds by historical kill power and runs the ordered
portfolio against a mutant, attributing the kill to the first seed
that exposes it.

Interfaces:
  schedule(seeds, history) -> [seed...]
    Unseen seeds (not in history) first in input order, then seen
    seeds by kills desc (ties broken by seed asc, deterministic).
  portfolio_kill(seeds, run_fn, is_kill)
    -> {"killed", "by_seed", "attempts"}
    Runs seeds in the given order: run_fn(seed) -> result,
    is_kill(result) -> bool. Stops at the first kill. killed is
    False and by_seed None when nothing kills. attempts counts how
    many seeds actually ran.

Honesty contract:
  - history is an in-memory {seed: kills} dict; no I/O happens here.
  - Non-int seeds (bool rejected), non-dict history, negative or
    non-int kill counts, and non-callable run_fn/is_kill all raise
    loudly -- never silently scheduled or reported.
  - run_fn/is_kill exceptions propagate loudly; a kill verdict is
    returned ONLY for completed predicate checks (never a fake
    miss over errors).

Default-off contract (Wave E header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts,
writes no files (pure in-memory ordering + injected callables), and
no caller is wired.

Pure stdlib; no API, no keys, no network.
"""

__all__ = ["schedule", "portfolio_kill"]


def _require_seed(seed):
    """Validate one seed; bool is rejected (it is not a seed)."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError(
            "seeds must be ints, got %r: refusing to schedule "
            "a non-seed" % (seed,))
    return int(seed)


def _require_history(history):
    """Validate the kill-history map; returns a plain {int: int} copy."""
    if history is None:
        return {}
    if not isinstance(history, dict):
        raise TypeError(
            "history must be a {seed: kills} dict, got %r" % (history,))
    clean = {}
    for seed, kills in history.items():
        seed = _require_seed(seed)
        if isinstance(kills, bool) or not isinstance(kills, int):
            raise TypeError(
                "kill count for seed %d must be an int, got %r: "
                "refusing to rank on a non-count" % (seed, kills))
        if kills < 0:
            raise ValueError(
                "kill count for seed %d must be >= 0, got %d" % (seed,
                                                                 kills))
        clean[seed] = int(kills)
    return clean


def schedule(seeds, history):
    """Order seeds: unseen first (input order), then kills desc.

    seeds: iterable of int seeds (a list is the norm; any iterable
      works). history: {seed: kills} in-memory map (None means no
      history: every seed is unseen). Returns a NEW list; inputs
      are never mutated. Ties among seen seeds break by seed asc
      so the order is deterministic for the same inputs.
    """
    seed_list = [_require_seed(s) for s in seeds]
    kills = _require_history(history)
    unseen = [s for s in seed_list if s not in kills]
    seen = [s for s in seed_list if s in kills]
    seen.sort(key=lambda s: (-kills[s], s))
    return unseen + seen


def portfolio_kill(seeds, run_fn, is_kill):
    """Run the portfolio in order; attribute the kill to one seed.

    seeds: iterable of int seeds, tried in the given order.
    run_fn(seed) -> result (injected sim call; exceptions propagate
      loudly and are never reported as a miss). is_kill(result) ->
      bool (kill predicate; exceptions propagate loudly).

    Returns {"killed", "by_seed", "attempts"}: killed True with
    by_seed set to the FIRST killing seed (later seeds never run);
    killed False with by_seed None when nothing kills (or the
    portfolio is empty). attempts counts seeds actually run.
    """
    if not callable(run_fn):
        raise TypeError(
            "run_fn must be callable, got %r: refusing to run "
            "a portfolio without a runner" % (run_fn,))
    if not callable(is_kill):
        raise TypeError(
            "is_kill must be callable, got %r: refusing to judge "
            "kills without a predicate" % (is_kill,))
    seed_list = [_require_seed(s) for s in seeds]
    attempts = 0
    for seed in seed_list:
        result = run_fn(seed)  # raises loudly on sim error: never a miss
        attempts += 1
        if is_kill(result):  # raises loudly on predicate error
            return {"killed": True, "by_seed": seed, "attempts": attempts}
    return {"killed": False, "by_seed": None, "attempts": attempts}
