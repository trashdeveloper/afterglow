"""Decision audit trail (Task D4, plan item 34, Wave D).

Logs every agent decision with timestamp, model id, and prompt hash;
`replay_decisions()` reconstructs the decision sequence from the log
alone. stdlib only; no loop/guard wiring (default-off).
"""
import hashlib
import json
import os
from datetime import datetime, timezone

_ENTRY_KEYS = ("seq", "timestamp", "model_id",
               "prompt_hash", "decision", "detail")


def _utcnow_z():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_z(ts):
    try:
        if not isinstance(ts, str) or not ts.endswith("Z"):
            raise ValueError("timestamp must be an ISO-8601 UTC 'Z' string")
        return datetime.fromisoformat(ts[:-1] + "+00:00")
    except ValueError:
        raise ValueError("timestamp must be an ISO-8601 UTC 'Z' string: %r"
                         % (ts,))


def hash_prompt(prompt):
    """sha256 hex digest of a prompt string."""
    if isinstance(prompt, bool) or not isinstance(prompt, str):
        raise ValueError("prompt must be a string, got %r" % type(prompt))
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _check_hash(phash, position):
    if (isinstance(phash, bool) or not isinstance(phash, str)
            or len(phash) != 64):
        raise ValueError("log entry %d has bad prompt_hash" % position)
    try:
        int(phash, 16)
    except ValueError:
        raise ValueError("log entry %d has non-hex prompt_hash" % position)
    if phash != phash.lower():
        raise ValueError("log entry %d prompt_hash must be lower hex"
                         % position)
    if len(set(phash)) == 1:
        # A real sha256 digest is never 64 identical chars
        # (probability 16^-63): uniform digests are tamper-evident
        # against placeholder/fabricated hashes like "0"*64.
        raise ValueError("log entry %d has placeholder prompt_hash"
                         % position)


def _check_entry_shape(entry, position):
    if not isinstance(entry, dict):
        raise ValueError("log entry %d must be a dict, got %r"
                         % (position, type(entry)))
    missing = [k for k in _ENTRY_KEYS if k not in entry]
    if missing:
        raise ValueError("log entry %d missing keys: %s"
                         % (position, ",".join(missing)))
    seq = entry["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ValueError("log entry %d has non-int seq: %r" % (position, seq))
    if seq != position:
        raise ValueError("log entry %d has seq %r, expected %d"
                         % (position, seq, position))
    _parse_z(entry["timestamp"])
    model_id = entry["model_id"]
    if (isinstance(model_id, bool) or not isinstance(model_id, str)
            or not model_id.strip()):
        raise ValueError("log entry %d has bad model_id" % position)
    _check_hash(entry["prompt_hash"], position)
    decision = entry["decision"]
    if (isinstance(decision, bool) or not isinstance(decision, str)
            or not decision.strip()):
        raise ValueError("log entry %d has bad decision" % position)
    detail = entry["detail"]
    if detail is not None and not isinstance(detail, (dict, list, str)):
        raise ValueError("log entry %d has bad detail type %r"
                         % (position, type(detail)))


def log_decision(log, decision, model_id, prompt_hash, timestamp=None,
                 detail=None):
    """Append one validated entry to the log list; return the entry."""
    if not isinstance(log, list):
        raise ValueError("log must be a list, got %r" % type(log))
    if (isinstance(decision, bool) or not isinstance(decision, str)
            or not decision.strip()):
        raise ValueError("decision must be a non-blank string")
    if (isinstance(model_id, bool) or not isinstance(model_id, str)
            or not model_id.strip()):
        raise ValueError("model_id must be a non-blank string")
    try:
        _check_hash(prompt_hash, len(log))
    except ValueError as exc:
        raise ValueError("prompt_hash %s" % exc)
    if timestamp is None:
        timestamp = _utcnow_z()
    _parse_z(timestamp)
    if detail is not None and not isinstance(detail, (dict, list, str)):
        raise ValueError("detail must be a dict, list, string, or None")
    entry = {"seq": len(log), "timestamp": timestamp, "model_id": model_id,
             "prompt_hash": prompt_hash, "decision": decision,
             "detail": detail}
    log.append(entry)
    return entry


def replay_decisions(log):
    """Validate the trail and return the decision sequence verbatim."""
    if not isinstance(log, list):
        raise ValueError("log must be a list, got %r" % type(log))
    prev_ts = None
    for position, entry in enumerate(log):
        _check_entry_shape(entry, position)
        ts = datetime.fromisoformat(entry["timestamp"][:-1] + "+00:00")
        if prev_ts is not None and ts < prev_ts:
            raise ValueError("timestamp regression at entry %d" % position)
        prev_ts = ts
    return [entry["decision"] for entry in log]


def verify_trail(log):
    """Return {ok, n, reason} without raising on a broken trail."""
    try:
        decisions = replay_decisions(log)
    except ValueError as exc:
        return {"ok": False,
                "n": len(log) if isinstance(log, list) else 0,
                "reason": "INVALID: %s" % exc}
    return {"ok": True, "n": len(decisions), "reason": "OK: trail verifies"}


def trail_to_json(log):
    if not isinstance(log, list):
        raise ValueError("log must be a list, got %r" % type(log))
    return json.dumps(log, ensure_ascii=False)


def trail_from_json(text):
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError("trail_from_json: invalid JSON: %s" % exc)
    if not isinstance(parsed, list):
        raise ValueError("trail_from_json: top level must be a list")
    # Validate entries now: a trail that cannot replay is not a trail.
    replay_decisions(parsed)
    return parsed


class AuditTrail:
    """Stateful wrapper around a decision log list."""

    def __init__(self):
        self._log = []

    def log(self, decision, model_id, prompt_hash, timestamp=None,
            detail=None):
        return log_decision(self._log, decision, model_id, prompt_hash,
                            timestamp=timestamp, detail=detail)

    def decisions(self):
        return replay_decisions(self._log)

    def verify(self):
        return verify_trail(self._log)

    def __len__(self):
        return len(self._log)

    def to_json(self):
        return trail_to_json(self._log)

    @classmethod
    def from_json(cls, text):
        trail = cls()
        trail._log = trail_from_json(text)
        replay_decisions(trail._log)  # validate on load
        return trail

    def save(self, path):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.to_json())

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        return cls.from_json(text)
