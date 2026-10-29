#!/usr/bin/env python3
"""Proof-bundle v2 replay (Task D1, plan item 31, Wave D).

Regenerates a bundle's AFTER verdict from the bundle contents alone --
``patch.diff`` + ``meta.json`` + ``artifacts/verdict_after.json`` -- plus
the repo sim backend (no API calls, no model, sim-only). Used two ways:

1. Library: :func:`replay_bundle` re-runs RED-baseline-inject + patch +
   build + sim and compares the replayed checksum/tohost against the
   recorded ``verdict_after.json``. An injectable ``sim_fn`` keeps unit
   tests fast and offline; the default path runs REAL Verilator.
2. Bundle script: :func:`write_replay_sh` drops an executable
   ``REPLAY.sh`` into a bundle; a fresh copy of that bundle replays to
   an identical checksum (the script never references the original path).

Contrast: ``agent/loop.py`` replays a stacked fix onto a fresh RED tree
during the run; this module replays a FINISHED bundle after the fact, so
a judge (or CI) can re-derive the verdict without trusting the log.

Exit contract (CLI ``python3 agent/trust/replay.py <bundle>``):
0 = checksum MATCH, 1 = MISMATCH or bundle error, 2 = usage error.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIMDIR = os.path.join(ROOT, "sim")


def checksum_of_boot(boot_text: str) -> str:
    """Checksum over deterministic sim lines only.

    Mirrors ``sim/vbackend.py::run_case`` exactly: lines starting with
    ``"- "`` (Verilator report / walltime noise) are excluded, the rest
    are joined with ``"\\n"`` plus a trailing newline and hashed.
    """
    det = [ln for ln in boot_text.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _default_sim(case: str, seed: int, patch_text: str, work_dir: str | None):
    """REAL Verilator replay with zero shared-tree mutation.

    Snapshots the PRISTINE case sources via ``git show <pinned-sha>`` out
    of the shared cache tree (read-only; never resets or injects there,
    so concurrent loop runs are undisturbed), then -- inside a private
    temp worktree -- injects the case bug, applies the bundle patch,
    builds, and runs. Returns ``(boot_text, verdict_dict)`` with the
    same checksum/tohost/cycles semantics as ``vbackend.run_case``.
    """
    import shutil  # noqa: E402  (local: keeps module import stdlib-light)

    sys.path.insert(0, SIMDIR)
    import vbackend  # noqa: E402  (repo import, stdlib-only module)

    spec = vbackend.load_case(case)
    pins = vbackend.load_pins()
    if spec["project"] not in pins:
        raise KeyError("unknown project %r" % spec["project"])
    url, sha = pins[spec["project"]]
    src = vbackend.tree_dir(spec["project"], sha)
    if not (src / ".git").exists():
        # Cold cache only: clone once, then proceed read-only.
        src = vbackend.ensure_tree(spec["project"], sha, url, spec["files"])
    work = tempfile.mkdtemp(prefix="trust-replay-", dir=work_dir or None)
    try:
        for rel in spec["files"]:
            proc = subprocess.run(
                ["git", "-C", str(src), "show", "%s:%s" % (sha, rel)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(
                    "cannot snapshot pristine %s: %s"
                    % (rel, proc.stdout.decode(errors="replace")))
            dst = os.path.join(work, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(proc.stdout)
        vbackend.inject(work, spec)
        patch_path = os.path.join(work, "replay-bundle.diff")
        with open(patch_path, "w", encoding="utf-8") as fh:
            fh.write(patch_text)
        proc = subprocess.run(
            ["git", "apply", "replay-bundle.diff"], cwd=work,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError("bundle patch does not apply: %s" % proc.stdout)
        binary = vbackend.build(work, spec)
        try:
            _rc, log = vbackend.sh([str(binary), "+seed=%d" % seed],
                                   cwd=str(work), timeout=300)
        except Exception:  # noqa: BLE001  (timeout/spawn failure -> RED log)
            log = "- TIMEOUT after 300s\ntohost: FAIL 0xdead\nTRAP: sim timeout\n"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tohost = None
    for ln in log.splitlines():
        if "tohost:" in ln:
            tohost = ln.split("tohost:", 1)[1].strip()
    m = re.search(r"CYCLES=(\d+)", log)
    verdict = {"case": case, "pass": bool(spec["oracle_pass"] in log),
               "tohost": tohost, "checksum": checksum_of_boot(log),
               "cycles": int(m.group(1)) if m else None, "seed": int(seed)}
    return log, verdict


def replay_bundle(bundle_dir: str, work_dir: str | None = None,
                  sim_fn=None) -> dict:
    """Replay one finished bundle; never raises on bundle problems.

    Returns a dict with ``ok`` (True iff the replayed checksum -- and
    tohost -- equal the recorded ``verdict_after.json``), plus
    ``expected_checksum``, ``replayed_checksum``, ``reason``, ``case``,
    ``seed``. Missing/corrupt bundle files yield ``ok=False`` with a
    ``reason`` naming the file, not an exception.
    """
    sim_fn = sim_fn or _default_sim
    run_id = os.path.basename(os.path.abspath(bundle_dir))
    try:
        meta = _read_json(os.path.join(bundle_dir, "meta.json"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "run_id": run_id, "reason": "unreadable meta.json: %s" % exc}
    case = meta.get("case_id") or meta.get("case")
    if not case:
        return {"ok": False, "run_id": run_id, "reason": "meta.json names no case"}
    seed = meta.get("sim_seed", meta.get("seed", 42))
    try:
        with open(os.path.join(bundle_dir, "patch.diff"), "r",
                   encoding="utf-8") as fh:
            patch_text = fh.read()
    except OSError as exc:
        return {"ok": False, "run_id": run_id, "case": case, "seed": seed,
                "reason": "unreadable patch.diff: %s" % exc}
    if not patch_text.strip():
        return {"ok": False, "run_id": run_id, "case": case, "seed": seed,
                "reason": "patch.diff is empty"}
    try:
        expected = _read_json(
            os.path.join(bundle_dir, "artifacts", "verdict_after.json"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "run_id": run_id, "case": case, "seed": seed,
                "reason": "unreadable artifacts/verdict_after.json: %s" % exc}
    try:
        _boot, verdict = sim_fn(case, int(seed), patch_text, work_dir)
    except Exception as exc:  # noqa: BLE001  (replay must report, not crash)
        return {"ok": False, "run_id": run_id, "case": case, "seed": seed,
                "expected_checksum": expected.get("checksum"),
                "reason": "replay sim failed: %r" % (exc,)}
    replayed_checksum = verdict.get("checksum")
    expected_checksum = expected.get("checksum")
    replayed_tohost = verdict.get("tohost")
    expected_tohost = expected.get("tohost")
    match = (replayed_checksum == expected_checksum
             and replayed_tohost == expected_tohost)
    return {"ok": bool(match), "run_id": run_id, "case": case, "seed": seed,
            "expected_checksum": expected_checksum,
            "replayed_checksum": replayed_checksum,
            "expected_tohost": expected_tohost,
            "replayed_tohost": replayed_tohost,
            "reason": "checksum MATCH" if match else
                      "checksum MISMATCH: replayed %s (%s) != recorded %s (%s)"
                      % (replayed_checksum, replayed_tohost,
                         expected_checksum, expected_tohost)}


REPLAY_SH_TEMPLATE = """#!/usr/bin/env bash
# REPLAY.sh -- proof-bundle v2 (Task D1, plan item 31, Wave D).
# Regenerates the AFTER verdict from THIS bundle's contents alone
# (patch.diff + meta.json + artifacts/verdict_after.json) using the repo
# sim backend, and checks the checksum matches verdict_after.json.
# Safe to run from a fresh copy of this bundle: it never references the
# original path. Set AF_REPO if this copy lives outside runs/<id>/.
set -euo pipefail
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${AF_REPO:-}" ]; then
  REPO_DIR="$AF_REPO"
elif [ -f "$BUNDLE_DIR/../../agent/trust/replay.py" ]; then
  REPO_DIR="$(cd "$BUNDLE_DIR/../.." && pwd)"
else
  echo "REPLAY: cannot locate repo (set AF_REPO=/path/to/afterglow)" >&2
  exit 2
fi
exec python3 "$REPO_DIR/agent/trust/replay.py" "$BUNDLE_DIR" "$@"
"""


def write_replay_sh(bundle_dir: str) -> str:
    """Write an executable ``REPLAY.sh`` into ``bundle_dir``.

    The script is portable: it resolves its own location at run time and
    never hardcodes the bundle's absolute path, so a fresh copy replays
    identically. Returns the script path.
    """
    path = os.path.join(bundle_dir, "REPLAY.sh")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(REPLAY_SH_TEMPLATE)
    os.chmod(path, 0o755)
    return path


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    work_dir = None
    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--work-dir" and i + 1 < len(argv):
            work_dir = argv[i + 1]
            i += 2
        else:
            positional.append(argv[i])
            i += 1
    if len(positional) != 1:
        print("usage: replay.py <runs/<id>> [--work-dir DIR]", file=sys.stderr)
        return 2
    res = replay_bundle(positional[0], work_dir=work_dir)
    print("REPLAY %s case=%s seed=%s" % (
        res.get("run_id"), res.get("case"), res.get("seed")))
    print("expected checksum %s (%s)" % (
        res.get("expected_checksum"), res.get("expected_tohost")))
    print("replayed checksum %s (%s)" % (
        res.get("replayed_checksum"), res.get("replayed_tohost")))
    print(res.get("reason"))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
