#!/usr/bin/env python3
"""Task C5: Interactive repair (plan item 25, Wave C FIX BETTER).

``--interact`` pauses AFTER PATCH: the dev accepts/edits/rejects each
hunk, then the loop continues (resim/verify). Reject-all restores the
baseline (no hunk applied).

Interface::

  split_hunks(patch) -> [{"file","header","lines","raw","index",
                          "file_headers"}]
  review_patch(patch, decide) -> {"patch","decisions"}
  apply_patch(baseline, patch) -> new_files (baseline untouched)
  make_prompt_decide(input_fn, print_fn) -> decide_fn (the pause)
  interactive_review(baseline, patch, decide=None, input_fn=None,
                     print_fn=None)
    -> {"files","patch","decisions","outcome","restored"}

``baseline`` is a dict of ``path -> file content`` (strings). ``patch``
is a unified diff string (``diff --git`` + ``@@`` headers). ``decide``
is ``decide(hunk, index)`` returning one of ``"accept"`` (``"a"``) /
``"reject"`` (``"r"``) / ``("edit", new_body_text)`` /
``{"action": "edit", "hunk": new_body_text}`` /
``{"action": "accept"}`` / ``{"action": "reject"}``. A single-shot
prompt form ``"e:<replacement body>"`` is also accepted. Anything else
raises (refuses to guess the dev's intent).

``outcome`` is one of ``"accepted"`` (all accept), ``"edited"`` (any
edit), ``"partial"`` (some reject, none edit... more precisely: any
reject and no edit), ``"rejected_all"`` (every hunk rejected).
``restored`` is True iff ``outcome == "rejected_all"``: ``files`` is
then an exact copy of ``baseline`` and ``patch`` is ``""``.

``--interact`` wiring (NOT done here): ``agent/loop.py`` calls
``interactive_review(baseline_files, patch_text)`` with no ``decide``
when the flag is passed, so the real ``input``/``print`` pause runs;
tests inject ``decide`` or ``input_fn`` fixtures instead of a TTY.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``re`` only); no API, no keys, no network, no real I/O
unless the caller explicitly asks for the prompt pause (and even then
only through the injected ``input_fn``/``print_fn`` or, when neither
is given, the builtin ``input``/``print``).
"""

import re

ACCEPT = "accept"
REJECT = "reject"
EDIT = "edit"

OUTCOMES = ("accepted", "edited", "partial", "rejected_all")

_DIFF_GIT = re.compile(r"^\s*diff\s+--git\s+\S+\s+(\S+)\s*$")
_PLUSPLUS = re.compile(r"^\+\+\+\s+(\S+)")
_HUNK_HEADER = re.compile(
    r"@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")
_NO_NEWLINE = "No newline at end of file"


def _strip_prefix(path):
    """Strip a/ b/ prefixes from a diff path (b/rtl/uart.v -> rtl/uart.v)."""
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _hunk_record(path, header, lines, index, file_headers):
    """Build one hunk record dict."""
    return {
        "file": path,
        "header": header,
        "lines": list(lines),
        "raw": header + "\n" + "\n".join(lines) + "\n",
        "index": index,
        "file_headers": list(file_headers),
    }


def split_hunks(patch):
    """Split a unified diff into per-hunk records.

    Raises TypeError for a non-string patch, ValueError for an empty
    string or text that is not a unified diff (no ``diff --git`` /
    ``@@`` header). Hunk body lines must start with ``' '``/``'+'``/
    ``'-'`` (a bare ``""`` line is blank context); ``\\ No newline``
    markers are skipped.
    """
    if not isinstance(patch, str):
        raise TypeError(
            "interact: patch must be a string, got %r" % (patch,))
    if not patch.strip():
        raise ValueError(
            "interact: patch must be a non-empty unified diff string")
    if "diff --git" not in patch or "@@" not in patch:
        raise ValueError(
            "interact: patch is not a unified diff "
            "(no diff --git / @@ header), got %r" % (patch[:80],))
    hunks = []
    cur_file = None
    cur_headers = []
    cur_header = None
    cur_lines = []
    headers_by_file = {}
    raw_lines = patch.split("\n")
    if patch.endswith("\n"):
        # The split artifact after the final newline is a terminator,
        # not a blank context line. (Interior "" lines are still
        # blank-context, handled below.)
        raw_lines.pop()

    def _flush():
        if cur_header is not None:
            hunks.append(_hunk_record(
                cur_file, cur_header, cur_lines, len(hunks),
                headers_by_file.get(cur_file, cur_headers)))

    for raw_line in raw_lines:
        line = raw_line
        m_git = _DIFF_GIT.match(line)
        if m_git:
            _flush()
            cur_header = None
            cur_lines = []
            cur_file = _strip_prefix(m_git.group(1))
            cur_headers = [line]
            headers_by_file[cur_file] = cur_headers
            continue
        if line.startswith("--- ") or _PLUSPLUS.match(line):
            # Belongs to the current file section; not hunk body.
            cur_headers.append(line)
            if cur_file in headers_by_file:
                headers_by_file[cur_file] = cur_headers
            continue
        m_hunk = _HUNK_HEADER.search(line)
        if m_hunk and cur_file is not None:
            _flush()
            cur_header = line.strip()
            cur_lines = []
            continue
        if cur_header is not None:
            if line == "":
                cur_lines.append(" ")
                continue
            if _NO_NEWLINE in line:
                continue
            if line[0] not in (" ", "+", "-"):
                raise ValueError(
                    "interact: bad hunk body line %r in %s"
                    % (line, cur_header))
            cur_lines.append(line)
            continue
        if line.strip() in ("", "index", ) or line.startswith(
                ("index ", "new file", "old file", "new mode",
                 "old mode", "similarity", "rename ")):
            continue
        # Other metadata outside hunks is tolerated (blank/version
        # lines); only the hunk count below is enforced.
    _flush()
    if not hunks:
        raise ValueError(
            "interact: patch has no hunks (no @@ section), got %r"
            % (patch[:80],))
    return hunks


def _normalize_decision(res, index):
    """Normalize one decide() return into (action, replacement_or_None)."""
    if isinstance(res, str):
        text = res.strip()
        low = text.lower()
        if low in ("accept", "a"):
            return (ACCEPT, None)
        if low in ("reject", "r"):
            return (REJECT, None)
        if low in ("edit", "e"):
            raise ValueError(
                "interact: edit decision for hunk %d needs replacement "
                "hunk text (got bare %r)" % (index, res))
        for prefix in ("e:", "edit:"):
            if low.startswith(prefix):
                body = text[len(prefix):]
                return (EDIT, _require_edit_body(body, index))
        raise ValueError(
            "interact: unknown decision %r for hunk %d "
            "(want accept/edit/reject)" % (res, index))
    if isinstance(res, dict):
        action = res.get("action", "")
        if not isinstance(action, str):
            raise TypeError(
                "interact: decide dict action must be a string, "
                "got %r for hunk %d" % (action, index))
        low = action.strip().lower()
        if low in ("accept", "a"):
            return (ACCEPT, None)
        if low in ("reject", "r"):
            return (REJECT, None)
        if low in ("edit", "e"):
            return (EDIT, _require_edit_body(res.get("hunk"), index))
        raise ValueError(
            "interact: unknown decision %r for hunk %d "
            "(want accept/edit/reject)" % (res, index))
    if isinstance(res, (tuple, list)) and len(res) == 2:
        first, second = res
        if isinstance(first, str) and first.strip().lower() in ("edit", "e"):
            return (EDIT, _require_edit_body(second, index))
        raise ValueError(
            "interact: unknown decision %r for hunk %d "
            "(want accept/edit/reject)" % (res, index))
    raise TypeError(
        "interact: decide must return accept/reject/edit, "
        "got %r for hunk %d" % (res, index))


def _require_edit_body(body, index):
    """Validate replacement hunk body text; return body lines."""
    if not isinstance(body, str) or not body.strip():
        raise ValueError(
            "interact: edit decision for hunk %d needs non-empty "
            "replacement hunk text" % (index,))
    text = body.strip("\n")
    lines = [_normalize_body_line(ln) for ln in text.split("\n")]
    if not any(ln[:1] in ("+", "-") for ln in lines):
        raise ValueError(
            "interact: edit for hunk %d changes nothing "
            "(no +/- line in replacement)" % (index,))
    return lines


def _normalize_body_line(line):
    """Normalize one replacement body line (blank -> blank context)."""
    if line == "":
        return " "
    if line[0] not in (" ", "+", "-"):
        raise ValueError(
            "interact: bad replacement hunk body line %r "
            "(want ' '/'+'/'-' prefix)" % (line,))
    return line


def _reconstruct_patch(hunks, kept):
    """Rebuild a unified diff from kept (hunk, body_lines) pairs.

    Groups hunks by file (first-seen order), re-emitting each file's
    ``diff --git``/``---``/``+++`` headers. No kept hunk -> "".
    """
    if not kept:
        return ""
    order = []
    by_file = {}
    for hunk, body in kept:
        if hunk["file"] not in by_file:
            order.append(hunk["file"])
            by_file[hunk["file"]] = {
                "headers": hunk["file_headers"], "parts": []}
        by_file[hunk["file"]]["parts"].append(
            hunk["header"] + "\n" + "\n".join(body))
    sections = []
    for path in order:
        entry = by_file[path]
        sections.append(
            "\n".join(entry["headers"]) + "\n" +
            "\n".join(entry["parts"]))
    return "\n".join(sections) + "\n"


def review_patch(patch, decide):
    """Apply decide() per hunk; return {"patch","decisions"}.

    ``decide(hunk, index)`` sees a copy of the hunk record. Accepted
    hunks are kept verbatim, edited hunks keep the original ``@@``
    header with the replacement body, rejected hunks are dropped.
    Reject-all yields ``""``. Raises on a bad patch, a non-callable
    decide, or an unknown decision (never guesses).
    """
    if not callable(decide):
        raise TypeError(
            "interact: decide must be callable, got %r" % (decide,))
    hunks = split_hunks(patch)
    decisions = []
    kept = []
    for hunk in hunks:
        res = decide(dict(hunk, lines=list(hunk["lines"])),
                     hunk["index"])
        action, replacement = _normalize_decision(res, hunk["index"])
        entry = {"index": hunk["index"], "file": hunk["file"],
                 "header": hunk["header"], "decision": action}
        if action == EDIT:
            entry["replacement"] = "\n".join(replacement)
            kept.append((hunk, replacement))
        elif action == ACCEPT:
            kept.append((hunk, list(hunk["lines"])))
        decisions.append(entry)
    return {"patch": _reconstruct_patch(hunks, kept), "decisions": decisions}


def _require_baseline(baseline):
    """Validate the baseline dict; return it or raise."""
    if not isinstance(baseline, dict):
        raise TypeError(
            "interact: baseline must be a dict of path->content "
            "strings, got %r" % (baseline,))
    for path, content in baseline.items():
        if not isinstance(path, str) or not path:
            raise ValueError(
                "interact: baseline path must be a non-empty string, "
                "got %r" % (path,))
        if not isinstance(content, str):
            raise TypeError(
                "interact: baseline content for %r must be a string, "
                "got %r" % (path, content))
    return baseline


def apply_patch(baseline, patch):
    """Apply a unified diff to baseline; return a NEW files dict.

    Empty/whitespace patch is a no-op returning a copy. Files the
    patch touches must exist in baseline; every context/``-`` line must
    match (offset-tracked ``@@`` application). Mismatch raises loudly
    -- never fabricates applied content. Baseline is never mutated.
    """
    _require_baseline(baseline)
    if not isinstance(patch, str):
        raise TypeError(
            "interact: patch must be a string, got %r" % (patch,))
    if not patch.strip():
        return dict(baseline)
    hunks = split_hunks(patch)
    by_file = {}
    for hunk in hunks:
        by_file.setdefault(hunk["file"], []).append(hunk)
    files = {}
    for path, content in baseline.items():
        files[path] = content.split("\n")
    for path, file_hunks in by_file.items():
        if path not in files:
            raise ValueError(
                "interact: patch touches unknown file %r "
                "(not in baseline)" % (path,))
        working = files[path]
        offset = 0
        for hunk in file_hunks:
            m = _HUNK_HEADER.search(hunk["header"])
            if not m:
                raise ValueError(
                    "interact: bad @@ header %r" % (hunk["header"],))
            old_start = int(m.group(1))
            pos = old_start - 1 + offset
            if pos < 0:
                raise ValueError(
                    "interact: hunk %r starts before file top" % (
                        hunk["header"],))
            delta = 0
            for line in hunk["lines"]:
                kind = line[0] if line else " "
                text = line[1:] if line else ""
                if kind == " ":
                    if pos >= len(working) or working[pos] != text:
                        raise ValueError(
                            "interact: context mismatch at %s:%d: "
                            "want %r" % (path, pos + 1, text))
                    pos += 1
                elif kind == "-":
                    if pos >= len(working) or working[pos] != text:
                        raise ValueError(
                            "interact: removal mismatch at %s:%d: "
                            "want %r" % (path, pos + 1, text))
                    del working[pos]
                    delta -= 1
                else:  # "+"
                    working.insert(pos, text)
                    pos += 1
                    delta += 1
            offset += delta
    return {path: "\n".join(lines) for path, lines in files.items()}


def make_prompt_decide(input_fn, print_fn=None):
    """Build a decide() fn that pauses per hunk (the --interact pause).

    ``input_fn(prompt)`` returns the dev's reply string; ``print_fn``
    (or nothing) shows the hunk. Replies: ``a``/``accept``,
    ``r``/``reject``, ``e``/``edit`` (then replacement lines, one per
    ``input_fn`` call, terminated by a single ``.`` line), or
    single-shot ``e:<replacement body>``. Unknown/empty replies raise.
    """
    if not callable(input_fn):
        raise TypeError(
            "interact: input_fn must be callable, got %r" % (input_fn,))
    if print_fn is not None and not callable(print_fn):
        raise TypeError(
            "interact: print_fn must be callable or None, got %r"
            % (print_fn,))

    def _decide(hunk, index):
        if print_fn is not None:
            print_fn("Hunk %d (%s %s):\n%s"
                     % (index, hunk["file"], hunk["header"], hunk["raw"]))
        reply = input_fn(
            "Hunk %d [a]ccept / [e]dit / [r]eject? " % (index,))
        if not isinstance(reply, str):
            raise TypeError(
                "interact: dev reply must be a string, got %r" % (reply,))
        low = reply.strip().lower()
        if low in ("accept", "a"):
            return ACCEPT
        if low in ("reject", "r"):
            return REJECT
        if low.startswith("e:") or low.startswith("edit:"):
            body = reply.strip()[reply.strip().find(":") + 1:]
            return (EDIT, body)
        if low in ("edit", "e"):
            collected = []
            while True:
                line = input_fn(
                    "replacement line (end with single '.'): ")
                if not isinstance(line, str):
                    raise TypeError(
                        "interact: dev reply must be a string, got %r"
                        % (line,))
                if line.strip() == ".":
                    break
                collected.append(line)
            if not collected or not "\n".join(collected).strip():
                raise ValueError(
                    "interact: empty edit for hunk %d "
                    "(no replacement lines)" % (index,))
            return (EDIT, "\n".join(collected))
        raise ValueError(
            "interact: unknown reply %r for hunk %d "
            "(want accept/edit/reject)" % (reply, index))

    return _decide


def interactive_review(baseline, patch, decide=None, input_fn=None,
                       print_fn=None):
    """Review a patch hunk-by-hunk against baseline; return the verdict.

    Returns ``{"files","patch","decisions","outcome","restored"}``:
    ``files`` is the post-review tree (reject-all -> exact baseline
    copy), ``patch`` the reviewed diff (reject-all -> ``""``).
    ``decide`` and ``input_fn`` are mutually exclusive; with neither,
    the real ``input``/``print`` pause runs (the ``--interact`` path).
    """
    _require_baseline(baseline)
    if decide is not None and input_fn is not None:
        raise ValueError(
            "interact: pass decide or input_fn, not both "
            "(ambiguous reviewer)")
    if decide is None:
        decide = make_prompt_decide(
            input_fn if input_fn is not None else input, print_fn)
    elif not callable(decide):
        raise TypeError(
            "interact: decide must be callable, got %r" % (decide,))
    reviewed = review_patch(patch, decide)
    final = reviewed["patch"]
    decisions = reviewed["decisions"]
    if not final.strip():
        return {"files": dict(baseline), "patch": "",
                "decisions": decisions, "outcome": "rejected_all",
                "restored": True}
    files = apply_patch(baseline, final)
    kinds = set(d["decision"] for d in decisions)
    if EDIT in kinds:
        outcome = "edited"
    elif REJECT in kinds:
        outcome = "partial"
    else:
        outcome = "accepted"
    return {"files": files, "patch": final, "decisions": decisions,
            "outcome": outcome, "restored": False}
