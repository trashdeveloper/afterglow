"""Provenance-chain trail (Task D4-TRUE, plan item 34, Wave D).

Rebuilds ONE JSON trail fault -> diagnosis -> patch -> green from a
finished `runs/` bundle alone. Bundle reads only: no API calls, no
re-sim, no RTL edits. stdlib + repo imports only (`agent.guard` for
the patch verdict; never touches loop/guard/score/CI).

Each step carries its request id where one exists (fault and green
are pre/post-API local facts, so their id is None):

- fault:     case + mutant record + RED verdict (tohost/checksum/cycles)
- diagnosis: text api request id + prompt hash (+ image request id)
- patch:     patch.diff sha256 + guard verdict; request id = the text
             api call that authored the patch
- green:     resim checksum + verdict_after (pass/tohost/cycles)

API:

  build_trail(bundle_dir, rtl_root=None) -> dict
  verify_trail(trail) -> (ok, reasons)
  trail_matches_bundle(trail, bundle_dir) -> (ok, reasons)
"""
import hashlib
import json
import os

from agent import guard

STEPS = ("fault", "diagnosis", "patch", "green")


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


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


def _repo_root():
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def build_trail(bundle_dir, rtl_root=None):
    """Rebuild the provenance trail from a finished bundle directory.

    Reads (all required): meta.json, api/text_request.json,
    api/text_response.json, patch.diff, artifacts/verdict_before.json,
    artifacts/verdict_after.json. Optional: api/image_request.json,
    api/image_response.json (absent -> image ids None).

    Raises FileNotFoundError when a required bundle file is missing,
    ValueError when the bundle has no mutant record (a trail without a
    fault record proves nothing).
    """
    meta = _load_json(os.path.join(bundle_dir, "meta.json"))
    text_req = _load_json(os.path.join(bundle_dir, "api", "text_request.json"))
    text_resp = _load_json(os.path.join(bundle_dir, "api", "text_response.json"))
    patch_text = _load_text(os.path.join(bundle_dir, "patch.diff"))
    before = _load_json(os.path.join(bundle_dir, "artifacts",
                                     "verdict_before.json"))
    after = _load_json(os.path.join(bundle_dir, "artifacts",
                                    "verdict_after.json"))

    mutant_record = meta.get("mutant_record")
    if not isinstance(mutant_record, dict) or not mutant_record.get("file"):
        raise ValueError("bundle %s has no mutant_record: fault unprovable"
                         % (bundle_dir,))

    try:
        image_resp = _load_json(os.path.join(bundle_dir, "api",
                                             "image_response.json"))
        image_request_id = image_resp.get("request_id")
    except FileNotFoundError:
        image_request_id = None
    try:
        image_req = _load_json(os.path.join(bundle_dir, "api",
                                            "image_request.json"))
        image_prompt_hash = image_req.get("prompt_sha256")
    except FileNotFoundError:
        image_prompt_hash = None

    if rtl_root is None:
        mutant_tree = meta.get("mutant_tree")
        if (isinstance(mutant_tree, str) and mutant_tree
                and os.path.isdir(mutant_tree)):
            rtl_root = mutant_tree
        else:
            rtl_root = _repo_root()
    guard_ok, guard_reasons, guard_info = guard.check(patch_text,
                                                     rtl_root=rtl_root)

    case = meta.get("case") or before.get("case")
    text_request_id = text_resp.get("request_id")
    return {
        "bundle": os.path.basename(os.path.normpath(bundle_dir)),
        "case": case,
        "steps": {
            "fault": {
                "case": case,
                "mutant_record": mutant_record,
                "seed": meta.get("seed", before.get("seed")),
                "tohost_before": before.get("tohost"),
                "checksum_before": before.get("checksum"),
                "cycles_before": before.get("cycles"),
                "request_id": None,
            },
            "diagnosis": {
                "request_id": text_request_id,
                "prompt_hash": text_req.get("prompt_sha256"),
                "model": text_resp.get("model") or text_req.get("model"),
                "diagnosis": text_resp.get("diagnosis") or "",
                "latency_ms": text_resp.get("latency_ms"),
                "image_request_id": image_request_id,
                "image_prompt_hash": image_prompt_hash,
            },
            "patch": {
                "request_id": text_request_id,
                "patch_sha256": hashlib.sha256(
                    patch_text.encode("utf-8")).hexdigest(),
                "patch_lines": guard_info.get("patch_lines"),
                "guard_ok": guard_ok,
                "guard_reasons": list(guard_reasons),
                "guard_rtl_root": rtl_root,
            },
            "green": {
                "request_id": None,
                "pass": after.get("pass"),
                "tohost_after": after.get("tohost"),
                "checksum_after": after.get("checksum"),
                "cycles_after": after.get("cycles"),
            },
        },
    }


def verify_trail(trail):
    """Check a trail is complete and green. Returns (ok, reasons)."""
    reasons = []
    if not isinstance(trail, dict):
        return False, ["trail must be a dict"]
    steps = trail.get("steps")
    if not isinstance(steps, dict):
        return False, ["trail has no steps dict"]
    missing = [s for s in STEPS if s not in steps]
    if missing:
        return False, ["missing steps: %s" % (",".join(missing,))]
    if list(steps.keys()) != list(STEPS):
        return False, ["steps out of order: %s" % (",".join(steps.keys()),)]

    fault = steps["fault"]
    if not fault.get("case"):
        reasons.append("fault: empty case")
    mutant = fault.get("mutant_record")
    if not isinstance(mutant, dict) or not mutant.get("file"):
        reasons.append("fault: mutant record lacks file")
    elif not mutant.get("faults") and mutant.get("line") is None:
        reasons.append("fault: mutant record pinpoints no line")
    if not _is_hex64(fault.get("checksum_before") or ""):
        reasons.append("fault: bad checksum_before")
    if fault.get("request_id") is not None:
        reasons.append("fault: pre-API step must have null request_id")

    diag = steps["diagnosis"]
    if not diag.get("request_id"):
        reasons.append("diagnosis: missing api request id")
    if not _is_hex64(diag.get("prompt_hash") or ""):
        reasons.append("diagnosis: bad prompt hash")
    if not (isinstance(diag.get("diagnosis"), str)
            and diag["diagnosis"].strip()):
        reasons.append("diagnosis: empty diagnosis text")

    patch = steps["patch"]
    if not patch.get("request_id"):
        reasons.append("patch: missing authoring request id")
    if not _is_hex64(patch.get("patch_sha256") or ""):
        reasons.append("patch: bad patch sha256")
    lines = patch.get("patch_lines")
    if (isinstance(lines, bool) or not isinstance(lines, int)
            or not 1 <= lines <= guard.MAX_PATCH_LINES):
        reasons.append("patch: patch_lines out of 1..%d"
                       % (guard.MAX_PATCH_LINES,))
    if patch.get("guard_ok") is not True:
        reasons.append("patch: guard verdict not ACCEPT (%r)"
                       % (patch.get("guard_reasons"),))

    green = steps["green"]
    if green.get("pass") is not True:
        reasons.append("green: verdict is not PASS")
    tohost = green.get("tohost_after")
    if not (isinstance(tohost, str) and tohost.startswith("PASS")):
        reasons.append("green: tohost_after is not PASS")
    if not _is_hex64(green.get("checksum_after") or ""):
        reasons.append("green: bad checksum_after")
    if (green.get("checksum_after") and fault.get("checksum_before")
            and green["checksum_after"] == fault["checksum_before"]):
        reasons.append("green: checksum unchanged from RED (no fix landed)")
    if green.get("request_id") is not None:
        reasons.append("green: local resim step must have null request_id")

    return (len(reasons) == 0), reasons


def trail_matches_bundle(trail, bundle_dir):
    """Rebuild the trail and compare field-by-field. Returns (ok, reasons)."""
    if not isinstance(trail, dict) or "steps" not in trail:
        return False, ["not a trail dict"]
    recorded_root = ((trail.get("steps") or {}).get("patch") or {}).get(
        "guard_rtl_root")
    rebuilt = build_trail(bundle_dir, rtl_root=recorded_root)
    if (rebuilt == trail
            and list((rebuilt.get("steps") or {}).keys())
            == list((trail.get("steps") or {}).keys())):
        return True, []
    diffs = []
    if rebuilt.get("bundle") != trail.get("bundle"):
        diffs.append("bundle id")
    if rebuilt.get("case") != trail.get("case"):
        diffs.append("case")
    for step in STEPS:
        if rebuilt.get("steps", {}).get(step) != trail.get("steps", {}).get(step):
            diffs.append("steps.%s" % (step,))
    return False, ["trail differs from bundle in: %s" % (",".join(diffs,))]
