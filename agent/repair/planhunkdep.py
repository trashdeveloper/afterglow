#!/usr/bin/env python3
"""Task G1: Dependent multi-hunk planner (plan item 71).

Consumes the ``agent/repair/planhunk.py`` plan format (a list of
``file:line`` hunks) and enforces signal-dependency order.

Interface::

  order_hunks(plan) -> ordered | raises on unorderable

Ordering constraint (plan text): the second hunk referencing the
first's signal must come after (the definer).

Plan entries are either bare ``"file:line"`` strings (the exact
``parse_plan`` output shape: no signal info, hence independent of
every other hunk) or dicts that ADD explicit signal annotations::

  {"hunk": "picorv32.v:373", "defines": ["mem_xfer"], "uses": []}

``defines`` lists the signals this hunk establishes; ``uses`` lists
the signals it references. A hunk whose ``uses`` names a signal
defined by a LATER hunk is out of order -> ValueError (rejected). A
dependency cycle (A uses B's signal while B uses A's) is unorderable
-> ValueError in either input order. Uses of signals no hunk defines
are external inputs and always fine; a hunk using its own defined
signal imposes no constraint.

``order_hunks`` validates the given order and echoes it back as the
normalized bare-pair list (``["picorv32.v:373", ...]``); it never
reorders silently, so a caller passing an out-of-order plan gets a
loud rejection instead of a quiet fix.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``re`` only); no API, no keys, no network, no I/O.
"""

import re

# Same hunk shape as planhunk.py: path with an extension, colon,
# 1-based line number (e.g. "picorv32.v:373").
_HUNK = re.compile(r"([A-Za-z0-9_][\w\-./]*\.\w+):(\d+)")


def _require_signal_list(entry, key):
    """Validate entry["defines"|"uses"]; return stripped names or raise."""
    raw = entry.get(key, [])
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError(
            "planhunkdep: %r of %r must be a list of signal names, "
            "got %r" % (key, entry.get("hunk"), raw))
    names = []
    for sig in raw:
        if not isinstance(sig, str) or not sig.strip():
            raise ValueError(
                "planhunkdep: %r of %r must be non-empty strings, "
                "got %r" % (key, entry.get("hunk"), sig))
        names.append(sig.strip())
    return names


def _split_entry(entry):
    """Normalize one plan entry to (bare_pair, defines, uses) or raise."""
    if isinstance(entry, str):
        match = _HUNK.search(entry)
        if match is None:
            raise ValueError(
                "planhunkdep: plan entries must be 'file:line' strings, "
                "got %r" % (entry,))
        return match.group(0), [], []
    if isinstance(entry, dict):
        hunk = entry.get("hunk")
        if not isinstance(hunk, str):
            raise ValueError(
                "planhunkdep: dict entries need a 'hunk' file:line "
                "string, got %r" % (entry,))
        match = _HUNK.search(hunk)
        if match is None:
            raise ValueError(
                "planhunkdep: dict 'hunk' must hold a 'file:line' pair, "
                "got %r" % (entry,))
        defines = _require_signal_list(entry, "defines")
        uses = _require_signal_list(entry, "uses")
        return match.group(0), defines, uses
    raise ValueError(
        "planhunkdep: plan entries must be 'file:line' strings or "
        "{'hunk', 'defines', 'uses'} dicts, got %r" % (entry,))


def _find_cycle(n, edges):
    """Return a cycle (list of node indices) or None. Iterative DFS."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * n
    for root in range(n):
        if color[root] != WHITE:
            continue
        stack = [(root, iter(sorted(edges[root])))]
        path = [root]
        color[root] = GRAY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color[nxt] == GRAY:
                    return path[path.index(nxt):] + [nxt]
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    path.append(nxt)
                    stack.append((nxt, iter(sorted(edges[nxt]))))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                path.pop()
                color[node] = BLACK
    return None


def order_hunks(plan):
    """Validate dependency order; echo the normalized plan or raise.

    Returns the plan as bare ``["file:line", ...]`` pairs in the given
    order when every signal reference is satisfied by an EARLIER hunk
    (or by no hunk at all -- external signals). Raises ValueError when
    a hunk references a signal defined only by a later hunk
    (out-of-order dependent plan, rejected) or when the dependency
    graph has a cycle (unorderable in any order).
    """
    if not isinstance(plan, (list, tuple)) or not plan:
        raise ValueError(
            "planhunkdep: plan must be a non-empty list of hunks, "
            "got %r" % (plan,))
    pairs, all_defines, all_uses = [], [], []
    for entry in plan:
        pair, defines, uses = _split_entry(entry)
        pairs.append(pair)
        all_defines.append(defines)
        all_uses.append(uses)
    n = len(pairs)
    # Definer of each signal: earliest hunk defining it (self-use
    # imposes no order constraint, so it is skipped per-hunk below).
    defined_by = {}
    for i, defines in enumerate(all_defines):
        for sig in defines:
            if sig not in defined_by:
                defined_by[sig] = i
    # Edges definer -> user (excluding self-use).
    edges = [set() for _ in range(n)]
    for i, uses in enumerate(all_uses):
        for sig in uses:
            j = defined_by.get(sig)
            if j is not None and j != i:
                edges[j].add(i)
    cycle = _find_cycle(n, edges)
    if cycle is not None:
        raise ValueError(
            "planhunkdep: unorderable dependency cycle: %s" % " -> ".join(
                "%s(%s)" % (pairs[i], sorted(all_uses[i])) for i in cycle))
    for i, uses in enumerate(all_uses):
        own = set(all_defines[i])
        for sig in uses:
            if sig in own:
                continue  # self reference: no constraint
            j = defined_by.get(sig)
            if j is not None and j > i:
                raise ValueError(
                    "planhunkdep: out-of-order dependent plan: %r uses "
                    "signal %r defined by later hunk %r; the referencing "
                    "hunk must come after its definer"
                    % (pairs[i], sig, pairs[j]))
    return list(pairs)
