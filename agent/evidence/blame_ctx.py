#!/usr/bin/env python3
"""Task B3: Git-blame context (plan item 13, Wave B SEE MORE).

EvidencePack extension: for fault lines (file + line list passed by
the caller), reports the last-commit hash + subject touching each
line via repo-local git
(`git blame -L <line>,<line> --porcelain -- <file>`).

Interfaces:
  blame_line(repo_root, rel_path, lineno, runner) -> entry or None
  blame_lines(repo_root, rel_path, lines, runner) -> {lineno: entry/None}
  format_prompt_section(results) -> str (cited, "" if none)
  blame_for_prompt(faults, repo_root, enable_blame, runner) -> pack dict

The runner is an injected callable `runner(cmd, cwd)` returning an
object with `.returncode`, `.stdout` (str), `.stderr` (str). The
default runner shells out to the repo-local `git` via subprocess;
tests pass a fake runner (canned porcelain, no subprocess) or point
the default runner at a FIXTURE tmp git repo with pinned commits --
never at the real repo.

Default-off contract (Wave B header: every addition is additive +
default-off): blame_for_prompt with enable_blame=False (the
default) returns {"blame_enabled": False, "blame": [], ...}
WITHOUT spawning git or reading anything. The loop
(agent/loop.py) is NOT edited by this task; prompt builders opt in
by passing enable_blame=True with caller-built fault lines.

Honesty contract (mirrors vcd_slice.py: never invent evidence):
- repo_root that is not a git checkout raises ValueError loudly
  (detected via `git rev-parse --git-dir` through the runner).
- A line git cannot attribute (out of range, uncommitted zeros
  hash, missing path) yields None for THAT line -- never a
  guessed hash. Format skips None entries; all-None renders "".
- Every reported hash comes verbatim from git's porcelain first
  field; every subject from git's `summary ` line. Unparseable
  porcelain yields None, not a guess.

Pure stdlib (os, re, subprocess); no API, no keys, no network.
"""
import os
import re
import subprocess

_DEFAULT_SUBJECT = "(no subject)"

_ZERO_RE = re.compile(r"^0+$")
_SUMMARY_RE = re.compile(r"^summary (.*)$")


def _default_runner(cmd, cwd):
    """Run a git command; returns CompletedProcess (str stdout/stderr)."""
    return subprocess.run(
        list(cmd), cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)


def _ensure_git_repo(repo_root, runner):
    """Raise ValueError unless repo_root is a git checkout (via runner)."""
    if not isinstance(repo_root, str) or not repo_root:
        raise ValueError("repo_root must be a non-empty path, got %r"
                         % (repo_root,))
    if not os.path.isdir(repo_root):
        raise ValueError("repo_root is not a directory: %r" % (repo_root,))
    proc = runner(["git", "rev-parse", "--git-dir"], repo_root)
    if proc.returncode != 0:
        raise ValueError(
            "not a git repo (git rev-parse --git-dir failed in %r): %s -- "
            "refusing to invent blame" % (repo_root,
                                           (proc.stderr or "").strip()))


def _parse_blame_porcelain(output):
    """Parse `git blame --porcelain` for a single line.

    Returns (commit_hash, subject) or (None, None) when the output
    is empty, all-zeros (uncommitted), or otherwise unparseable.
    """
    if not isinstance(output, str) or not output.strip():
        return None, None
    lines = output.splitlines()
    head = lines[0].split()
    if not head:
        return None, None
    commit = head[0]
    if _ZERO_RE.match(commit):
        return None, None  # uncommitted content: no commit to cite
    if not re.match(r"^[0-9a-f]{5,64}$", commit):
        return None, None
    subject = None
    for ln in lines[1:]:
        m = _SUMMARY_RE.match(ln)
        if m:
            subject = m.group(1)
            break
        if ln.startswith("\t"):
            break
    if subject is None:
        subject = _DEFAULT_SUBJECT
    return commit, subject


def blame_line(repo_root, rel_path, lineno, runner=None):
    """Blame one 1-based line; entry dict or None (never a guess).

    Entry: {"file", "line", "commit", "subject"}. Raises ValueError
    for a non-git repo_root or a non-positive lineno.
    """
    runner = runner or _default_runner
    if not isinstance(lineno, int) or lineno <= 0:
        raise ValueError("lineno must be a positive int, got %r"
                         % (lineno,))
    if not isinstance(rel_path, str) or not rel_path:
        raise ValueError("rel_path must be a non-empty path, got %r"
                         % (rel_path,))
    _ensure_git_repo(repo_root, runner)
    proc = runner(["git", "blame", "-L", "%d,%d" % (lineno, lineno),
                   "--porcelain", "--", rel_path], repo_root)
    if proc.returncode != 0:
        return None  # out of range / missing path: loud None, not a hash
    commit, subject = _parse_blame_porcelain(proc.stdout or "")
    if commit is None:
        return None
    return {"file": rel_path, "line": lineno,
            "commit": commit, "subject": subject}


def blame_lines(repo_root, rel_path, lines, runner=None):
    """Blame each 1-based line; {lineno: entry-or-None}.

    One `git blame -L n,n --porcelain` call per line (fault lists
    are short; grouping would blur per-line attribution). A line
    git cannot attribute maps to None. Empty `lines` returns {}.
    """
    runner = runner or _default_runner
    if lines is None:
        raise ValueError("lines must be a list of positive ints, "
                         "got None")
    items = list(lines)
    for n in items:
        if not isinstance(n, int) or n <= 0:
            raise ValueError("line numbers must be positive ints, "
                             "got %r" % (n,))
    _ensure_git_repo(repo_root, runner)
    return {n: blame_line(repo_root, rel_path, n, runner=runner)
            for n in items}


def format_prompt_section(results, max_chars=None):
    """Render blame entries as a cited prompt section, "" when empty.

    Entries with commit None are skipped (no attribution, no line).
    Every hash/subject is verbatim git output, never rewritten.
    """
    hits = [r for r in (results or []) if r and r.get("commit")]
    if not hits:
        return ""
    parts = ["Git-blame context (last commit touching each fault line):"]
    for hit in hits:
        parts.append("%s:%s last touched by %s -- %s"
                     % (hit.get("file", "?"), hit.get("line", "?"),
                        hit["commit"], hit.get("subject", "")))
    text = "\n".join(parts)
    if max_chars is not None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive, got %r"
                             % (max_chars,))
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + " [...]"
    return text


def _coerce_fault(fault):
    """Accept {"file", "line"} dicts or (file, line) tuples."""
    if isinstance(fault, dict):
        return fault.get("file"), fault.get("line")
    if isinstance(fault, (tuple, list)) and len(fault) == 2:
        return fault[0], fault[1]
    raise ValueError("fault must be {'file', 'line'} or (file, line), "
                     "got %r" % (fault,))


def blame_for_prompt(faults=None, repo_root=None, enable_blame=False,
                     runner=None):
    """EvidencePack section dict with a default-off blame section.

    enable_blame=False (default): returns {"blame_enabled": False,
    "blame": [], "prompt_section": ""} WITHOUT spawning git --
    existing loop/prompt behavior is byte-identical.
    enable_blame=True: requires a non-empty caller-built faults
    list (ValueError otherwise); blames each fault line in
    repo_root and renders the cited section.
    """
    pack = {"blame_enabled": bool(enable_blame), "blame": [],
            "prompt_section": ""}
    if not enable_blame:
        return pack
    if not faults:
        raise ValueError("enable_blame=True needs a non-empty faults "
                         "list ([{'file', 'line'}]): refusing to blame "
                         "nothing")
    runner = runner or _default_runner
    _ensure_git_repo(repo_root, runner)
    entries = []
    for fault in faults:
        rel_path, lineno = _coerce_fault(fault)
        entries.append(blame_line(repo_root, rel_path, lineno,
                                  runner=runner))
    hits = [e for e in entries if e is not None]
    pack["blame"] = hits
    pack["prompt_section"] = format_prompt_section(hits)
    return pack
