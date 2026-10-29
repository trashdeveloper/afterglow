"""TB strength score (Task D3-TRUE, plan item 33, Wave D).

Kill-rate of a standard mutant sample by the case TB, written to meta:
each mutant in the sample counts as killed iff the C3 equivalence
check (agent/repair/equiv.py) finds its trace divergent from pristine
under that TB; survivors are trace-equivalent. Sim-only (no API, no
keys).

Interface::

  score_tb(pristine_tree, mutant_trees, case, master_seed=..., n=...,
           build_fn=None, run_fn=None, tb=None)
    -> {"tb", "kill_rate", "killed", "total", "killed_ids",
        "survived_ids", "evidence"}
  record_tb_strength(meta, pristine_tree, mutant_trees, case, tb=None,
                     master_seed=..., n=..., build_fn=None, run_fn=None)
    -> same report; also sets meta["tb_strength"] to the score record.

``mutant_trees`` is a non-empty list of mutant tree paths (a bare
string is not a sample). ``case`` is a vbackend case dict (or a
registered case-name string, loaded via vbackend inside equiv).
``tb`` optionally names the TB under test; by default the case name
is used. ``kill_rate`` is ``killed / total`` in [0, 1].

Honesty contract (mirrors equiv/falsefix):
- The verdict for every mutant comes from a real C3 equivalence run:
  killed means the sim traces actually diverged, survived means they
  matched on every stimulus seed. No heuristic kill judgment.
- An empty sample, a bare-string sample, or a non-list sample raises
  ValueError: a score over zero mutants would be invented.
- Build/run failures, blank trees, bad cases, and bad (master_seed,
  n) propagate loudly from equiv -- never counted as kills or
  survivals, and record_tb_strength never half-writes meta (meta is
  touched only after every mutant scored cleanly).

Default-off contract (Wave D header: additive + default-off): this
module runs ONLY on explicit call. It is imported by nothing in
agent/loop.py, agent/guard.py, or eval/score.py -- those files are
untouched.

Repo imports only (agent.repair.equiv): no API, no keys, no network,
no file or device use.
"""
from agent.repair import equiv

DEFAULT_MASTER_SEED = 1234
DEFAULT_N = 2


def _require_sample(mutant_trees):
    """Return the mutant sample as a list, or raise ValueError."""
    if isinstance(mutant_trees, (str, bytes)):
        raise ValueError(
            "tbstrength: mutant_trees must be a non-empty list of "
            "mutant tree paths, got a bare string %r: refusing to "
            "score a sample of one character at a time"
            % (mutant_trees,))
    try:
        sample = list(mutant_trees)
    except TypeError:
        raise ValueError(
            "tbstrength: mutant_trees must be a non-empty list of "
            "mutant tree paths, got %r" % (mutant_trees,))
    if not sample:
        raise ValueError(
            "tbstrength: mutant_trees must be non-empty: a kill-rate "
            "over zero mutants would be invented -- refusing to score")
    return sample


def _tb_name(tb, case):
    """Resolve the TB-under-test name: explicit override, else case."""
    if tb is not None:
        if not isinstance(tb, str) or not tb.strip():
            raise ValueError(
                "tbstrength: tb must be a non-blank string, got %r"
                % (tb,))
        return tb
    if isinstance(case, dict):
        name = case.get("name", "?")
        return name if isinstance(name, str) and name.strip() else "?"
    if isinstance(case, str) and case.strip():
        return case
    return "?"


def score_tb(pristine_tree, mutant_trees, case, master_seed=DEFAULT_MASTER_SEED,
             n=DEFAULT_N, build_fn=None, run_fn=None, tb=None):
    """Score the case TB: kill-rate over the mutant sample.

    Each mutant is judged by one C3 equivalence run (pristine vs that
    mutant under ``case``): divergent traces mean killed, identical
    traces mean survived. Returns the score report; writes nothing.
    Every failure mode raises loudly (never a fake score).
    """
    sample = _require_sample(mutant_trees)
    entries = []
    for mutant in sample:
        verdict = equiv.check_equivalence(
            pristine_tree, mutant, case,
            master_seed=master_seed, n=n,
            build_fn=build_fn, run_fn=run_fn)
        killed = not verdict["equivalent"]
        entries.append({
            "mutant": mutant if isinstance(mutant, str) else str(mutant),
            "killed": killed,
            "equivalence": verdict,
        })
    killed_ids = [e["mutant"] for e in entries if e["killed"]]
    survived_ids = [e["mutant"] for e in entries if not e["killed"]]
    total = len(entries)
    name = _tb_name(tb, case)
    case_name = case.get("name", "?") if isinstance(case, dict) else (
        case if isinstance(case, str) else "?")
    return {
        "tb": name,
        "kill_rate": len(killed_ids) / total,
        "killed": len(killed_ids),
        "total": total,
        "killed_ids": killed_ids,
        "survived_ids": survived_ids,
        "evidence": {
            "case": case_name,
            "master_seed": master_seed,
            "n": n,
            "mutants": entries,
        },
    }


def record_tb_strength(meta, pristine_tree, mutant_trees, case, tb=None,
                       master_seed=DEFAULT_MASTER_SEED, n=DEFAULT_N,
                       build_fn=None, run_fn=None):
    """Score the case TB and write the score record into meta.

    Sets ``meta["tb_strength"]`` to {"tb", "kill_rate", "killed",
    "total", "killed_ids", "survived_ids", "case", "master_seed",
    "n"} and returns the full score report. ``meta`` must be a dict;
    scoring runs first, so a scoring failure leaves meta untouched.
    """
    if not isinstance(meta, dict):
        raise ValueError(
            "tbstrength: meta must be a dict, got %r" % type(meta))
    report = score_tb(
        pristine_tree, mutant_trees, case,
        master_seed=master_seed, n=n,
        build_fn=build_fn, run_fn=run_fn, tb=tb)
    meta["tb_strength"] = {
        "tb": report["tb"],
        "kill_rate": report["kill_rate"],
        "killed": report["killed"],
        "total": report["total"],
        "killed_ids": report["killed_ids"],
        "survived_ids": report["survived_ids"],
        "case": report["evidence"]["case"],
        "master_seed": report["evidence"]["master_seed"],
        "n": report["evidence"]["n"],
    }
    return report
