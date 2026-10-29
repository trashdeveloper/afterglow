"""Task C10: Incremental rebuild cache (plan item 30, Wave C FIX BETTER).

Preserves the Verilator obj dir across attempts when only one file
changed -- versus the current ``agent/loop.py::resim_with_integrity``
drop-on-suspect, which removes ``obj_afterglow_<case>`` on ANY
stale-tree / stale-build suspect. The suspect path still drops (loop
parity: a suspect build is never trusted to be incremental).

Interface::

  obj_dir_for(tree, case) -> str
  snapshot(tree, files) -> {rel: sha256}
  changed_files(before, after) -> [sorted changed rels]
  decide(before, after, suspect=False, suspect_detail="") ->
      {"reuse", "drop", "changed", "reason"}
  rebuild(tree, case, build_fn, before=None, after=None, suspect=False,
          suspect_detail="") ->
      {"reused", "dropped", "changed", "reason", "build_secs",
       "result", "obj_dir"}

Policy (``decide``):
- ``suspect`` truthy -> DROP (clean rebuild, obj dir removed), with
  ``suspect_detail`` in ``reason``. Suspect evidence always beats the
  file count: even a single-file change under suspect rebuilds clean.
- 0 or 1 changed files, no suspect -> REUSE (obj dir kept, the
  ``build_fn`` runs incrementally against the cached objects).
- 2+ changed files, no suspect -> DROP (too much changed to trust the
  cache; clean rebuild).

``rebuild`` enforces the policy then runs
``build_fn(tree, case) -> result`` exactly once, timing it with
``time.monotonic`` (``build_secs``). The result is forwarded verbatim;
a ``build_fn`` exception propagates loudly (never a fabricated
``reused``). ``before``/``after`` default to a live snapshot of
``case["files"]`` (no baseline given = assume nothing changed, reuse
unless suspect).

Honesty contract (mirrors lintgate/transfer/rankfix):
- Blank tree, non-dict case, missing/blank case name, a missing RTL
  source on disk, non-dict snapshots, or ``build_fn`` None /
  non-callable all raise loudly (REFUSE the rebuild; never fabricate
  reuse True/False over bad inputs).
- Unlike ``loop.snapshot_file_hashes`` (which skips missing files so
  the loop never crashes on provenance), ``snapshot`` raises
  FileNotFoundError on a missing source: the cache decision must be
  made over exactly the files it claims, never over a silent subset.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``hashlib``/``os``/``shutil``/``time``); no API, no keys,
no network.
"""

import hashlib
import os
import shutil
import time


def _require_tree(tree):
    if isinstance(tree, os.PathLike):
        tree = os.fspath(tree)
    if not isinstance(tree, (str, bytes)) or not str(tree).strip():
        raise ValueError(
            "rebuildcache needs a non-blank tree path, got %r: refusing "
            "to decide a rebuild cache for an unnamed tree" % (tree,))
    return str(tree)


def _require_case(case):
    if not isinstance(case, dict):
        raise ValueError(
            "rebuildcache needs a case dict, got %r" % (case,))
    name = case.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            "rebuildcache needs case['name'] non-blank, got %r: refusing "
            "to guess the obj dir" % (name,))
    return name.strip()


def _require_files(files):
    if (not isinstance(files, (list, tuple)) or not files
            or not all(isinstance(f, str) and f.strip() for f in files)):
        raise ValueError(
            "rebuildcache needs files as a non-empty string list, "
            "got %r" % (files,))
    return [str(f) for f in files]


def obj_dir_for(tree, case):
    """Absolute obj dir for this case (``obj_afterglow_<name>``)."""
    tree = _require_tree(tree)
    name = _require_case(case)
    return os.path.join(tree, "obj_afterglow_%s" % name)


def _sha_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(tree, files):
    """Content-hash the named tree-relative RTL sources.

    Every listed file must exist: a missing source raises
    FileNotFoundError (fail closed -- the cache decision must cover
    exactly the files it claims, never a silent subset).
    """
    tree = _require_tree(tree)
    rels = _require_files(files)
    out = {}
    for rel in rels:
        path = os.path.join(tree, rel)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                "rebuildcache: missing RTL source: %s (refusing to "
                "snapshot a tree without its sources)" % path)
        out[rel] = _sha_file(path)
    return out


def changed_files(before, after):
    """Sorted rels whose hash differs (added/removed/modified)."""
    if not isinstance(before, dict):
        raise TypeError(
            "rebuildcache: before must be a snapshot dict, got %r"
            % (before,))
    if not isinstance(after, dict):
        raise TypeError(
            "rebuildcache: after must be a snapshot dict, got %r"
            % (after,))
    return sorted(r for r in set(before) | set(after)
                  if before.get(r) != after.get(r))


def decide(before, after, suspect=False, suspect_detail=""):
    """Apply the reuse policy; pure predicate, no I/O.

    Returns ``{"reuse", "drop", "changed", "reason"}`` with
    ``drop == (not reuse)``. Suspect always drops (loop parity);
    otherwise 0-1 changed files reuse, 2+ drop.
    """
    changed = changed_files(before, after)
    if suspect:
        detail = str(suspect_detail or "").strip() or "suspect build"
        return {
            "reuse": False,
            "drop": True,
            "changed": changed,
            "reason": ("suspect path (%s): clean rebuild forced, "
                       "obj dir dropped" % detail),
        }
    if len(changed) <= 1:
        return {
            "reuse": True,
            "drop": False,
            "changed": changed,
            "reason": ("%d file(s) changed (%s): incremental rebuild, "
                       "obj dir kept"
                       % (len(changed), ", ".join(changed) or "none")),
        }
    return {
        "reuse": False,
        "drop": True,
        "changed": changed,
        "reason": ("%d files changed (%s): too much for the cache, "
                   "clean rebuild forced, obj dir dropped"
                   % (len(changed), ", ".join(changed))),
    }


def rebuild(tree, case, build_fn, before=None, after=None, suspect=False,
            suspect_detail=""):
    """Enforce the cache policy, run ``build_fn(tree, case)`` once.

    Drops ``obj_afterglow_<case>`` exactly when ``decide`` says drop
    (suspect or 2+ changed files), keeps it otherwise, then runs the
    build and times it. Returns ``{"reused", "dropped", "changed",
    "reason", "build_secs", "result", "obj_dir"}``. ``build_fn`` None
    / non-callable raises; a build exception propagates (never a fake
    ``reused`` verdict).
    """
    tree = _require_tree(tree)
    if not isinstance(case, dict):
        raise ValueError(
            "rebuildcache needs a case dict, got %r" % (case,))
    if build_fn is None:
        raise ValueError(
            "rebuildcache needs build_fn(tree, case) -> result, got "
            "None: refusing to decide a rebuild that cannot run")
    if not callable(build_fn):
        raise ValueError(
            "rebuildcache needs build_fn callable, got %r" % (build_fn,))
    if before is None:
        rels = case.get("files", [])
        before = snapshot(tree, rels) if rels else {}
    if after is None:
        after = dict(before)
    decision = decide(before, after, suspect=suspect,
                      suspect_detail=suspect_detail)
    obj = obj_dir_for(tree, case)
    dropped = False
    if decision["drop"]:
        shutil.rmtree(obj, ignore_errors=True)
        dropped = True
    t0 = time.monotonic()
    result = build_fn(tree, case)
    secs = time.monotonic() - t0
    return {"reused": decision["reuse"], "dropped": dropped,
            "changed": decision["changed"], "reason": decision["reason"],
            "build_secs": float(secs), "result": result, "obj_dir": obj}
