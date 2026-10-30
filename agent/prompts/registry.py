#!/usr/bin/env python3
"""Task H5: Prompt-strategy registry (plan item 85).

Interface (plan text, verbatim)::

  register(version, stats) / pick(case_class) -> version
    (historical winner per class; ties -> newest).

``register`` records one prompt version's A/B outcomes; ``pick``
returns the historical winner for a case class (highest win rate),
breaking ties toward the most recently registered version. A case
class with no history -- or an empty/missing store -- is a miss and
returns None (never a fabricated version string).

``stats`` is ``{case_class: outcome}`` where each outcome is
``{"wins": int, "runs": int}`` (``"runs"`` also accepts the aliases
``"total"`` / ``"trials"``); a ``(wins, runs)`` pair or a plain
non-negative int win count is also accepted and normalized on write.
Malformed version / stats / case_class raise TypeError/ValueError
loudly.

Store is a flat JSON file ``{version: {"stats": {...}, "seq": N}}``
(order isotope ``seq`` records registration recency for the
ties->newest rule). The path is injected via the optional ``store``
argument (tests pass a tmp-dir path); when omitted it defaults to
``$AF_PROMPT_REGISTRY`` or ``<tmpdir>/afterglow_prompt_registry.json``.
Writes are atomic (tmp file + os.replace).

Default-off contract (Wave H header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``json``/``os``/``tempfile`` only); no API, no keys, no
network.
"""

import json
import os
import tempfile

_ENV_VAR = "AF_PROMPT_REGISTRY"
_DEFAULT_NAME = "afterglow_prompt_registry.json"


def _default_store():
    """Default flat-file path when no store is injected."""
    env = os.environ.get(_ENV_VAR)
    if env:
        return env
    return os.path.join(tempfile.gettempdir(), _DEFAULT_NAME)


def _coerce_store(store):
    if store is None:
        return _default_store()
    if isinstance(store, (str, bytes, os.PathLike)):
        return os.fspath(store)
    raise TypeError(
        "registry: store must be a path or None, got %r" % (store,))


def _check_version(version):
    if not isinstance(version, str):
        raise TypeError(
            "registry: version must be a string, got %r" % (version,))
    if not version.strip():
        raise ValueError("registry: version must be non-empty")


def _check_case_class(case_class):
    if not isinstance(case_class, str):
        raise TypeError(
            "registry: case_class must be a string, got %r"
            % (case_class,))
    if not case_class.strip():
        raise ValueError("registry: case_class must be non-empty")


def _normalize_outcome(case_class, outcome):
    """Normalize one outcome to (wins, runs); raise loudly if malformed."""
    if isinstance(outcome, bool):
        raise TypeError(
            "registry: outcome for %r must not be bool, got %r"
            % (case_class, outcome))
    if isinstance(outcome, int):
        wins, runs = outcome, outcome
    elif isinstance(outcome, (list, tuple)):
        if len(outcome) != 2:
            raise ValueError(
                "registry: outcome for %r must be a (wins, runs) "
                "pair, got %r" % (case_class, outcome))
        wins, runs = outcome
    elif isinstance(outcome, dict):
        if "wins" not in outcome:
            raise ValueError(
                "registry: outcome for %r needs a 'wins' key, "
                "got %r" % (case_class, outcome))
        wins = outcome["wins"]
        runs = outcome.get("runs",
                           outcome.get("total", outcome.get("trials")))
        if runs is None:
            raise ValueError(
                "registry: outcome for %r needs a 'runs' "
                "(or 'total'/'trials') key, got %r"
                % (case_class, outcome))
    else:
        raise TypeError(
            "registry: outcome for %r must be a wins int, "
            "(wins, runs) pair, or {'wins', 'runs'} dict, got %r"
            % (case_class, outcome))
    if isinstance(wins, bool) or isinstance(runs, bool) \
            or not isinstance(wins, int) or not isinstance(runs, int):
        raise TypeError(
            "registry: wins/runs for %r must be ints, got %r"
            % (case_class, outcome))
    if runs < 1:
        raise ValueError(
            "registry: runs for %r must be >= 1, got %r"
            % (case_class, outcome))
    if not 0 <= wins <= runs:
        raise ValueError(
            "registry: need 0 <= wins <= runs for %r, got %r"
            % (case_class, outcome))
    return wins, runs


def _normalize_stats(stats):
    if not isinstance(stats, dict):
        raise TypeError(
            "registry: stats must be a {case_class: outcome} dict, "
            "got %r" % (stats,))
    if not stats:
        raise ValueError("registry: stats must be non-empty")
    normalized = {}
    for case_class, outcome in stats.items():
        _check_case_class(case_class)
        wins, runs = _normalize_outcome(case_class, outcome)
        normalized[case_class] = {"wins": wins, "runs": runs}
    return normalized


def _load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "registry: store %r is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ValueError(
            "registry: store %r must hold a JSON object, got %r"
            % (path, data))
    return data


def _save(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def register(version, stats, store=None):
    """Record ``version``'s A/B outcomes (``stats``) in the JSON store."""
    _check_version(version)
    normalized = _normalize_stats(stats)
    path = _coerce_store(store)
    data = _load(path)
    seq = 0
    for entry in data.values():
        if isinstance(entry, dict) and isinstance(entry.get("seq"), int):
            seq = max(seq, entry["seq"])
    data[version] = {"stats": normalized, "seq": seq + 1}
    _save(path, data)
    return version


def _score(entry_stats, case_class):
    outcome = entry_stats.get(case_class)
    if outcome is None:
        return None
    return (outcome["wins"], outcome["runs"])


def pick(case_class, store=None):
    """Return the historical winner version for ``case_class``.

    Highest win rate wins; exact rational comparison (no float drift);
    ties -> most recently registered. No history -> None.
    """
    _check_case_class(case_class)
    path = _coerce_store(store)
    data = _load(path)
    best = None
    best_key = None
    for version, entry in data.items():
        if not isinstance(entry, dict):
            continue
        score = _score(entry.get("stats", {}), case_class)
        if score is None:
            continue
        wins, runs = score
        seq = entry.get("seq", 0)
        key = (wins * 1.0 / runs, seq)
        # Exact comparison first (cross-multiplied), seq breaks ties.
        if best is None:
            best, best_key = version, key
            continue
        bwins, bruns = _score(
            data[best].get("stats", {}), case_class)
        if wins * bruns > bwins * runs:
            best, best_key = version, key
        elif wins * bruns == bwins * runs and seq > best_key[1]:
            best, best_key = version, key
    return best
