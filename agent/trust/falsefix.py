"""False-fix alarm (Task D2-TRUE, plan item 32, Wave D).

After GREEN, runs the C3 equivalence check (fixed vs pristine tree on
random stimulus). On divergence the verdict stays GREEN but meta is
marked needs-review with a false_fix_alarm record. stdlib + repo
imports only; no loop/guard wiring (default-off).
"""
from agent.repair import equiv


def alarm_after_green(meta, fixed_tree, pristine_tree, case,
                      master_seed=1234, n=2, build_fn=None, run_fn=None):
    """Run C3 equivalence after GREEN; alarm (never flip) on divergence.

    Returns {alarm, verdict, reason, equivalence}. On divergence sets
    meta["needs_review"] = True and meta["false_fix_alarm"] with the
    evidence. verdict is always "GREEN".
    """
    if not isinstance(meta, dict):
        raise ValueError("meta must be a dict, got %r" % type(meta))
    verdict = equiv.check_equivalence(
        fixed_tree, pristine_tree, case,
        master_seed=master_seed, n=n,
        build_fn=build_fn, run_fn=run_fn)
    if verdict.get("equivalent"):
        return {"alarm": False, "verdict": "GREEN",
                "reason": "OK: fixed tree equivalent to pristine",
                "equivalence": verdict}
    ev = verdict.get("evidence", {}) or {}
    record = {"diverged_seeds": [d.get("seed")
                                 for d in ev.get("divergences", [])],
              "evidence": ev,
              "case": case.get("name") if isinstance(case, dict) else None}
    meta["needs_review"] = True
    meta["false_fix_alarm"] = record
    return {"alarm": True, "verdict": "GREEN",
            "reason": "DIVERGED: fixed tree differs from pristine; "
                      "verdict stays GREEN, marked needs-review",
            "equivalence": verdict}
