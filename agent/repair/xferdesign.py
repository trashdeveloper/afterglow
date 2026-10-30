#!/usr/bin/env python3
"""Task H4: Cross-design transfer (plan item 84).

Replays a fix learned on one design (picorv32) onto a same-class
fault on a second design (darkriscv), with zero model calls.

Interface (plan text, verbatim)::

  transfer_fix(picorv32_fix, darkriscv_fault) -> patch|None
    without any model call.

Consumes ``agent/repair/transfer.py`` index: ``picorv32_fix`` (one
``{"signals", "patch"}`` entry -- dict or ``(signals, patch)`` pair,
exactly the shapes ``transfer.build_index`` accepts; a list of such
entries is also accepted and indexed together) is indexed by signal
names, and ``darkriscv_fault`` (free text or a signal-name list) is
matched against it with ``transfer.suggest``. Best overlap wins;
zero overlap -> None (a miss, not a guess).

``model`` is an accepted-but-never-called hook (default None) so
callers on a model path can pass their model through and prove --
with a counting callable in tests -- that cross-design transfer
costs zero model calls. It is ignored unconditionally.

Malformed input propagates TypeError/ValueError loudly from the
transfer layer (refuse to fabricate a patch); never a fake diff over
errors.

Default-off contract (Wave H header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib plus the consumed agent/repair/transfer.py index; no API,
no keys, no network, no I/O.
"""

from agent.repair import transfer


def _as_entries(picorv32_fix):
    """Normalize the picorv32 fix(es) to an entries list for build_index."""
    if isinstance(picorv32_fix, dict):
        return [picorv32_fix]
    if isinstance(picorv32_fix, (list, tuple)):
        if len(picorv32_fix) == 2 and isinstance(picorv32_fix[0], str):
            # A single (free-text-signals, patch) pair, not a list of
            # entries: free-text signals + diff string.
            return [picorv32_fix]
        if len(picorv32_fix) == 2 and isinstance(picorv32_fix[1], str) \
                and isinstance(picorv32_fix[0], (list, tuple)):
            # A single (signal-list, patch) pair.
            return [picorv32_fix]
        if picorv32_fix and all(
                isinstance(e, (dict, list, tuple)) for e in picorv32_fix):
            return list(picorv32_fix)
        if not picorv32_fix:
            raise ValueError(
                "xferdesign: picorv32_fix must be a non-empty "
                "{'signals', 'patch'} entry or a list of entries, "
                "got %r" % (picorv32_fix,))
    raise TypeError(
        "xferdesign: picorv32_fix must be a {'signals', 'patch'} "
        "entry or a list of entries, got %r" % (picorv32_fix,))


def transfer_fix(picorv32_fix, darkriscv_fault, model=None):
    """Replay a picorv32 fix onto a darkriscv fault, no model calls.

    Returns the prior picorv32 patch when the darkriscv fault names
    the same signals (best overlap, top match), else None. The
    ``model`` hook -- when given -- is never called.
    """
    _ = model  # accepted-but-never-called: zero model calls, always.
    index = transfer.build_index(_as_entries(picorv32_fix))
    hits = transfer.suggest(darkriscv_fault, index, top_n=1)
    return hits[0] if hits else None
