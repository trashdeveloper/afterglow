#!/usr/bin/env python3
"""Task F10: Diagnosis tournament (plan item 70, Wave F EVIDENCE).

Two injectable diagnosis strategies compete on one fault; a verifier
resims BOTH candidate neighborhoods and the winner is whoever verifies.
This picks the diagnosis with resim proof behind it, instead of trusting
a single strategy's guess.

Interfaces:
  compete(fault, strategy_a, strategy_b, verifier_fn) ->
      {"winner", "evidence"} (plan contract).

  fault:        opaque fault descriptor, passed through untouched to both
                strategies and to the verifier (never mutated, never
                inspected).
  strategy_a/b: injectable callables fault -> candidate. A candidate is
                any value describing the proposed diagnosis (e.g. a
                {"file", "line"} dict); it is passed through untouched
                to the verifier and recorded in evidence.
  verifier_fn:  injectable callable (fault, candidate) -> result. Called
                once per candidate (ALWAYS twice, A then B, even when A
                fails) so both neighborhoods are resimmed. A dict result
                with a "verified" key uses that key; any other result
                uses truthiness. The RAW result is logged in evidence.

  Returns a NEW dict {"winner", "evidence"} where winner is one of
  "a" | "b" | "tie":
    only A verifies -> "a"; only B verifies -> "b";
    both or neither verify -> "tie" (withholding the choice beats
    inventing one -- the resim log still shows what was tried).
  evidence is a NEW dict:
    {"candidate_a", "candidate_b",      # what each strategy proposed
     "verified_a", "verified_b",        # bool resim outcomes
     "resim_a", "resim_b"}              # raw verifier results

Honesty contract (mirrors vcdshape.py: never invent evidence):
  - Non-callable strategy/verifier is a caller bug: TypeError loudly.
  - Exceptions from strategies/verifier propagate: a crashed resim is
    never recorded as a pass or a fail.
  - A tie never invents a winner; evidence always logs both resims.

Default-off contract (Wave F header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing and no caller
is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (no imports); no API, no keys, no network, no I/O.
"""


def _require_callable(value, name):
    """Return value if callable, else raise TypeError (caller bug)."""
    if not callable(value):
        raise TypeError("%s must be a callable, got %r"
                        % (name, type(value).__name__))
    return value


def _as_verified(result):
    """Normalize a verifier result to bool (raw result kept in evidence).

    Dict results use their "verified" key; everything else uses
    truthiness. Missing/non-bool "verified" keys are caller bugs
    (ValueError): a resim verdict must be unambiguous.
    """
    if isinstance(result, dict) and "verified" in result:
        verified = result["verified"]
        if not isinstance(verified, bool):
            raise ValueError('verifier result["verified"] must be a bool, '
                             "got %r" % (verified,))
        return verified
    return bool(result)


def compete(fault, strategy_a, strategy_b, verifier_fn):
    """Run a two-strategy diagnosis tournament on one fault.

    Calls strategy_a(fault) and strategy_b(fault), resims both via
    verifier_fn(fault, candidate), and returns a NEW dict
    {"winner": "a" | "b" | "tie", "evidence": {...}}. Inputs unmodified;
    verifier is always invoked twice (A then B). Raises TypeError on
    non-callable strategies/verifier; strategy/verifier exceptions
    propagate.
    """
    _require_callable(strategy_a, "strategy_a")
    _require_callable(strategy_b, "strategy_b")
    _require_callable(verifier_fn, "verifier_fn")

    candidate_a = strategy_a(fault)
    candidate_b = strategy_b(fault)

    resim_a = verifier_fn(fault, candidate_a)
    resim_b = verifier_fn(fault, candidate_b)
    verified_a = _as_verified(resim_a)
    verified_b = _as_verified(resim_b)

    if verified_a and not verified_b:
        winner = "a"
    elif verified_b and not verified_a:
        winner = "b"
    else:
        winner = "tie"

    return {"winner": winner,
            "evidence": {"candidate_a": candidate_a,
                         "candidate_b": candidate_b,
                         "verified_a": verified_a,
                         "verified_b": verified_b,
                         "resim_a": resim_a,
                         "resim_b": resim_b}}
