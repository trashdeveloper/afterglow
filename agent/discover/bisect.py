#!/usr/bin/env python3
"""Task A10: regression bisect (plan item 10, Wave A FIND BUGS).

Given a failing stimulus list and a predicate
(subset -> True still fails / False passes), delta-minimizes to the
shortest failing trigger with ddmin-style chunk halving: split the
current set into n chunks, test each chunk alone, then test each
complement (current minus one chunk); on the first still-failing
candidate narrow to it, otherwise double the granularity and retry.

Interfaces:
  minimize(stimuli, predicate, max_rounds=None) -> report
  report = {"minimal", "trail", "calls", "n_initial", "n_minimal"}
  trail entry = {"step", "kind", "granularity", "before", "after",
    "removed"} with kind "subset" (a chunk alone still fails) or
    "complement" (current minus one chunk still fails).

Honesty contract (pure algorithm task, no sim, no API):
  - An empty initial list raises ValueError before the predicate ever
    runs (nothing to minimize).
  - A predicate that passes on the FULL initial set raises ValueError
    loudly (vacuous input: there is no failing trigger to bisect).
  - Predicate exceptions propagate unchanged -- NEVER caught, never
    treated as pass or fail.
  - Progress guarantee: only a strictly smaller still-failing
    candidate is ever accepted, so every trail step has
    after < before. At most len(initial)-1 accepted steps exist.
  - Termination: each loop round either strictly shrinks the set or
    doubles the granularity (at most log2 steps between shrinks, then
    n reaches the set size and single-element complements are tried);
    max_rounds (default 10*n+100) raises RuntimeError if ever
    exceeded, so the loop cannot spin forever.
  - Result is 1-minimal: the minimal set still fails and removing any
    single element from it passes (for monotone "contains the core"
    predicates this is exactly the planted core).
"""

__all__ = ["minimize"]


def _split_evenly(items, n):
    """Split items into n non-empty chunks, as equal as possible."""
    n = max(1, min(int(n), len(items)))
    base, rem = divmod(len(items), n)
    chunks = []
    idx = 0
    for i in range(n):
        size = base + (1 if i < rem else 0)
        chunks.append(list(items[idx:idx + size]))
        idx += size
    return [c for c in chunks if c]


def minimize(stimuli, predicate, max_rounds=None):
    """Delta-minimize a failing stimulus list to its shortest trigger.

    stimuli: non-empty list of stimulus items (any type; compared by
      value via the predicate, split by position so duplicates work).
    predicate: callable taking a list (a subset copy, original order
      preserved) and returning True (subset still fails) / False
      (subset passes). Must be pure-ish: called many times on fresh
      list copies. Any exception it raises propagates to the caller.
    max_rounds: hard cap on halving rounds (default 10*n+100);
      RuntimeError if exceeded (should never fire -- proves the
      loop terminates rather than spinning).

    Returns {"minimal": still-failing 1-minimal list,
      "trail": [one entry per accepted shrink, before/after sizes],
      "calls": total predicate evaluations (including the initial
      full-set check), "n_initial", "n_minimal"}.
    """
    items = list(stimuli)
    n_initial = len(items)
    if n_initial == 0:
        raise ValueError(
            "minimize needs a non-empty failing stimulus list, got []: "
            "refusing to bisect nothing")
    if not callable(predicate):
        raise TypeError("predicate must be callable, got %r" % (predicate,))
    if max_rounds is None:
        max_rounds = 10 * n_initial + 100

    calls = 1
    if not predicate(list(items)):
        raise ValueError(
            "vacuous input: predicate passes on the full %d-item set, "
            "so no failing trigger exists to minimize -- refusing to "
            "return a fake 'minimal' set" % n_initial)

    current = items
    trail = []
    step = 0
    rounds = 0
    n = 2
    while len(current) > 1:
        if n > len(current):
            n = len(current)
        rounds += 1
        if rounds > max_rounds:
            raise RuntimeError(
                "bisect did not converge after %d rounds "
                "(%d -> %d items): refusing to spin forever"
                % (max_rounds, n_initial, len(current)))
        chunks = _split_evenly(current, n)
        narrowed = False

        # 1. A chunk alone that still fails becomes the new current.
        for chunk in chunks:
            if len(chunk) >= len(current):
                continue  # safety: never accept a non-shrink
            calls += 1
            if predicate(list(chunk)):  # raises propagate, never a verdict
                step += 1
                trail.append({
                    "step": step,
                    "kind": "subset",
                    "granularity": n,
                    "before": len(current),
                    "after": len(chunk),
                    "removed": len(current) - len(chunk),
                })
                current = list(chunk)
                n = 2
                narrowed = True
                break
        if narrowed:
            continue

        # 2. A complement (current minus one chunk) that still fails
        #    becomes the new current.
        for i in range(len(chunks)):
            complement = []
            for j, chunk in enumerate(chunks):
                if j != i:
                    complement.extend(chunk)
            if not complement or len(complement) >= len(current):
                continue  # safety: never accept a non-shrink
            calls += 1
            if predicate(list(complement)):  # raises propagate
                step += 1
                trail.append({
                    "step": step,
                    "kind": "complement",
                    "granularity": n,
                    "before": len(current),
                    "after": len(complement),
                    "removed": len(current) - len(complement),
                })
                current = list(complement)
                n = max(n - 1, 2)
                narrowed = True
                break
        if narrowed:
            continue

        # 3. Nothing at this granularity still fails: halve smaller.
        if n >= len(current):
            break  # single-element complements all pass: 1-minimal
        n = min(len(current), n * 2)

    return {
        "minimal": list(current),
        "trail": trail,
        "calls": calls,
        "n_initial": n_initial,
        "n_minimal": len(current),
    }
