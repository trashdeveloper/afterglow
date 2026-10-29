"""Audit-mode hash chain (Task D8-TRUE, plan item 38, Wave D).

Dumps ONE JSON audit log chaining prompt -> response -> patch -> resim
from a finished `runs/` bundle alone. Bundle reads only: no API calls,
no re-sim, no RTL edits, no loop/guard/score/CI wiring. stdlib + repo
imports only (repo import: none -- pure stdlib; deliberately avoids
`agent.guard` so audit stays a read-only observer that can never gate
or alter a verdict).

Each link is hash-verified from bundle files (the dump records the
sha256 of the file bytes it read, plus the semantic hash inside):

- prompt:    api/text_request.json (prompt_sha256 field + file sha +
             prompt-content sha recomputed from the stored prompt)
- response:  api/text_response.json (request id + latency_ms + file
             sha + content sha recomputed from the stored content)
- patch:     patch.diff sha256 (file bytes)
- resim:     artifacts/verdict_after.json checksum (file sha +
             checksum_after + boot_after.log re-checksummed from bytes;
             boot_checksum_match records whether the log reproduces
             the recorded verdict)

The four link digests form a hash chain: digest[i] = sha256(prev +
canonical_json(link[i])), genesis "AUDIT-GENESIS". `chain_head` is the
last digest. Any post-dump edit of a bundle file, or of the audit log
itself, breaks `audit_matches_bundle()` / `verify_audit()`.

API:

  dump_audit(bundle_dir) -> dict
  verify_audit(audit) -> (ok, reasons)
  audit_matches_bundle(audit, bundle_dir) -> (ok, reasons)

CLI (`--audit` dump): python3 agent/trust/auditmode.py runs/<id>
prints the audit JSON to stdout; exit 0 iff the dump verifies AND
matches the bundle.
"""
import hashlib
import json
import os
import sys

LINKS = ("prompt", "response", "patch", "resim")

_GENESIS = "AUDIT-GENESIS"


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_hex64(value):
    if isinstance(value, bool) or not isinstance(value, str):
        return False
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower() and len(set(value)) > 1


def checksum_of_boot(boot_text):
    """Checksum over deterministic sim lines only.

    Mirrors `sim/vbackend.py::run_case`: lines starting with `"- "`
    (Verilator report / walltime noise) are excluded, the rest are
    joined with `"\\n"` plus a trailing newline and hashed.
    """
    det = [ln for ln in boot_text.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def _canonical(link):
    return json.dumps(link, sort_keys=True, separators=(",", ":"))


def _chain_links(links):
    """Recompute the digest chain over the four links in LINKS order."""
    chain = []
    prev = _GENESIS
    for name in LINKS:
        digest = hashlib.sha256(
            (prev + _canonical(links[name])).encode("utf-8")).hexdigest()
        chain.append(digest)
        prev = digest
    return chain


def dump_audit(bundle_dir):
    """Dump the audit chain from a finished bundle directory.

    Reads (all required): meta.json, api/text_request.json,
    api/text_response.json, patch.diff, artifacts/verdict_after.json,
    artifacts/boot_after.log. Raises FileNotFoundError when any is
    missing, ValueError when a required field is absent. Never raises
    on a RED bundle: a mismatch (e.g. boot log not reproducing the
    recorded checksum) is RECORDED (`boot_checksum_match: false`) and
    left for `verify_audit()` to reject.
    """
    meta = _load_json(os.path.join(bundle_dir, "meta.json"))
    req_raw = _load_text(os.path.join(bundle_dir, "api", "text_request.json"))
    resp_raw = _load_text(os.path.join(bundle_dir, "api",
                                       "text_response.json"))
    patch_text = _load_text(os.path.join(bundle_dir, "patch.diff"))
    after_raw = _load_text(os.path.join(bundle_dir, "artifacts",
                                        "verdict_after.json"))
    boot_text = _load_text(os.path.join(bundle_dir, "artifacts",
                                        "boot_after.log"))
    req = json.loads(req_raw)
    resp = json.loads(resp_raw)
    after = json.loads(after_raw)

    for field in ("prompt_sha256", "prompt"):
        if field not in req:
            raise ValueError("api/text_request.json lacks %r" % (field,))
    for field in ("request_id", "latency_ms", "content"):
        if field not in resp:
            raise ValueError("api/text_response.json lacks %r" % (field,))
    for field in ("checksum", "tohost", "pass"):
        if field not in after:
            raise ValueError("artifacts/verdict_after.json lacks %r"
                             % (field,))

    case = (meta.get("case") or meta.get("case_id")
            or after.get("case"))
    links = {
        "prompt": {
            "file": "api/text_request.json",
            "file_sha256": _sha_text(req_raw),
            "prompt_sha256": req["prompt_sha256"],
            "prompt_content_sha256": _sha_text(req["prompt"]),
            "model": req.get("model"),
        },
        "response": {
            "file": "api/text_response.json",
            "file_sha256": _sha_text(resp_raw),
            "request_id": resp["request_id"],
            "latency_ms": resp["latency_ms"],
            "content_sha256": _sha_text(resp["content"]),
            "model": resp.get("model"),
        },
        "patch": {
            "file": "patch.diff",
            "file_sha256": _sha_text(patch_text),
            "patch_sha256": _sha_text(patch_text),
            "patch_bytes": len(patch_text.encode("utf-8")),
        },
        "resim": {
            "file": "artifacts/verdict_after.json",
            "file_sha256": _sha_text(after_raw),
            "checksum_after": after["checksum"],
            "tohost_after": after["tohost"],
            "pass": after["pass"],
            "cycles": after.get("cycles"),
            "boot_checksum_match":
                checksum_of_boot(boot_text) == after["checksum"],
        },
    }
    chain = _chain_links(links)
    return {
        "bundle": os.path.basename(os.path.normpath(bundle_dir)),
        "case": case,
        "links": links,
        "chain": chain,
        "chain_head": chain[-1],
    }


def verify_audit(audit):
    """Check a dump is internally consistent. Returns (ok, reasons)."""
    reasons = []
    if not isinstance(audit, dict):
        return False, ["audit must be a dict"]
    links = audit.get("links")
    if not isinstance(links, dict):
        return False, ["audit has no links dict"]
    if list(links.keys()) != list(LINKS):
        return False, ["links out of order: %s" % (",".join(links.keys()),)]
    if not audit.get("bundle"):
        reasons.append("empty bundle id")
    if not audit.get("case"):
        reasons.append("empty case")

    prompt = links["prompt"]
    if not _is_hex64(prompt.get("prompt_sha256") or ""):
        reasons.append("prompt: bad prompt_sha256")
    if not _is_hex64(prompt.get("file_sha256") or ""):
        reasons.append("prompt: bad file_sha256")
    if not _is_hex64(prompt.get("prompt_content_sha256") or ""):
        reasons.append("prompt: bad prompt_content_sha256")
    if not (isinstance(prompt.get("model"), str) and prompt["model"].strip()):
        reasons.append("prompt: empty model")

    resp = links["response"]
    if not (isinstance(resp.get("request_id"), str)
            and resp["request_id"].strip()):
        reasons.append("response: missing api request id")
    latency = resp.get("latency_ms")
    if isinstance(latency, bool) or not isinstance(latency, int):
        reasons.append("response: bad latency_ms")
    elif latency < 0:
        reasons.append("response: negative latency_ms")
    if not _is_hex64(resp.get("file_sha256") or ""):
        reasons.append("response: bad file_sha256")
    if not _is_hex64(resp.get("content_sha256") or ""):
        reasons.append("response: bad content_sha256")

    patch = links["patch"]
    if not _is_hex64(patch.get("patch_sha256") or ""):
        reasons.append("patch: bad patch_sha256")
    if patch.get("file_sha256") != patch.get("patch_sha256"):
        reasons.append("patch: file_sha256 != patch_sha256")
    nbytes = patch.get("patch_bytes")
    if isinstance(nbytes, bool) or not isinstance(nbytes, int) or nbytes < 1:
        reasons.append("patch: bad patch_bytes")

    resim = links["resim"]
    if not _is_hex64(resim.get("checksum_after") or ""):
        reasons.append("resim: bad checksum_after")
    tohost = resim.get("tohost_after")
    if not (isinstance(tohost, str) and tohost.startswith("PASS")):
        reasons.append("resim: tohost_after is not PASS")
    if resim.get("pass") is not True:
        reasons.append("resim: verdict is not PASS")
    if resim.get("boot_checksum_match") is not True:
        reasons.append("resim: boot_after.log does not reproduce "
                       "checksum_after")

    chain = audit.get("chain")
    if not (isinstance(chain, list) and len(chain) == len(LINKS)
            and all(_is_hex64(d) for d in chain)):
        reasons.append("chain: must list 4 hex digests")
    elif chain != _chain_links(links):
        reasons.append("chain: digests do not recompute from links")
    if audit.get("chain_head") != (chain[-1] if isinstance(chain, list)
                                   and chain else None):
        reasons.append("chain: chain_head != last digest")

    return (len(reasons) == 0), reasons


def audit_matches_bundle(audit, bundle_dir):
    """Rebuild the dump and compare field-by-field. Returns (ok, reasons)."""
    if not isinstance(audit, dict) or "links" not in audit:
        return False, ["not an audit dict"]
    rebuilt = dump_audit(bundle_dir)
    if (rebuilt == audit
            and list(rebuilt["links"].keys())
            == list(audit["links"].keys())):
        return True, []
    diffs = []
    if rebuilt.get("bundle") != audit.get("bundle"):
        diffs.append("bundle id")
    if rebuilt.get("case") != audit.get("case"):
        diffs.append("case")
    for name in LINKS:
        if rebuilt.get("links", {}).get(name) != audit.get("links", {}).get(
                name):
            diffs.append("links.%s" % (name,))
    if rebuilt.get("chain") != audit.get("chain"):
        diffs.append("chain")
    if rebuilt.get("chain_head") != audit.get("chain_head"):
        diffs.append("chain_head")
    return False, ["audit differs from bundle in: %s" % (",".join(diffs,))]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print("usage: auditmode.py runs/<id>  (--audit dump to stdout)",
              file=sys.stderr)
        return 2
    try:
        audit = dump_audit(argv[0])
    except (OSError, ValueError) as exc:
        print("AUDIT ERROR: %s" % (exc,), file=sys.stderr)
        return 1
    print(json.dumps(audit, indent=2, sort_keys=False))
    ok, reasons = verify_audit(audit)
    if not ok:
        print("AUDIT VERIFY FAIL: %s" % ("; ".join(reasons,)),
              file=sys.stderr)
        return 1
    ok, reasons = audit_matches_bundle(audit, argv[0])
    if not ok:
        print("AUDIT MATCH FAIL: %s" % ("; ".join(reasons,)),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
