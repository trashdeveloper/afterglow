#!/usr/bin/env python3
"""Evidence board (Task D5, plan item 35, Wave D).

Every claim in a report links to a bundle artifact (checksums, logs):
a report with an unlinked claim fails validation.

Interface::

  validate_board(board) -> list of claim ids (raises on unlinked)
  validate_report(report) -> list of claim ids (report dict w/ "claims")
  verify_board(board) -> {"ok", "n", "unlinked", "reason"} (never raises
    on a bad board; FileNotFoundError from load still propagates)
  board_to_json(board) / board_from_json(text)
  EvidenceBoard -> stateful board with add/validate/verify/to_json/
    from_json/save/load

A board is a list of claim dicts; a report is a dict with a "claims"
list (extra report keys such as ``report_id`` are ignored). Each
claim dict holds ``claim`` (non-blank text) and ``artifacts``
(non-empty list). An artifact entry is either a bundle-relative path
string or a ``{"path", "sha256"}`` dict carrying a checksum. Artifact
paths must be exactly one of :data:`BUNDLE_FILES` (the AGENTS.md
section 1 proof-bundle set); absolute paths, ``..`` traversal, and
unknown names are rejected. Checksums must be 64-char lower hex and
never a placeholder repeat such as ``"0" * 64``.

``validate_board`` returns the claim ids in order (claims without an
explicit ``id`` get ``"claim-<index>"``); duplicate ids raise. An
empty board validates to ``[]`` (vacuous: every one of zero claims is
linked).

Default-off contract (Wave D header: additive + default-off): this
module runs ONLY on explicit call. It is imported by nothing in
agent/loop.py, agent/guard.py, or eval/score.py -- those files are
untouched.

Pure stdlib core (json + os only for the save/load helpers): no
network calls and no child processes.
"""

import json
import os

_VERSION = "1"

#: The AGENTS.md section 1 proof-bundle set. Every claim must link to
#: at least one of these; anything else is not bundle evidence.
BUNDLE_FILES = (
    "artifacts/input_trace.txt",
    "artifacts/boot.log",
    "artifacts/boot_after.log",
    "artifacts/verdict_before.json",
    "artifacts/verdict_after.json",
    "api/text_request.json",
    "api/text_response.json",
    "api/image_request.json",
    "api/image_response.json",
    "explain.png",
    "patch.diff",
    "report.md",
    "meta.json",
)


def _check_sha(digest, where):
    """Return digest, or raise ValueError (64 lower hex, not uniform)."""
    if (isinstance(digest, bool) or not isinstance(digest, str)
            or len(digest) != 64):
        raise ValueError("%s has bad sha256 (want 64 hex chars)" % where)
    try:
        int(digest, 16)
    except ValueError:
        raise ValueError("%s has non-hex sha256" % where)
    if digest != digest.lower():
        raise ValueError("%s sha256 must be lower hex" % where)
    if len(set(digest)) == 1:
        raise ValueError("%s has placeholder sha256" % where)
    return digest


def _check_artifact_path(path, where):
    """Return stripped path, or raise ValueError."""
    if isinstance(path, bool) or not isinstance(path, str):
        raise ValueError("%s artifact must be a path string, got %r"
                         % (where, path))
    cleaned = path.strip()
    if not cleaned:
        raise ValueError("%s artifact path must be non-blank" % where)
    if cleaned.startswith("/") or ".." in cleaned.split("/"):
        raise ValueError("%s artifact must be bundle-relative, got %r"
                         % (where, path))
    if cleaned not in BUNDLE_FILES:
        raise ValueError("%s artifact %r is not a bundle file"
                         % (where, cleaned))
    return cleaned


def _check_artifact_entry(entry, where):
    """Normalize one artifact entry to a clean str or dict, or raise."""
    if isinstance(entry, dict):
        if "path" not in entry or "sha256" not in entry:
            raise ValueError(
                "%s artifact dict needs 'path' + 'sha256', got %r"
                % (where, entry))
        path = _check_artifact_path(entry["path"], where)
        digest = _check_sha(entry["sha256"], where)
        return {"path": path, "sha256": digest}
    return _check_artifact_path(entry, where)


def _check_claim_text(claim, where):
    if isinstance(claim, bool) or not isinstance(claim, str):
        raise ValueError("%s claim must be a string, got %r"
                         % (where, claim))
    if not claim.strip():
        raise ValueError("%s claim must be non-blank" % where)
    return claim


def _check_claim_entry(entry, position):
    """Return (claim_id_or_None, claim, artifacts); raise on junk."""
    if not isinstance(entry, dict):
        raise ValueError("claim %d must be a dict, got %r"
                         % (position, entry))
    raw_id = entry.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        where = "claim %r" % raw_id.strip()
    else:
        where = "claim %d" % position
    if "claim" not in entry:
        raise ValueError("%s missing 'claim' text" % where)
    if "artifacts" not in entry:
        raise ValueError("%s has no linked bundle artifact "
                         "(missing 'artifacts')" % where)
    claim = _check_claim_text(entry["claim"], where)
    raw = entry["artifacts"]
    if isinstance(raw, tuple):
        raw = list(raw)
    if not isinstance(raw, list) or not raw:
        raise ValueError("%s links no bundle artifact "
                         "(need >=1 entry)" % where)
    artifacts = [_check_artifact_entry(a, where) for a in raw]
    cid = entry.get("id")
    if cid is None:
        return None, claim, artifacts
    if isinstance(cid, bool) or not isinstance(cid, str) or not cid.strip():
        raise ValueError("%s has bad id %r" % (where, cid))
    return cid.strip(), claim, artifacts


def _extract_claims(board):
    """Return the claims list from a board list or a report dict."""
    if isinstance(board, list):
        return board
    if isinstance(board, dict) and isinstance(board.get("claims"), list):
        return board["claims"]
    raise ValueError("board must be a claims list or a report dict "
                     "with a 'claims' list, got %r" % (board,))


def validate_board(board):
    """Check every claim links to >=1 bundle artifact.

    Accepts a claims list or a report dict (``{"claims": [...]}``).
    Returns the claim ids in order; claims without an explicit id get
    ``"claim-<index>"``. Raises ValueError naming the offender when a
    claim is unlinked, malformed, duplicated, or points outside the
    bundle set.
    """
    claims = _extract_claims(board)
    ids = []
    seen = set()
    for position, entry in enumerate(claims):
        cid, _, _ = _check_claim_entry(entry, position)
        if cid is None:
            cid = "claim-%d" % position
        if cid in seen:
            raise ValueError("duplicate claim id %r" % cid)
        seen.add(cid)
        ids.append(cid)
    return ids


def validate_report(report):
    """Validate a report dict (must hold a ``claims`` list)."""
    if not isinstance(report, dict) or not isinstance(
            report.get("claims"), list):
        raise ValueError("report must be a dict with a 'claims' list, "
                         "got %r" % (report,))
    return validate_board(report["claims"])


def _linked_lenient(entry, position):
    """Best-effort linkage check for verify_board; True iff linked."""
    try:
        _, _, _ = _check_claim_entry(entry, position)
    except ValueError:
        return False
    return True


def verify_board(board):
    """Return {ok, n, unlinked, reason} without raising on a bad board."""
    try:
        claims = _extract_claims(board)
    except ValueError as exc:
        return {"ok": False, "n": 0, "unlinked": [],
                "reason": "INVALID: %s" % exc}
    unlinked = []
    for position, entry in enumerate(claims):
        if not _linked_lenient(entry, position):
            if isinstance(entry, dict):
                cid = entry.get("id")
                if isinstance(cid, str) and cid.strip():
                    unlinked.append(cid.strip())
                else:
                    unlinked.append("claim-%d" % position)
            else:
                unlinked.append("claim-%d" % position)
    if unlinked:
        return {"ok": False, "n": len(claims), "unlinked": unlinked,
                "reason": "INVALID: %d unlinked claim(s): %s"
                          % (len(unlinked), ",".join(unlinked))}
    try:
        ids = validate_board(claims)
    except ValueError as exc:
        return {"ok": False, "n": len(claims), "unlinked": unlinked,
                "reason": "INVALID: %s" % exc}
    return {"ok": True, "n": len(ids), "unlinked": [],
            "reason": "OK: all %d claim(s) link to bundle artifacts"
                      % len(ids)}


def board_to_json(board):
    """Validate the board and serialize it to a JSON string."""
    validate_board(board)
    if isinstance(board, dict):
        return json.dumps(board, ensure_ascii=False)
    return json.dumps(list(board), ensure_ascii=False)


def board_from_json(text):
    """Parse JSON into a board list (or report dict); validate it."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError("board_from_json: invalid JSON: %s" % exc)
    if isinstance(parsed, dict):
        validate_report(parsed)
        return parsed
    if isinstance(parsed, list):
        validate_board(parsed)
        return parsed
    raise ValueError("board_from_json: top level must be a claims list "
                     "or a report dict")


class EvidenceBoard:
    """Stateful evidence board: every claim links to bundle artifacts."""

    def __init__(self):
        self._claims = []

    def add(self, claim, artifacts, claim_id=None):
        """Append one linked claim; return the stored entry."""
        if isinstance(artifacts, tuple):
            artifacts = list(artifacts)
        entry = {"id": claim_id, "claim": claim, "artifacts": artifacts}
        if claim_id is None:
            entry = {"claim": claim, "artifacts": artifacts,
                     "id": "claim-%d" % len(self._claims)}
        cid, text, arts = _check_claim_entry(entry, len(self._claims))
        if cid in (c.get("id") for c in self._claims):
            raise ValueError("duplicate claim id %r" % cid)
        stored = {"id": cid, "claim": text, "artifacts": arts}
        self._claims.append(stored)
        return stored

    def claims(self):
        """Return a copy of the claim entries."""
        return [dict(c) for c in self._claims]

    def validate(self):
        """Full validation; returns claim ids in order."""
        return validate_board(self._claims)

    def verify(self):
        """Non-raising rollup; see :func:`verify_board`."""
        return verify_board(self._claims)

    def __len__(self):
        return len(self._claims)

    def to_json(self):
        """Serialize the board to JSON (validated first)."""
        return board_to_json(self._claims)

    @classmethod
    def from_json(cls, text):
        """Parse JSON into a board; invalid boards raise ValueError."""
        board = cls()
        parsed = board_from_json(text)
        claims = parsed["claims"] if isinstance(parsed, dict) else parsed
        for entry in claims:
            board._claims.append(entry)
        validate_board(board._claims)  # validate on load
        return board

    def save(self, path):
        """Write the validated board JSON to path (makedirs as needed)."""
        text = self.to_json()
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    @classmethod
    def load(cls, path):
        """Load board JSON from path; invalid boards raise ValueError."""
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        return cls.from_json(text)
