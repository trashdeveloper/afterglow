#!/usr/bin/env python3
"""Task G6: Repair portfolio race (plan item 76).

Produces::

  race(fault, strategies, verify_fn) -> {"winner": ..., "attempts": [...]}

A portfolio of repair strategies (injectable callables with
template/model/transfer shapes) races over one fault. ``fault`` is an
opaque descriptor passed through untouched to each strategy. Each
strategy ``fn(fault)`` returns a candidate; ``verify_fn(candidate)``
is truthy when that candidate greens (all simulated, no live API).
The return is ``{"winner": <strategy name or None>, "attempts": [...]}``
where ``attempts`` logs one ``{"strategy": name, "ok": bool}`` entry
per strategy in run order. The race runs EVERY strategy (attempts
always covers the full portfolio); the winner is the FIRST green in
run order. An all-red race returns ``winner=None`` (honest red --
never an invented winner).

``fault`` must not be ``None``; ``strategies`` must be a non-empty
list/tuple of callables; ``verify_fn`` must be callable; otherwise
TypeError/ValueError loudly, before any verdict. Strategy and
verify_fn exceptions propagate unchanged -- never caught, never a
verdict.

Honesty contract (mirrors ``agent/repair/bisectreg.py``: vacuous input
raises, probe errors propagate, deterministic -- adapted from
commit-bisect to strategy-race because a race has no baseline/tip
monotonicity to check; the graded shape here is "template greens ->
winner is template with all three attempts logged" instead of a log2
probe bound).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py, agent/guard.py, or agent/trust/*, never changes guard
verdicts, and no caller is wired: agent/loop.py and agent/guard.py
are untouched.

Pure stdlib (no imports); no API, no keys, no network, no I/O.
"""


def _strategy_name(fn):
    """Best-effort display name for a strategy callable."""
    name = getattr(fn, "__name__", None)
    if isinstance(name, str) and name.strip():
        return name
    return type(fn).__name__


def _check_strategies(strategies):
    """Validate the strategy portfolio, return it as a list."""
    if not isinstance(strategies, (list, tuple)):
        raise TypeError(
            "repairrace: strategies must be a list/tuple of callables "
            "(template/model/transfer shapes), got %r" % (strategies,))
    funcs = list(strategies)
    if not funcs:
        raise ValueError(
            "repairrace: strategies is empty: nothing to race")
    for fn in funcs:
        if not callable(fn):
            raise TypeError(
                "repairrace: each strategy must be callable, got %r"
                % (fn,))
    return funcs


def race(fault, strategies, verify_fn=None):
    """Race a strategy portfolio over one fault.

    Returns ``{"winner": name-or-None, "attempts": [...]}``. Every
    strategy runs exactly once in order; ``attempts[i]`` is
    ``{"strategy": name, "ok": bool}``. Winner is the first green in
    order, ``None`` when all are red. Raises TypeError/ValueError on
    vacuous input; strategy/verify exceptions propagate.
    """
    if fault is None:
        raise TypeError(
            "repairrace: fault must not be None (opaque fault "
            "descriptor, passed through to each strategy)")
    funcs = _check_strategies(strategies)
    if not callable(verify_fn):
        raise TypeError(
            "repairrace: verify_fn must be a callable probe taking "
            "a candidate, got %r" % (verify_fn,))

    attempts = []
    winner = None
    for fn in funcs:
        candidate = fn(fault)  # raises propagate, never a verdict
        ok = bool(verify_fn(candidate))  # raises propagate, never a verdict
        attempts.append({"strategy": _strategy_name(fn), "ok": ok})
        if ok and winner is None:
            winner = _strategy_name(fn)
    return {"winner": winner, "attempts": attempts}
