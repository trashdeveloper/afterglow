#!/usr/bin/env python3
"""Task A9: timing stressor (plan item 9, Wave A FIND BUGS).

Varies `+seed` and clock-edge sampling parameters across runs of the
same DUT+TB (build ONCE via sim/vbackend.py primitives, run once per
seed x sampling-config), collects per-seed verdicts via the
decide_verdict oracle marker, and flags order-dependent PASS/FAIL
flips: stable (all runs agree) or flaky (divergent runs with the exact
flip pair + logs).

Interfaces:
  normalize_sampling(sampling) -> [config]
  sampling_plusargs(config) -> [plusarg, ...]
  stress_timing(tree, case, seeds, sampling=None, build_fn=None,
                run_fn=None) -> report
  write_discovery_report(path, case_name, report) -> path

A sampling config is {"name": <non-blank str>,
"plusargs": [<extra argv strings>]}. The default run dimension is
seeds x sampling-configs: the same binary is re-run with `+seed=<n>`
plus the config's extra plusargs (reserved for TBs that honor e.g.
`+sample_edge=` / `+sample_dly=`; the picorv32 fetch TB logs `+seed=`
only, so sampling variation there proves the harness tolerates
unknown plusargs without changing verdicts). The run dimension, not
the build, carries the variation: the build runs exactly ONCE.

Honesty contract (mirrors fuzzer/blindspot/seeddiff: never invent signal):
  - Fewer than 2 seed entries raises ValueError (nothing to compare).
    Duplicate seeds are ALLOWED (same-seed-twice must agree).
  - sampling=None means one default config; an empty list, a blank
    name, or non-string plusargs raise ValueError loudly. Zero runs
    produce zero verdicts, so none is invented.
  - The build runs exactly ONCE; every build error propagates loudly.
  - Any run error (exception), non-string log, or empty/
    whitespace-only log raises loudly -- NEVER recorded as a verdict.
    A finished run WITHOUT the oracle marker is a genuine FAIL per the
    decide_verdict oracle contract, not an error path: only exceptions
    and missing/empty logs are loud.
  - stable is True iff EVERY run's verdict agrees (all PASS or all
    FAIL -- a deterministic all-FAIL is a real bug, not a flake).
    Any disagreement lists EVERY divergent pair with both verdicts
    and both verbatim logs. Agreeing pairs stay silent.
  - Discovery reports NEVER carry verdicts; they state STABLE vs
    FLAKY and name each flip pair.
"""

import os
import sys

_DISCOVER_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_DISCOVER_DIR)
_ROOT = os.path.dirname(_AGENT_DIR)
sys.path.insert(0, os.path.join(_ROOT, "sim"))
sys.path.insert(0, _ROOT)

import vbackend  # noqa: E402
from afterglow_core.types import decide_verdict  # noqa: E402

SIM_TIMEOUT_S = 300

# Default clock-edge sampling: sample on the rising edge with no added
# setup delay. Carries no plusargs (the fetch TB honors `+seed=` only);
# named explicitly so records always state which sampling ran.
DEFAULT_SAMPLING = {"name": "posedge-sample", "plusargs": []}


def normalize_sampling(sampling):
    """Validate sampling configs -> list of {"name", "plusargs"} dicts.

    None means [DEFAULT_SAMPLING]. Anything else must be a non-empty
    list of dicts with a non-blank "name" and an optional list of
    string "plusargs". Raises ValueError loudly on any deviation;
    never silently drops or invents a config. Returned dicts are
    copies (the caller's objects are never mutated or aliased).
    """
    if sampling is None:
        return [dict(DEFAULT_SAMPLING)]
    if not isinstance(sampling, list) or not sampling:
        raise ValueError(
            "sampling must be a non-empty list of configs, got %r: "
            "refusing to judge timing from zero configs" % (sampling,))
    clean = []
    for cfg in sampling:
        if not isinstance(cfg, dict):
            raise ValueError("sampling config must be a dict with "
                             "'name'/'plusargs', got %r" % (cfg,))
        name = cfg.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("sampling config needs a non-blank string "
                             "'name', got %r" % (cfg,))
        plus = cfg.get("plusargs", [])
        if not isinstance(plus, list) or any(
                not isinstance(p, str) for p in plus):
            raise ValueError("sampling config %r 'plusargs' must be a "
                             "list of strings, got %r" % (name, plus))
        clean.append({"name": name.strip(), "plusargs": list(plus)})
    return clean


def sampling_plusargs(config):
    """Extra sim argv for one normalized sampling config."""
    return list(config["plusargs"])


def _default_build(tree, case):
    return vbackend.build(tree, case)


def _default_run(binary, seed, config):
    """Run the ALREADY-BUILT sim binary with +seed + sampling plusargs.

    Exit code is deliberately ignored (the oracle marker decides, per
    the fuzzer/blindspot contract); a timeout raises (never a verdict).
    """
    argv = [str(binary), "+seed=%d" % int(seed)]
    argv += sampling_plusargs(config)
    parent = os.path.dirname(os.path.abspath(str(binary)))
    if os.path.basename(parent).startswith("obj_afterglow_"):
        cwd = os.path.dirname(parent)
    else:
        cwd = parent
    rc, log = vbackend.sh(argv, cwd=cwd, timeout=SIM_TIMEOUT_S)
    del rc  # exit code never decides; the oracle marker does
    return log


def stress_timing(tree, case, seeds, sampling=None, build_fn=None,
                  run_fn=None):
    """Build once, run once per (seed x sampling-config), verdict each.

    tree: RTL tree path. case: vbackend case dict (or case-name string
      loaded via vbackend.load_case). seeds: list of >= 2 int seeds
      (duplicates allowed: same-seed-twice must agree). sampling:
      None (one default config) or a non-empty list of sampling
      config dicts (see normalize_sampling).
    build_fn(tree, case) -> binary path (default vbackend.build).
    run_fn(binary, seed, config) -> sim log text (default: run the
      binary with +seed + config plusargs via vbackend.sh).

    Returns {"case", "seeds", "sampling", "n", "stable", "n_pass",
      "n_fail", "records": [{seed, sampling, pass, log}],
      "flips": [{seed_a, seed_b, sampling_a, sampling_b, pass_a,
      pass_b, log_a, log_b}]}. flips holds EVERY disagreeing run pair;
      agreeing pairs stay silent. stable is True iff flips is empty.

    Every failure mode raises loudly; a report is returned ONLY for
    completed runs (never a fake "stable" over errors).
    """
    seed_list = [int(s) for s in seeds]
    if len(seed_list) < 2:
        raise ValueError(
            "stress_timing needs >= 2 seed entries to compare, got %d "
            "(%r): refusing to report stability from nothing"
            % (len(seed_list), seeds))
    configs = normalize_sampling(sampling)
    if isinstance(case, str):
        case = vbackend.load_case(case)
    if not isinstance(case, dict):
        raise ValueError("case must be a dict, got %r" % (case,))
    try:
        oracle = case["oracle_pass"]
    except (KeyError, TypeError):
        raise KeyError("case is missing required key 'oracle_pass'")
    if not str(oracle).strip():
        raise ValueError("case oracle_pass is blank; verdicts would be "
                         "meaningless -- refusing to run.")
    build = build_fn or _default_build
    run = run_fn or _default_run
    binary = build(tree, case)  # raises loudly on build failure; once only
    records = []
    for seed in seed_list:
        for config in configs:
            log = run(binary, seed, config)  # raises loudly on sim error
            if not isinstance(log, str):
                raise TypeError("run_fn must return sim log text, got %r "
                                "for seed %r sampling %r"
                                % (type(log), seed, config["name"]))
            if not log.strip():
                raise RuntimeError(
                    "sim produced no log output for seed %r sampling %r: "
                    "cannot judge timing without a trace -- refusing to "
                    "report" % (seed, config["name"]))
            verdict = decide_verdict(log, oracle)
            records.append({
                "seed": seed,
                "sampling": config["name"],
                "pass": bool(getattr(verdict, "pass")),
                "log": log,
            })
    flips = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            ra, rb = records[i], records[j]
            if ra["pass"] == rb["pass"]:
                continue  # agreeing pair stays silent
            flips.append({
                "seed_a": ra["seed"],
                "seed_b": rb["seed"],
                "sampling_a": ra["sampling"],
                "sampling_b": rb["sampling"],
                "pass_a": ra["pass"],
                "pass_b": rb["pass"],
                "log_a": ra["log"],
                "log_b": rb["log"],
            })
    return {
        "case": case.get("name", "?"),
        "seeds": seed_list,
        "sampling": [dict(c) for c in configs],
        "n": len(records),
        "stable": not flips,
        "n_pass": sum(1 for r in records if r["pass"]),
        "n_fail": sum(1 for r in records if not r["pass"]),
        "records": records,
        "flips": flips,
    }


def write_discovery_report(path, case_name, report):
    """Write eval/discovery_timing.md-style report: STABLE vs FLAKY.

    Discovery reports NEVER carry verdicts; they state whether all
    runs agreed and, on divergence, name each flip pair with both
    verdicts and both sampling configs (logs live in the records).
    """
    records = report.get("records", [])
    if not records:
        raise ValueError(
            "refusing to write an empty timing report: zero runs "
            "would fake signal from nothing")
    lines = [
        "# Discovery: timing stressor (Task A9)",
        "",
        "- case: %s" % case_name,
        "- seeds: %s" % (", ".join(str(s) for s in report.get("seeds", []))),
        "- sampling: %s" % (", ".join(c.get("name", "?")
                                       for c in report.get("sampling", []))),
        "- runs: %d (PASS %d / FAIL %d)" % (report.get("n", 0),
                                            report.get("n_pass", 0),
                                            report.get("n_fail", 0)),
        "- stable: %s" % ("YES" if report.get("stable") else "NO"),
        "",
    ]
    if report.get("stable"):
        lines.append("STABLE: all %d runs agree under these seeds and "
                     "sampling configs; re-fire with +seed=<n> to "
                     "reproduce this exact comparison." % report.get("n", 0))
    else:
        lines.append("FLAKY: order-dependent PASS/FAIL flips observed. "
                     "Flip pairs (agreeing pairs omitted):")
        for fl in report.get("flips", []):
            lines.append("- seeds %s [%s] -> %s vs seeds %s [%s] -> %s"
                         % (fl.get("seed_a"), fl.get("sampling_a"),
                            "PASS" if fl.get("pass_a") else "FAIL",
                            fl.get("seed_b"), fl.get("sampling_b"),
                            "PASS" if fl.get("pass_b") else "FAIL"))
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
