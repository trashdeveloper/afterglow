#!/usr/bin/env python3
"""Third-party replay certification (Task H7, plan item 87, Wave H).

Certifies a finished proof bundle from a scratch copy: the bundle is
staged into ``clean_checkout`` (a fresh directory, or a pre-staged copy
used as-is), then two independent checks run against the SCRATCH COPY
alone -- never trusting the original path:

1. Replay: ``agent/trust/replay.py::replay_bundle`` re-derives the AFTER
   verdict (patch.diff + meta.json + artifacts/verdict_after.json) and
   compares the replayed checksum/tohost against the recorded verdict.
2. Audit chain: ``agent/trust/auditmode.py`` dumps and verifies the
   prompt -> response -> patch -> resim hash chain from the copy, and the
   copy's links are compared field-by-field against the original bundle
   (any post-copy tampering names the broken link).

API::

  certify(bundle_dir, clean_checkout, sim_fn=None)
    -> {"certified": bool, "reasons": [str], "stamp": str, ...}

``sim_fn`` is the injectable sim backend passed through to
``replay_bundle`` (unit tests inject a fake; default runs REAL
Verilator). On success ``reasons`` is exactly
``["OK: replay MATCH + audit chain verified"]``. A stamp file
``certification.json`` is written into the scratch copy recording the
verdict, the chain head, and both checksums.

Exit contract (CLI ``python3 agent/trust/replaycert.py <bundle>
<scratch>``): 0 = certified, 1 = not certified, 2 = usage error.

stdlib + repo imports only (repo: agent.trust.replay, agent.trust.auditmode).
No API calls, no keys, no network.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if ROOT not in sys.path:
    # Standalone-CLI support: `python3 agent/trust/replaycert.py ...`
    # runs with agent/trust/ (not the repo root) on sys.path, so
    # bootstrap the root for the `agent.trust` imports below.
    sys.path.insert(0, ROOT)

from agent.trust import auditmode, replay

STAMP_NAME = "certification.json"
OK_REASON = "OK: replay MATCH + audit chain verified"


def _stage_scratch_copy(bundle_dir: str, clean_checkout: str) -> str:
    """Stage the bundle into the scratch checkout; return its path.

    - ``clean_checkout`` missing -> ``copytree(bundle_dir, clean_checkout)``.
    - existing empty dir -> copy the bundle contents into it.
    - existing non-empty dir -> pre-staged third-party copy, used as-is.
    Raises ValueError / OSError with a message when staging is impossible.
    """
    if not os.path.isdir(bundle_dir):
        raise ValueError("bundle dir missing: %s" % bundle_dir)
    if not os.path.lexists(clean_checkout):
        shutil.copytree(bundle_dir, clean_checkout)
        return clean_checkout
    if not os.path.isdir(clean_checkout):
        raise ValueError("clean checkout is not a directory: %s"
                         % clean_checkout)
    if not os.listdir(clean_checkout):
        for name in os.listdir(bundle_dir):
            src = os.path.join(bundle_dir, name)
            dst = os.path.join(clean_checkout, name)
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        return clean_checkout
    return clean_checkout  # pre-staged copy: certify from it as-is


def _link_diffs(orig: dict, copy: dict) -> list:
    """Field-by-field link comparison (ignores the basename ``bundle`` id).

    Returns reason strings naming each broken link, e.g.
    ``"copy differs from bundle in links.patch"``.
    """
    diffs = []
    if orig.get("case") != copy.get("case"):
        diffs.append("copy differs from bundle in case")
    for name in auditmode.LINKS:
        if orig.get("links", {}).get(name) != copy.get("links", {}).get(name):
            diffs.append("copy differs from bundle in links.%s" % name)
    if orig.get("chain") != copy.get("chain"):
        diffs.append("copy differs from bundle in chain")
    if orig.get("chain_head") != copy.get("chain_head"):
        diffs.append("copy differs from bundle in chain_head")
    return diffs


def certify(bundle_dir: str, clean_checkout: str, sim_fn=None) -> dict:
    """Certify a bundle from a scratch copy; never raises on bundle problems.

    Returns a dict with ``certified`` (True iff replay MATCHES and the
    audit chain verifies with the scratch copy faithful to the bundle),
    ``reasons`` (list of strings; the OK sentence or the broken links),
    ``stamp`` (certification.json path in the scratch copy, or "" when
    staging failed), plus ``chain_head``, ``expected_checksum`` and
    ``replayed_checksum`` when available.
    """
    base = {"certified": False, "reasons": [], "stamp": "",
            "chain_head": None, "expected_checksum": None,
            "replayed_checksum": None}
    try:
        staged = _stage_scratch_copy(bundle_dir, clean_checkout)
    except (OSError, ValueError) as exc:
        base["reasons"] = ["cannot stage scratch copy: %s" % exc]
        return base

    try:
        orig_audit = auditmode.dump_audit(bundle_dir)
    except (OSError, ValueError) as exc:
        orig_audit = None
        orig_error = "audit dump of bundle failed: %s" % exc
    else:
        orig_error = ""
    try:
        copy_audit = auditmode.dump_audit(staged)
    except (OSError, ValueError) as exc:
        base["reasons"] = (["audit dump of scratch copy failed: %s" % exc]
                           + ([orig_error] if orig_error else []))
        _write_stamp(staged, base)
        base["stamp"] = os.path.join(staged, STAMP_NAME)
        return base

    base["chain_head"] = copy_audit.get("chain_head")

    ok_audit, audit_reasons = auditmode.verify_audit(copy_audit)
    faithfulness = _link_diffs(orig_audit, copy_audit) if orig_audit else [
        orig_error]

    replay_res = replay.replay_bundle(staged, sim_fn=sim_fn)
    base["expected_checksum"] = replay_res.get("expected_checksum")
    base["replayed_checksum"] = replay_res.get("replayed_checksum")
    replay_ok = bool(replay_res.get("ok"))

    reasons = []
    if not ok_audit:
        reasons.extend("audit %s" % r for r in audit_reasons)
    reasons.extend(faithfulness)
    if not replay_ok:
        reasons.append("replay: %s" % replay_res.get("reason"))

    if not reasons:
        base["certified"] = True
        base["reasons"] = [OK_REASON]
    else:
        base["reasons"] = reasons
    _write_stamp(staged, base)
    base["stamp"] = os.path.join(staged, STAMP_NAME)
    return base


def _write_stamp(staged: str, result: dict) -> str:
    """Write certification.json into the scratch copy; return its path."""
    path = os.path.join(staged, STAMP_NAME)
    stamp = {"certified": result["certified"], "reasons": result["reasons"],
             "chain_head": result.get("chain_head"),
             "expected_checksum": result.get("expected_checksum"),
             "replayed_checksum": result.get("replayed_checksum")}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(stamp, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError:
        pass
    return path


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: replaycert.py <runs/<id>> <clean-checkout>",
              file=sys.stderr)
        return 2
    res = certify(argv[0], argv[1])
    print("CERTIFY bundle=%s checkout=%s" % (argv[0], argv[1]))
    print("certified=%s chain_head=%s" % (res["certified"],
                                          res.get("chain_head")))
    for reason in res["reasons"]:
        print(reason)
    print("stamp: %s" % res["stamp"])
    return 0 if res["certified"] else 1


if __name__ == "__main__":
    sys.exit(main())
