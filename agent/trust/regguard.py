#!/usr/bin/env python3
"""Regression guard (Task D6-TRUE, plan item 36, Wave D).

Fixed bugs become permanent specs re-fired by CI: a registry
(``agent/trust/regressions.json``) records fixed case ids + their green
checksums, and :func:`refire_entry` / :func:`refire_all` re-run the
recorded specs and report any regression.

Sim-only, no API calls, stdlib + repo imports only. Two sim paths:

1. Library/CI default: REAL Verilator via ``agent.trust.replay``'s
   backend -- the entry's ``bundle`` points at the finished GREEN
   ``runs/<id>/`` bundle whose ``patch.diff`` is re-applied onto the
   pristine tree, rebuilt, re-run, and checksum-compared.
2. Unit tests: an injectable ``sim_fn`` with signature
   ``(case, seed, patch_text, work_dir) -> (boot_text, verdict_dict)``,
   fast and offline. Entries with no ``bundle`` replay the recorded
   checksum contract through the injected ``sim_fn`` (the default real
   sim requires a bundle and reports ``ok=False`` without one).

CI wiring WITHOUT touching ``scripts/ci.sh`` -- either entry point:

* standalone check: ``python3 agent/trust/regguard.py --check``
  (exit 0 = all specs green, 1 = regression, 2 = usage/error), or
* suite-callable function: ``check_regressions()`` / ``refire_all()``.

Exit contract (CLI ``--check``): 0 = every recorded spec re-fired to
its recorded checksum, 1 = any MISMATCH or entry error, 2 = usage error.
"""

from __future__ import annotations

import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_REGISTRY = os.path.join(ROOT, "agent", "trust", "regressions.json")

if ROOT not in sys.path:
    # Standalone-check support: `python3 agent/trust/regguard.py --check`
    # runs with agent/trust/ (not the repo root) on sys.path, so
    # bootstrap the root for the lazy `agent.trust.replay` import below.
    sys.path.insert(0, ROOT)


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_registry(path: str | None = None) -> dict:
    """Load the regression registry. Raises on missing/corrupt file."""
    path = path or DEFAULT_REGISTRY
    reg = _read_json(path)
    if not isinstance(reg, dict) or not isinstance(reg.get("cases"), list):
        raise ValueError("registry %s needs {version, cases: [...]}" % path)
    return reg


def save_registry(reg: dict, path: str | None = None) -> str:
    """Validate + write the registry. Returns the path written."""
    if not isinstance(reg, dict) or not isinstance(reg.get("cases"), list):
        raise ValueError("registry needs {version, cases: [...]}")
    for entry in reg["cases"]:
        if not isinstance(entry, dict) or not entry.get("case") \
                or not entry.get("checksum"):
            raise ValueError("registry entry needs case + checksum: %r" % (entry,))
    path = path or DEFAULT_REGISTRY
    reg = dict(reg)
    reg["version"] = 1
    reg["updated_utc"] = _utcnow()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(reg, indent=2) + "\n")
    return path


def record_fixed(case: str, checksum: str, tohost: str = "PASS",
                 seed: int = 42, bundle: str | None = None,
                 path: str | None = None, added_utc: str | None = None) -> dict:
    """Record (or update) one fixed spec, persist, return the entry.

    Upserts by ``case``: a re-recorded case updates in place instead of
    duplicating, so the registry holds one permanent spec per case.
    """
    if not isinstance(case, str) or not case.strip():
        raise ValueError("case must be a non-empty string")
    if not isinstance(checksum, str) or not checksum.strip():
        raise ValueError("checksum must be a non-empty string")
    path = path or DEFAULT_REGISTRY
    try:
        reg = load_registry(path)
    except (OSError, ValueError):
        reg = {"version": 1, "cases": []}
    entry = {"case": case.strip(), "checksum": checksum.strip(),
             "tohost": tohost, "seed": int(seed), "bundle": bundle,
             "added_utc": added_utc or _utcnow()}
    for i, old in enumerate(reg["cases"]):
        if isinstance(old, dict) and old.get("case") == entry["case"]:
            reg["cases"][i] = entry
            break
    else:
        reg["cases"].append(entry)
    save_registry(reg, path)
    return entry


def record_from_bundle(bundle_dir: str, path: str | None = None) -> dict:
    """Seed one registry entry from a finished GREEN ``runs/<id>/`` bundle.

    Reads ``meta.json`` (case + seed) and ``artifacts/verdict_after.json``
    (checksum + tohost). A bundle whose AFTER verdict is not PASS raises
    ValueError -- RED bundles never become permanent specs.
    """
    try:
        meta = _read_json(os.path.join(bundle_dir, "meta.json"))
    except (OSError, ValueError) as exc:
        raise ValueError("unreadable meta.json in %s: %s" % (bundle_dir, exc))
    case = meta.get("case_id") or meta.get("case")
    if not case:
        raise ValueError("meta.json in %s names no case" % bundle_dir)
    seed = meta.get("sim_seed", meta.get("seed", 42))
    try:
        with open(os.path.join(bundle_dir, "patch.diff"), "r",
                   encoding="utf-8") as fh:
            patch_text = fh.read()
    except OSError as exc:
        raise ValueError("unreadable patch.diff in %s: %s" % (bundle_dir, exc))
    if not patch_text.strip():
        raise ValueError("patch.diff in %s is empty" % bundle_dir)
    try:
        verdict = _read_json(
            os.path.join(bundle_dir, "artifacts", "verdict_after.json"))
    except (OSError, ValueError) as exc:
        raise ValueError("unreadable artifacts/verdict_after.json in %s: %s"
                         % (bundle_dir, exc))
    if not verdict.get("pass") or not verdict.get("checksum"):
        raise ValueError("bundle %s is not GREEN (pass=%r checksum=%r): "
                         "RED bundles never become permanent specs"
                         % (bundle_dir, verdict.get("pass"),
                            verdict.get("checksum")))
    abs_bundle = os.path.abspath(bundle_dir)
    if abs_bundle.startswith(os.path.abspath(ROOT) + os.sep):
        bundle_ref = os.path.relpath(abs_bundle, os.path.abspath(ROOT))
    else:
        bundle_ref = abs_bundle
    return record_fixed(case, verdict["checksum"],
                        tohost=verdict.get("tohost", "PASS"),
                        seed=int(seed), bundle=bundle_ref, path=path)


def _resolve_bundle(bundle_ref: str) -> str:
    if os.path.isabs(bundle_ref):
        return bundle_ref
    return os.path.join(ROOT, bundle_ref)


def refire_entry(entry: dict, sim_fn=None, work_dir: str | None = None) -> dict:
    """Re-fire one recorded spec; never raises on entry/bundle problems.

    Returns a dict with ``ok`` (True iff the replayed checksum -- and
    tohost -- equal the recorded ones), plus ``expected_checksum``,
    ``replayed_checksum``, ``reason``, ``case``, ``seed``.
    """
    if not isinstance(entry, dict):
        return {"ok": False, "reason": "registry entry must be a dict"}
    case = entry.get("case")
    if not case:
        return {"ok": False, "reason": "registry entry names no case"}
    expected_checksum = entry.get("checksum")
    if not expected_checksum:
        return {"ok": False, "case": case,
                "reason": "entry %s records no checksum" % case}
    try:
        seed = int(entry.get("seed", 42))
    except (TypeError, ValueError):
        return {"ok": False, "case": case,
                "reason": "entry %s has a bad seed %r" % (case, entry.get("seed"))}
    expected_tohost = entry.get("tohost", "PASS")
    bundle_ref = entry.get("bundle")
    if bundle_ref:
        patch_path = os.path.join(_resolve_bundle(bundle_ref), "patch.diff")
        try:
            with open(patch_path, "r", encoding="utf-8") as fh:
                patch_text = fh.read()
        except OSError as exc:
            return {"ok": False, "case": case, "seed": seed,
                    "expected_checksum": expected_checksum,
                    "reason": "unreadable bundle patch %s: %s" % (patch_path, exc)}
        if not patch_text.strip():
            return {"ok": False, "case": case, "seed": seed,
                    "expected_checksum": expected_checksum,
                    "reason": "bundle patch %s is empty" % patch_path}
    else:
        if sim_fn is None:
            return {"ok": False, "case": case, "seed": seed,
                    "expected_checksum": expected_checksum,
                    "reason": "no bundle recorded for %s: default real sim "
                              "needs patch.diff" % case}
        patch_text = ("# checksum-replay-only: no bundle recorded for %s\n"
                      % case)
    if sim_fn is None:
        from agent.trust import replay  # noqa: E402  (repo import, lazy)
        sim_fn = replay._default_sim
    try:
        _boot, verdict = sim_fn(case, seed, patch_text, work_dir)
    except Exception as exc:  # noqa: BLE001  (re-fire must report, not crash)
        return {"ok": False, "case": case, "seed": seed,
                "expected_checksum": expected_checksum,
                "reason": "re-fire sim failed: %r" % (exc,)}
    if not isinstance(verdict, dict):
        return {"ok": False, "case": case, "seed": seed,
                "expected_checksum": expected_checksum,
                "reason": "sim_fn returned no verdict dict"}
    replayed_checksum = verdict.get("checksum")
    replayed_tohost = verdict.get("tohost")
    match = (replayed_checksum == expected_checksum
             and replayed_tohost == expected_tohost)
    return {"ok": bool(match), "case": case, "seed": seed,
            "expected_checksum": expected_checksum,
            "replayed_checksum": replayed_checksum,
            "expected_tohost": expected_tohost,
            "replayed_tohost": replayed_tohost,
            "reason": "checksum MATCH" if match else
                      "checksum MISMATCH (REGRESSION): re-fired %s (%s) != "
                      "recorded %s (%s)"
                      % (replayed_checksum, replayed_tohost,
                         expected_checksum, expected_tohost)}


def refire_all(registry: dict | None = None, path: str | None = None,
               sim_fn=None, work_dir: str | None = None) -> dict:
    """Re-fire every recorded spec; never raises on entry problems.

    Returns ``{ok, total, passed, failed, results, reason}`` with one
    :func:`refire_entry` dict per recorded case. ``ok`` is True iff
    every spec re-fired green (an empty registry is vacuously green).
    """
    if registry is None:
        try:
            registry = load_registry(path)
        except (OSError, ValueError) as exc:
            return {"ok": False, "total": 0, "passed": 0, "failed": 0,
                    "results": [],
                    "reason": "unreadable registry: %s" % exc}
    cases = registry.get("cases", []) if isinstance(registry, dict) else []
    results = [refire_entry(e, sim_fn=sim_fn, work_dir=work_dir)
               for e in cases]
    passed = sum(1 for r in results if r.get("ok"))
    failed = len(results) - passed
    bad = [r.get("case", "?") for r in results if not r.get("ok")]
    return {"ok": failed == 0, "total": len(results),
            "passed": passed, "failed": failed, "results": results,
            "reason": ("all %d fixed spec(s) still green" % len(results))
                      if failed == 0 else
                      ("REGRESSION in %d/%d spec(s): %s"
                       % (failed, len(results), ", ".join(bad)))}


def check_regressions(path: str | None = None, sim_fn=None,
                      work_dir: str | None = None) -> dict:
    """Suite-callable CI hook: re-fire the registry, report regressions.

    Same contract as :func:`refire_all` with the registry loaded from
    ``path`` (default ``agent/trust/regressions.json``). CI calls this
    without touching ``scripts/ci.sh``.
    """
    return refire_all(path=path, sim_fn=sim_fn, work_dir=work_dir)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    work_dir = None
    registry = None
    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--work-dir" and i + 1 < len(argv):
            work_dir = argv[i + 1]
            i += 2
        elif argv[i] == "--registry" and i + 1 < len(argv):
            registry = argv[i + 1]
            i += 2
        else:
            positional.append(argv[i])
            i += 1
    if not positional or positional[0] not in ("--check", "--list", "--record"):
        print("usage: regguard.py --check [--registry PATH] [--work-dir DIR]",
              file=sys.stderr)
        print("       regguard.py --list [--registry PATH]", file=sys.stderr)
        print("       regguard.py --record <runs/<id>> [--registry PATH]",
              file=sys.stderr)
        return 2
    cmd = positional[0]
    if cmd == "--list":
        try:
            reg = load_registry(registry)
        except (OSError, ValueError) as exc:
            print("REGGUARD: %s" % exc, file=sys.stderr)
            return 2
        for entry in reg["cases"]:
            print("%s seed=%s tohost=%s checksum=%s bundle=%s"
                  % (entry.get("case"), entry.get("seed"),
                     entry.get("tohost"), entry.get("checksum"),
                     entry.get("bundle")))
        return 0
    if cmd == "--record":
        if len(positional) != 2:
            print("usage: regguard.py --record <runs/<id>>", file=sys.stderr)
            return 2
        try:
            entry = record_from_bundle(positional[1], path=registry)
        except ValueError as exc:
            print("REGGUARD: %s" % exc, file=sys.stderr)
            return 1
        print("REGGUARD recorded %s checksum=%s bundle=%s"
              % (entry["case"], entry["checksum"], entry["bundle"]))
        return 0
    # --check: re-fire every recorded spec with the REAL Verilator backend.
    res = check_regressions(path=registry, work_dir=work_dir)
    for r in res.get("results", []):
        print("REGGUARD %s case=%s seed=%s" % (
            "MATCH" if r.get("ok") else "MISMATCH",
            r.get("case"), r.get("seed")))
        print("  expected %s (%s)" % (
            r.get("expected_checksum"), r.get("expected_tohost")))
        print("  re-fired %s (%s): %s" % (
            r.get("replayed_checksum"), r.get("replayed_tohost"),
            r.get("reason")))
    print("REGGUARD %s" % res.get("reason"))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
