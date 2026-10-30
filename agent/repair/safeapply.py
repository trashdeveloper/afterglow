#!/usr/bin/env python3
"""Task G9: Safe auto-apply (plan item 79).

Produces::

  apply_logged(tree, patch, resim_fn, log=None, signoff_token_path=None)
    -> {"green", "rolled_back"}

Apply ``patch`` to ``tree``, resim, and auto-roll back on RED --
no human in the loop, every step appended to a log list.

- ``tree`` is a directory path (same convention as
  ``agent/repair/lintgate.py``).
- ``patch`` is ``{relpath: new_content}`` with str/bytes full-file
  replacements. Every target must already exist as a regular file
  under ``tree`` (missing targets raise FileNotFoundError before any
  verdict; the tree is left untouched).
- ``resim_fn(tree)`` runs against the patched tree: truthy is GREEN
  (patch kept), falsy is RED (byte-identical rollback). A resim
  exception rolls back first, then propagates (never a fabricated
  verdict, never a half-patched tree).
- ``log`` is a caller-provided list (a fresh list is used when None);
  every step is appended as a ``{"step": ...}`` dict: ``apply``,
  then ``resim``, then ``rollback`` on RED / resim error (no
  ``rollback`` entry on GREEN).
- ``signoff_token_path`` honors D10 (``agent/trust/signoff.py``):
  trees under the system temp dir are fixture copies (no token
  needed); any other (real) tree requires a token file whose content
  exactly matches ``signoff.PINNED_TOKEN`` (verified with
  ``signoff.gate_green``). Without a valid token a real-tree apply
  raises PermissionError fail-closed, before touching the tree.

Returns ``{"green": bool, "rolled_back": bool}``: GREEN-kept is
``{"green": True, "rolled_back": False}``; RED-rolled-back is
``{"green": False, "rolled_back": True}``.

Honesty contract (mirrors ``agent/repair/lintgate.py``: vacuous
input raises, resim errors propagate, deterministic -- adapted from
lint-gate to safe-apply because apply must snapshot/restore the tree
bytes itself, so the graded shape here is "failing resim triggers
rollback with tree bytes identical before/after and log
apply->resim->rollback" instead of a lint skip).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and
no caller is wired: agent/loop.py and agent/guard.py are untouched.

Stdlib (``os``, ``tempfile``) + one repo import
(``agent/trust/signoff.py``); no API, no keys, no network.
"""

import os
import tempfile

from agent.trust import signoff


def _require_tree(tree):
    """Validate the tree dir path, returning it as a string."""
    if isinstance(tree, os.PathLike):
        tree = os.fspath(tree)
    if isinstance(tree, bytes):
        try:
            tree = os.fsdecode(tree)
        except (OSError, UnicodeError):
            raise ValueError(
                "safeapply needs a tree dir path, got undecodable %r: "
                "refusing to patch an unnamed tree" % (tree,))
    if not isinstance(tree, str) or not tree.strip():
        raise ValueError(
            "safeapply needs a non-blank tree dir path, got %r: refusing "
            "to patch an unnamed tree" % (tree,))
    if not os.path.isdir(tree):
        raise FileNotFoundError(
            "safeapply: tree dir does not exist: %s (refusing to patch "
            "a missing tree)" % tree)
    return tree


def _require_patch(patch):
    """Validate the patch dict, returning a list of (relpath, bytes)."""
    if not isinstance(patch, dict):
        raise TypeError(
            "safeapply: patch must be a {relpath: new_content} dict, "
            "got %r" % (patch,))
    if not patch:
        raise ValueError(
            "safeapply: patch is empty: nothing to apply (never a "
            "vacuous pass)")
    items = []
    for relpath, content in patch.items():
        if not isinstance(relpath, str) or not relpath.strip():
            raise ValueError(
                "safeapply: patch relpath must be a non-blank string, "
                "got %r" % (relpath,))
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError(
                "safeapply: patch content for %r must be str/bytes, "
                "got %r" % (relpath, content))
        items.append((relpath, bytes(content)))
    return items


def _is_fixture_copy(tree):
    """True when the tree lives under the system temp dir."""
    return os.path.abspath(tree).startswith(
        os.path.abspath(tempfile.gettempdir()) + os.sep)


def _require_signoff(tree, signoff_token_path):
    """Enforce D10: real-tree apply needs a valid sign-off token."""
    if _is_fixture_copy(tree):
        return
    verdict = signoff.gate_green({}, signoff_token_path)
    if not verdict["signed_off"]:
        raise PermissionError(
            "safeapply: refusing real-tree apply without a valid "
            "sign-off token (D10): %s" % verdict["reason"])


def _snapshot(tree):
    """Map every file under tree (relpath -> bytes)."""
    snap = {}
    for dirpath, _dirnames, filenames in os.walk(tree):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, tree)
            with open(full, "rb") as handle:
                snap[rel] = handle.read()
    return snap


def _restore(tree, snap):
    """Restore every snapshotted file byte-identical."""
    for rel, content in snap.items():
        full = os.path.join(tree, rel)
        with open(full, "wb") as handle:
            handle.write(content)


def _check_targets(tree, items):
    """Every patch target must already exist as a file (fail closed)."""
    for relpath, _content in items:
        full = os.path.join(tree, relpath)
        if not os.path.isfile(full):
            raise FileNotFoundError(
                "safeapply: patch target missing: %s (refusing to "
                "apply a patch with unknown targets)" % full)


def apply_logged(tree, patch, resim_fn, log=None,
                 signoff_token_path=None):
    """Apply patch to tree, resim, auto-roll back on RED.

    Returns ``{"green": bool, "rolled_back": bool}``. Every step is
    appended to ``log`` as ``{"step": ...}`` (``apply`` -> ``resim``
    -> ``rollback`` on RED). Raises TypeError / ValueError /
    FileNotFoundError on vacuous input, PermissionError on a
    real-tree apply without a valid D10 sign-off token; a resim
    exception rolls back first, then propagates.
    """
    tree = _require_tree(tree)
    items = _require_patch(patch)
    if resim_fn is None:
        raise ValueError(
            "safeapply needs resim_fn(tree) -> verdict, got None: "
            "refusing to apply a patch that cannot be re-simulated")
    if not callable(resim_fn):
        raise ValueError(
            "safeapply needs resim_fn callable, got %r" % (resim_fn,))
    if log is None:
        log = []
    if not isinstance(log, list):
        raise TypeError(
            "safeapply: log must be a list, got %r" % (log,))

    _require_signoff(tree, signoff_token_path)
    _check_targets(tree, items)

    snap = _snapshot(tree)
    for relpath, content in items:
        with open(os.path.join(tree, relpath), "wb") as handle:
            handle.write(content)
    log.append({"step": "apply", "files": [rel for rel, _ in items]})

    try:
        verdict = resim_fn(tree)
    except Exception:
        _restore(tree, snap)
        log.append({"step": "resim", "ok": False, "error": True})
        log.append({"step": "rollback", "reason": "resim raised"})
        raise
    green = bool(verdict)
    # A dict verdict (e.g. {"passed": ...}) is truthy even when RED;
    # honor its explicit flag when present.
    if isinstance(verdict, dict):
        for key in ("passed", "pass", "green", "ok"):
            if key in verdict:
                green = bool(verdict[key])
                break
    log.append({"step": "resim", "ok": green})
    if green:
        return {"green": True, "rolled_back": False}
    _restore(tree, snap)
    log.append({"step": "rollback", "reason": "resim RED"})
    return {"green": False, "rolled_back": True}
