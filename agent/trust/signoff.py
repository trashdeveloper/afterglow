"""Sign-off gate (Task D10-TRUE, plan item 40, Wave D -- LAST plan item).

``--signoff`` requires an approval token file before GREEN is finalized.

Interface::

  gate_green(bundle, token_path) -> {"signed_off", "reason"}

``bundle`` must be a dict (anything else raises ValueError loudly).
``token_path`` is the approval token file path. The file content must
match :data:`PINNED_TOKEN` exactly:

- missing file (no path, blank path, nonexistent, directory,
  unreadable) -> {"signed_off": False, reason names "missing"/"blank"}.
- blank file (empty or whitespace-only) -> block, reason names "blank".
- tampered file (any byte != PINNED_TOKEN) -> block, reason names
  mismatch/tampered/invalid.
- exact pinned content match -> {"signed_off": True, reason "OK ..."}.

Default-off contract (Wave D header: additive + default-off): this
module runs ONLY on explicit call. It is imported by nothing in
agent/loop.py, agent/guard.py, eval/score.py, scripts/ci.sh, or docs --
those files are untouched.

No API, no network, no keys: stdlib only (os).
"""
import os

PINNED_TOKEN = "AFTERGLOW-SIGNOFF-APPROVED-v1:9f2c\n"


def gate_green(bundle, token_path=None):
    """Check the sign-off approval token before finalizing GREEN.

    Returns {"signed_off": bool, "reason": str}. Only an exact
    :data:`PINNED_TOKEN` file match passes. Anything else blocks.
    Raises ValueError if bundle is not a dict.
    """
    if not isinstance(bundle, dict):
        raise ValueError(
            "signoff: bundle must be a dict, got %r" % type(bundle))
    if token_path is None:
        return {"signed_off": False,
                "reason": "BLOCKED: sign-off token file missing "
                          "(no path given)"}
    try:
        path_str = os.fspath(token_path)
    except TypeError:
        return {"signed_off": False,
                "reason": "BLOCKED: sign-off token path invalid: %r"
                          % (token_path,)}
    if isinstance(path_str, bytes):
        try:
            path_str = os.fsdecode(path_str)
        except (OSError, UnicodeError):
            return {"signed_off": False,
                    "reason": "BLOCKED: sign-off token path invalid"}
    if not isinstance(path_str, str) or not path_str.strip():
        return {"signed_off": False,
                "reason": "BLOCKED: sign-off token file missing "
                          "(blank path)"}
    if not os.path.isfile(path_str):
        return {"signed_off": False,
                "reason": "BLOCKED: sign-off token file missing: %s"
                          % path_str}
    try:
        with open(path_str, "r", encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return {"signed_off": False,
                "reason": "BLOCKED: sign-off token file missing "
                          "(unreadable %s: %s)" % (path_str, exc)}
    if not content.strip():
        return {"signed_off": False,
                "reason": "BLOCKED: sign-off token file blank: %s"
                          % path_str}
    if content != PINNED_TOKEN:
        return {"signed_off": False,
                "reason": "BLOCKED: sign-off token mismatch "
                          "(tampered/invalid token): %s" % path_str}
    return {"signed_off": True,
            "reason": "OK: sign-off token verified, GREEN finalized"}
