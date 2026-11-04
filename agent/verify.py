#!/usr/bin/env python3
"""Keyless re-verification of a committed proof bundle: `afterglow verify`.

Given runs/<id>/ with patch.diff + meta.json (AFTER checksum + case name),
this rebuilds the RED tree, applies patch.diff with the loop's own applier,
resims, and compares the checksum against meta.json's AFTER checksum::

    MATCH      resim checksum == meta AFTER checksum (exit 0)
    MISMATCH   anything else about the claim (exit 1)
    FATAL      cannot even attempt (missing bundle/files/case/tree; exit 2)

Keyless/offline by construction: only stdlib + repo imports, only local
subprocesses (git read-only, patch(1), verilator). No model API is ever
called -- there is no code path here that reads an API key. The shared
pinned tree is never written: RED is rebuilt in an isolated temp copy
(same pinned sha via `git rev-parse HEAD`, same line-anchored
vbackend.inject the RED phase uses), so concurrent loop runs are
undisturbed and no network fetch is needed.

Same-toolchain guarantee (not a reimplementation):
- spec/case:  vbackend.load_case (in-tree sim/targets/<id>.yaml, as RED)
- prepare:    vbackend.inject (the sanctioned anchor->bug injection)
- apply:      agent.loop.try_apply_diff (guard -> repair -> dry-run ->
              apply -> lint, identical to the loop)
- resim:      agent.loop.resim_after (build + run + identical checksum math)

Refusals (VerifyError, exit 2): missing bundle dir, missing/unparseable
meta.json, missing patch.diff, case name absent, unknown case, pair or
line-pinned mutant specs (inject would mis-fire -- those need the loop's
pinned prepare path, not the line-anchored one), pinned tree absent or at
the wrong sha. A patch that fails guard/apply is a verdict, not a
refusal: MISMATCH with the reason (the bundle does not re-verify).

Usage:
  python3 agent/verify.py runs/<id>      (also accepts a bare <id>)
  afterglow verify runs/<id>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, AGENT_DIR)
sys.path.insert(0, os.path.join(ROOT, "sim"))
sys.path.insert(0, ROOT)

import vbackend  # noqa: E402
from agent import loop  # noqa: E402 (same applier + resim as the loop)

EXIT_MATCH = 0
EXIT_MISMATCH = 1
EXIT_FATAL = 2


class VerifyError(RuntimeError):
    """Loud refusal: verification cannot even be attempted."""


def resolve_bundle(run_ref: str) -> str:
    """Resolve a run reference to an existing runs/<id>/ directory.

    Accepts runs/<id>, a bare <id> (resolved under runs/), or any
    existing path. Raises VerifyError when nothing exists there.
    """
    candidates = [run_ref, os.path.join(ROOT, "runs", run_ref),
                  os.path.join(ROOT, run_ref)]
    for cand in candidates:
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    raise VerifyError(
        "bundle dir not found: %r "
        "(expected runs/<id>/ with patch.diff + meta.json)" % (run_ref,))


def _load_meta(run_dir: str) -> dict:
    path = os.path.join(run_dir, "meta.json")
    if not os.path.isfile(path):
        raise VerifyError("missing meta.json in bundle: %s" % (run_dir,))
    try:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as exc:
        raise VerifyError("unparseable meta.json in %s: %s"
                          % (run_dir, exc))
    if not isinstance(meta, dict):
        raise VerifyError("meta.json is not an object in %s" % (run_dir,))
    return meta


def _case_name_from_meta(meta: dict, run_dir: str) -> str:
    for key in ("case", "case_id", "spec_case_id"):
        name = meta.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    raise VerifyError(
        "meta.json in %s names no case (need one of "
        "case/case_id/spec_case_id)" % (run_dir,))


def _pinned_tree(project: str, sha: str) -> str:
    """Locate the cached pinned tree, offline: it must already be at sha.

    Read-only against the shared cache (rev-parse only, never checkout):
    the RED rebuild happens in an isolated copy. No fetch/clone, so no
    network is ever needed; when the cache is absent or at another sha
    the fix names the network restore explicitly.
    """
    tree = str(vbackend.tree_dir(project, sha))
    if not os.path.isdir(tree):
        raise VerifyError(
            "pinned tree absent: %s (restore with network access, e.g. "
            "run the parent sim once, then re-verify)" % (tree,))
    try:
        proc = subprocess.run(
            ["git", "-C", tree, "rev-parse", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerifyError("cannot read pinned tree HEAD %s: %s"
                          % (tree, exc))
    head = (proc.stdout or "").strip()
    if proc.returncode != 0 or head != sha:
        raise VerifyError(
            "pinned tree %s is at %r, want pinned sha %s "
            "(restore with network access, then re-verify)"
            % (tree, head or proc.stdout.strip(), sha))
    return tree


def _copy_tree_offline(src: str, dest_parent: str) -> str:
    """Copy the pinned tree (minus .git/obj) to a fresh temp dir."""
    dest = tempfile.mkdtemp(prefix="afterglow-verify-RED-",
                            dir=dest_parent or None)

    def _skipped(name: str) -> bool:
        return name == ".git" or name.startswith("obj_afterglow_")

    def _ignore(_dir, names):
        return [n for n in names if _skipped(n)]

    for entry in os.listdir(src):
        if _skipped(entry):
            continue
        s = os.path.join(src, entry)
        d = os.path.join(dest, entry)
        if os.path.isdir(s) and not os.path.islink(s):
            shutil.copytree(s, d, ignore=_ignore)
        else:
            shutil.copy2(s, d)
    return dest


def verify_bundle(run_ref: str, work_parent: str | None = None,
                  keep_tree: bool = False) -> dict:
    """Re-verify one bundle. Returns a result dict (never raises verdicts).

    Result keys: run_id, run_dir, case, seed, expected, got,
    tohost_expected, tohost_got, match (bool), reason (str, "" on MATCH).
    Raises VerifyError only when verification cannot be attempted.
    """
    run_dir = resolve_bundle(run_ref)
    run_id = os.path.basename(run_dir.rstrip(os.sep))
    result = {"run_id": run_id, "run_dir": run_dir, "case": "?",
              "seed": None, "expected": None, "got": None,
              "tohost_expected": None, "tohost_got": None,
              "match": False, "reason": ""}
    meta = _load_meta(run_dir)
    name = _case_name_from_meta(meta, run_dir)
    result["case"] = name
    try:
        case = vbackend.load_case(name)
    except (OSError, ValueError, KeyError) as exc:
        raise VerifyError("unknown case %r (no sim/targets/%s.yaml): %s"
                          % (name, name, exc))
    if "faults" in case or "mutant_line" in case:
        raise VerifyError(
            "case %r needs the loop's pinned prepare path "
            "(pair/multi-fault or line-pinned mutant): keyless verify "
            "supports single-fault line-anchored cases only" % (name,))
    expected = meta.get("checksum_after")
    if not isinstance(expected, str) or not expected.strip():
        raise VerifyError(
            "meta.json in %s records no AFTER checksum "
            "(need checksum_after)" % (run_dir,))
    result["expected"] = expected.strip()
    result["tohost_expected"] = meta.get("tohost_after")
    seed = meta.get("sim_seed")
    if not isinstance(seed, int):
        raise VerifyError(
            "meta.json in %s records no sim seed (need sim_seed)" % (run_dir,))
    result["seed"] = seed
    patch_path = os.path.join(run_dir, "patch.diff")
    if not os.path.isfile(patch_path):
        raise VerifyError("missing patch.diff in bundle: %s" % (run_dir,))
    with open(patch_path, encoding="utf-8") as fh:
        diff_text = fh.read()
    if not diff_text.strip():
        raise VerifyError("patch.diff is empty in %s" % (run_dir,))
    pins = vbackend.load_pins()
    if case["project"] not in pins:
        raise VerifyError("unknown project %r (see targets.pin)"
                          % (case.get("project"),))
    _url, sha = pins[case["project"]]
    if str(meta.get("sim_tree_sha") or "") not in ("", sha):
        raise VerifyError(
            "bundle %s records sim_tree_sha %r, pinned sha is %s "
            "(tree moved since the run; cannot re-verify this bundle)"
            % (run_id, meta.get("sim_tree_sha"), sha))
    src = _pinned_tree(case["project"], sha)
    red_tree = _copy_tree_offline(src, work_parent or tempfile.gettempdir())
    try:
        try:
            vbackend.inject(red_tree, case)
        except RuntimeError as exc:
            raise VerifyError("RED inject failed in isolated copy: %s"
                              % (exc,))
        spec = loop.case_to_spec(case)
        tmp_patch = os.path.join(red_tree, ".verify_patch.diff")
        try:
            applied = loop.try_apply_diff(diff_text, red_tree, tmp_patch,
                                          spec=spec)
        finally:
            if os.path.exists(tmp_patch):
                os.remove(tmp_patch)
        if not (applied.get("guard_ok") and applied.get("applied")):
            reasons = applied.get("reasons") or ["unknown apply failure"]
            result["reason"] = ("patch would not apply to the RED tree: %s"
                                % ("; ".join(reasons)[:400]))
            return result
        boot_tmp = os.path.join(red_tree, ".verify_boot.log")
        verdict_tmp = os.path.join(red_tree, ".verify_verdict.json")
        try:
            verdict = loop.resim_after(red_tree, case, seed,
                                       boot_tmp, verdict_tmp)
        except Exception as exc:  # noqa: BLE001 -- build/run crash = no green
            result["reason"] = ("resim crashed (build or sim failure): %s"
                                % (exc,)[:400])
            return result
        result["got"] = verdict.get("checksum")
        result["tohost_got"] = verdict.get("tohost")
        if not result["got"]:
            result["reason"] = "resim produced no checksum"
            return result
        if result["got"] != result["expected"]:
            result["reason"] = (
                "checksum MISMATCH: resim %s vs bundle AFTER %s "
                "(tohost resim=%s bundle=%s)"
                % (result["got"], result["expected"],
                   result["tohost_got"], result["tohost_expected"]))
            return result
        result["match"] = True
        return result
    finally:
        if not keep_tree:
            shutil.rmtree(red_tree, ignore_errors=True)


def format_result(result: dict) -> str:
    """One-line verdict, mirroring the repro checksum contract."""
    if result.get("match"):
        return "checksum MATCH %s (case %s seed %s tohost %s)" % (
            result.get("got"), result.get("case"), result.get("seed"),
            result.get("tohost_got"))
    return "checksum MISMATCH: %s" % (result.get("reason") or "unknown",)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["-h"], ["--help"]):
        print("usage: afterglow verify runs/<id>   "
              "(keyless re-verification: RED -> apply patch.diff -> "
              "resim -> compare meta AFTER checksum; exit 0 MATCH, "
              "1 MISMATCH, 2 loud refusal)")
        return 0
    if len(args) != 1:
        print("FATAL: need exactly one bundle: afterglow verify runs/<id>",
              file=sys.stderr)
        return EXIT_FATAL
    try:
        result = verify_bundle(args[0])
    except VerifyError as exc:
        print("FATAL: %s" % (exc,), file=sys.stderr)
        return EXIT_FATAL
    except Exception as exc:  # noqa: BLE001 -- never a traceback verdict
        print("FATAL: verify crashed: %r" % (exc,), file=sys.stderr)
        return EXIT_FATAL
    print(format_result(result))
    return EXIT_MATCH if result["match"] else EXIT_MISMATCH


if __name__ == "__main__":
    sys.exit(main())
