#!/usr/bin/env python3
"""Task H9: Persistent fix library (plan item 89).

Persists the ``transfer.build_index`` fix index to disk with a
version stamp; a version-mismatched file refuses to load.

Interface::

  FIXLIB_VERSION = 1
  save(index, path) -> None
  load(path) -> index

``index`` is a ``transfer.build_index`` result
(``{"entries": [{"signals": tuple, "patch": str}],
"by_signal": {signal: [entry indices]}}``). On disk the entries are
plain JSON lists; :func:`load` restores ``signals`` to tuples so the
reloaded index suggests the same top fix as the original.

Version mismatch (missing, non-int, or != FIXLIB_VERSION) raises
ValueError. Missing files raise FileNotFoundError; corrupt JSON
raises ValueError. Bad index / bad path raise TypeError/ValueError
(refuse to fabricate persistence).

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``json`` only); no API, no keys, no network.
"""

import json

FIXLIB_VERSION = 1


def _check_index(index):
    """Validate a transfer-style index; return it or raise."""
    if not isinstance(index, dict):
        raise TypeError(
            "fixlib: index must be a build_index result dict, got %r"
            % (index,))
    entries = index.get("entries")
    by_signal = index.get("by_signal")
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            "fixlib: index must hold a non-empty 'entries' list, "
            "got %r" % (index,))
    if not isinstance(by_signal, dict):
        raise ValueError(
            "fixlib: index must hold a 'by_signal' dict, got %r"
            % (index,))
    for pos, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(
                "fixlib: entry %d must be a dict, got %r" % (pos, entry))
        signals = entry.get("signals")
        patch = entry.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            raise ValueError(
                "fixlib: entry %d patch must be a non-empty diff "
                "string, got %r" % (pos, patch))
        if isinstance(signals, (list, tuple)):
            names = list(signals)
        else:
            raise ValueError(
                "fixlib: entry %d signals must be a name list/tuple, "
                "got %r" % (pos, signals))
        if not names or any(not isinstance(s, str) or not s
                            for s in names):
            raise ValueError(
                "fixlib: entry %d signals must be non-empty strings, "
                "got %r" % (pos, signals))
    for sig, refs in by_signal.items():
        if not isinstance(sig, str) or not sig:
            raise ValueError(
                "fixlib: by_signal keys must be non-empty strings, "
                "got %r" % (sig,))
        if (not isinstance(refs, list) or not refs
                or any(isinstance(r, bool) or not isinstance(r, int)
                       or r < 0 or r >= len(entries) for r in refs)):
            raise ValueError(
                "fixlib: by_signal[%r] must be a non-empty list of "
                "entry indices, got %r" % (sig, refs))
    return index


def _check_path(path):
    """Validate a file path; return it or raise."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError(
            "fixlib: path must be a non-empty string, got %r" % (path,))
    return path


def save(index, path):
    """Persist ``index`` to ``path`` with a version stamp.

    Overwrites ``path`` with JSON
    ``{"version": FIXLIB_VERSION, "index": {...}}``. Bad index or
    bad path raises TypeError/ValueError.
    """
    _check_index(index)
    _check_path(path)
    doc = {
        "version": FIXLIB_VERSION,
        "index": {
            "entries": [
                {"signals": list(entry["signals"]),
                 "patch": entry["patch"]}
                for entry in index["entries"]
            ],
            "by_signal": {
                sig: list(refs)
                for sig, refs in index["by_signal"].items()
            },
        },
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")


def load(path):
    """Reload an index saved by :func:`save`.

    Returns ``{"entries": [{"signals": tuple, "patch": str}],
    "by_signal": {signal: [entry indices]}}``. Version missing or
    != FIXLIB_VERSION raises ValueError; missing file raises
    FileNotFoundError; corrupt JSON raises ValueError.
    """
    _check_path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            "fixlib: corrupt library file %r: %s" % (path, exc))
    except OSError as exc:
        raise ValueError(
            "fixlib: cannot read library file %r: %s" % (path, exc))
    if not isinstance(doc, dict):
        raise ValueError(
            "fixlib: library file %r must hold a JSON object, got %r"
            % (path, doc))
    if doc.get("version") != FIXLIB_VERSION:
        raise ValueError(
            "fixlib: version mismatch in %r: got %r, want %r"
            % (path, doc.get("version"), FIXLIB_VERSION))
    index = doc.get("index")
    _check_index(index)
    return {
        "entries": [
            {"signals": tuple(entry["signals"]),
             "patch": entry["patch"]}
            for entry in index["entries"]
        ],
        "by_signal": {
            sig: list(refs) for sig, refs in index["by_signal"].items()
        },
    }
