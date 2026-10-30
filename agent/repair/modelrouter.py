#!/usr/bin/env python3
"""Task H6: Cost-aware model router (plan item 86).

Interface (plan text, verbatim)::

  route(failure_class) -> "cheap"|"escalate";
  run_with_escalation(task_fn, cheap_fn, strong_fn).

Cheap model first, escalate on defined failure classes only.
``route`` returns ``"escalate"`` exactly when ``failure_class`` is a
member of ``ESCALATE_CLASSES`` (the defined failure classes), else
``"cheap"``. ``run_with_escalation`` calls ``task_fn()`` once to
obtain the task's failure class, routes it, then calls EXACTLY ONE of
``cheap_fn``/``strong_fn`` once with that class and returns
``{"route": ..., "result": ...}``. The other model fn is never called
(all legs are injected callables; this module makes zero live calls
itself -- no API, no keys, no network).

``failure_class`` must be a non-empty string, else TypeError (not a
string) / ValueError (empty/blank). ``task_fn``/``cheap_fn``/
``strong_fn`` must be callable, else TypeError before any call. Fn
exceptions propagate unchanged -- never caught, never a fabricated
result.

Default-off contract (Wave H header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (no imports at all -- routing is a local dispatch
decision); no API, no keys, no network, no I/O.
"""

ESCALATE_CLASSES = frozenset({"hard"})


def _check_failure_class(failure_class):
    """Validate a failure class; return it stripped."""
    if not isinstance(failure_class, str):
        raise TypeError(
            "modelrouter: failure_class must be a string, got %r"
            % (failure_class,))
    if not failure_class.strip():
        raise ValueError(
            "modelrouter: failure_class must be non-empty")
    return failure_class


def _check_fn(name, fn):
    """Validate an injected callable; raise loudly if not callable."""
    if not callable(fn):
        raise TypeError(
            "modelrouter: %s must be callable, got %r" % (name, fn))
    return fn


def route(failure_class):
    """Route ``failure_class`` to ``"cheap"`` or ``"escalate"``.

    ``"escalate"`` exactly for members of ``ESCALATE_CLASSES`` (the
    defined failure classes); ``"cheap"`` for everything else.
    """
    cls = _check_failure_class(failure_class)
    if cls in ESCALATE_CLASSES:
        return "escalate"
    return "cheap"


def run_with_escalation(task_fn, cheap_fn, strong_fn):
    """Run the cheap model first, escalate only on defined classes.

    Calls ``task_fn()`` once for the failure class, routes it, then
    calls exactly one model fn once with that class: ``strong_fn``
    on ``"escalate"``, ``cheap_fn`` otherwise. Returns
    ``{"route": route, "result": fn_result}``. Exceptions from any fn
    propagate unchanged.
    """
    _check_fn("task_fn", task_fn)
    _check_fn("cheap_fn", cheap_fn)
    _check_fn("strong_fn", strong_fn)
    failure_class = task_fn()  # raises propagate, never a verdict
    decision = route(failure_class)  # validates the class loudly
    if decision == "escalate":
        result = strong_fn(failure_class)  # raises propagate
    else:
        result = cheap_fn(failure_class)  # raises propagate
    return {"route": decision, "result": result}
