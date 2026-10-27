"""Afterglow patch guardrails (AGENTS.md section 2).

Serves eval cases: all 8 (locked + stress). Every model-produced patch
must pass :func:`check` before it is applied to the RTL.

A patch is ACCEPTED only if ALL of the following hold:

(a) it touches allowlisted RTL files ONLY (``agent/allowlist.txt``,
    compared by basename so ``rtl/simpleuart.v`` matches ``simpleuart.v``),
(b) it changes at most 40 +/- lines (``git diff --numstat`` style sum,
    header lines ``+++``/``---`` excluded),
(c) every touched RTL file still lints: exists, non-empty, no NUL
    bytes, balanced ``module``/``endmodule`` for HDL files
    (``.v``/``.sv``/``.vh``/``.svh``), compilable syntax for ``.py``,
(d) it is a real, non-empty unified diff: has ``---`` and
    ``+++ b/<path>`` headers plus at least one ``@@`` hunk, no binary
    content, and at least one substantive (non-whitespace-only) change,
(e) it contains no forbidden strings (``tohost`` force-pass, ``PASS``
    force, checksum hardcode, test-gated pass) and touches neither
    ``eval/`` nor ``EXPECTED.md``.

Retry budget (max 2 retries per case, then RED) is enforced by the loop
caller, not here: :func:`check` is a pure predicate so each rejected
diff can be logged into ``runs/`` as trust evidence (AGENTS.md requires
at least one REJECTED bad patch on record).

CLI: ``python3 agent/guard.py patch.diff [--allowlist X] [--rtl-root R]``
exits 0 on ACCEPT, 1 on REJECT.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

MAX_PATCH_LINES = 40

FORBIDDEN_PATTERNS = (
    r"tohost.*=.*1",
    r"PASS.*force",
    r"checksum.*hardcode",
    r"if.*test.*pass",
)

_PLUS_HEADER_RE = re.compile(r"^\+\+\+\s+(?:b/)?(.+?)\s*$")
_HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")
_HDL_EXTS = (".v", ".sv", ".vh", ".svh")


def load_allowlist(path: Optional[str] = None) -> List[str]:
    """Return allowlisted RTL basenames, one per line.

    Skips blank lines and ``#`` comments. Fail-closed: returns ``[]``
    when no allowlist file is found, which makes :func:`check` reject
    every file.
    """
    candidates: List[str] = []
    if path is not None:
        candidates.append(path)
    else:
        candidates.append(os.path.join(os.getcwd(), "agent", "allowlist.txt"))
        candidates.append(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "allowlist.txt")
        )
    for cand in candidates:
        if os.path.isfile(cand):
            with open(cand, "r", encoding="utf-8") as fh:
                return [
                    ln.strip()
                    for ln in fh
                    if ln.strip() and not ln.strip().startswith("#")
                ]
    return []


def diff_files(diff_text: str) -> List[str]:
    """Extract target file paths from ``+++ b/<path>`` headers, in order."""
    files: List[str] = []
    for line in diff_text.splitlines():
        m = _PLUS_HEADER_RE.match(line)
        if m:
            target = m.group(1).strip().strip('"')
            if target == "/dev/null":
                continue
            files.append(target)
    seen = set()
    ordered: List[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered


def count_patch_lines(diff_text: str) -> int:
    """Count +/- content lines, excluding ``+++``/``---`` headers."""
    n = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            n += 1
    return n


def is_whitespace_only(diff_text: str) -> bool:
    """True when no +/- line carries non-whitespace content.

    Returns True for an empty diff as well (nothing substantive); the
    empty case is additionally reported by :func:`check`.
    """
    found = False
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            found = True
            if line[1:].strip():
                return False
    return True


def lint_rtl(path: str) -> Tuple[bool, List[str]]:
    """Lint a single touched file. Returns (ok, reasons)."""
    if not os.path.isfile(path):
        return False, [f"RTL file not found: {path}"]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return False, [f"cannot read {path}: {exc}"]
    if len(raw) == 0:
        return False, [f"RTL file empty: {path}"]
    if b"\x00" in raw:
        return False, [f"null bytes in {path} (binary?)"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, [f"{path} is not valid UTF-8"]
    ext = os.path.splitext(path)[1].lower()
    if ext in _HDL_EXTS:
        # Count declarations at line starts only: bare-word matching counts
        # words inside comments (e.g. "// ... extra module" in picorv32.v).
        n_mod = len(re.findall(r"(?m)^\s*module\b", text))
        n_end = len(re.findall(r"(?m)^\s*endmodule\b", text))
        if n_mod == 0:
            return False, [f"{path}: no `module` block found"]
        if n_mod != n_end:
            return False, [
                f"{path}: unbalanced module/endmodule ({n_mod} vs {n_end})"
            ]
    elif ext == ".py":
        try:
            compile(text, path, "exec")
        except SyntaxError as exc:
            return False, [f"{path}: python syntax error: {exc}"]
    return True, []


def check(
    diff_text: str,
    allowlist: Optional[Sequence[str]] = None,
    rtl_root: str = ".",
) -> Tuple[bool, List[str], Dict]:
    """Validate a unified diff. Returns (ok, reasons, info).

    ``allowlist``: None loads ``agent/allowlist.txt``; a string is
    treated as an allowlist file path; otherwise a sequence of names
    (basenames are compared). ``rtl_root`` is the directory the touched
    RTL paths are resolved against.
    """
    reasons: List[str] = []
    info: Dict = {
        "files": [],
        "patch_lines": 0,
        "added": 0,
        "removed": 0,
        "allowlist": [],
        "lint": {},
    }

    if allowlist is None:
        allowed = load_allowlist()
    elif isinstance(allowlist, str):
        allowed = load_allowlist(allowlist)
    else:
        allowed = list(allowlist)
    allowed_bases = {os.path.basename(a.strip()) for a in allowed if a.strip()}
    info["allowlist"] = sorted(allowed_bases)

    if not diff_text or not diff_text.strip():
        return False, ["empty diff: not a real patch"], info

    lines = diff_text.splitlines()
    if any(
        l.startswith("GIT binary patch") or l.startswith("Binary files ")
        for l in lines
    ):
        reasons.append("binary diff not allowed")

    files = diff_files(diff_text)
    info["files"] = files
    if not files:
        reasons.append("not a unified diff: missing +++ b/<path> headers")
    if not any(l.startswith("---") for l in lines):
        reasons.append("not a unified diff: missing --- header")
    if not any(_HUNK_RE.match(l) for l in lines):
        reasons.append("not a unified diff: missing @@ hunk header")

    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    info["added"] = added
    info["removed"] = removed
    n = added + removed
    info["patch_lines"] = n
    if n == 0:
        reasons.append("no changed +/- lines")
    if n > MAX_PATCH_LINES:
        reasons.append(f"too many changed lines: {n} > {MAX_PATCH_LINES}")

    if files and not allowed_bases:
        reasons.append("allowlist empty/missing: refusing all files (fail closed)")
    for f in files:
        norm = f.replace(os.sep, "/")
        if os.path.basename(f) not in allowed_bases:
            reasons.append(f"file not allowlisted: {f}")
        if norm.startswith("eval/") or "/eval/" in norm:
            reasons.append(f"forbidden target (eval/ must never be patched): {f}")
        if norm == "EXPECTED.md" or norm.endswith("/EXPECTED.md"):
            reasons.append(f"forbidden target (EXPECTED.md must never be patched): {f}")

    if n and is_whitespace_only(diff_text):
        reasons.append("whitespace-only change: no substantive fix")

    payload = "\n".join(
        l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")
    )
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, payload, re.IGNORECASE):
            reasons.append(f"forbidden pattern in patch: {pat}")

    lint_results: Dict[str, bool] = {}
    for f in files:
        candidates = [
            os.path.join(rtl_root, f),
            os.path.join(rtl_root, os.path.basename(f)),
        ]
        target = next((c for c in candidates if os.path.isfile(c)), candidates[0])
        ok, why = lint_rtl(target)
        lint_results[f] = ok
        if not ok:
            reasons.extend(why)
    info["lint"] = lint_results

    return (len(reasons) == 0), reasons, info


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Afterglow patch guard (AGENTS.md §2).")
    ap.add_argument("diff", help="unified diff file to validate")
    ap.add_argument("--allowlist", default=None, help="allowlist file path")
    ap.add_argument("--rtl-root", default=".", help="RTL root for lint checks")
    args = ap.parse_args(argv)
    try:
        with open(args.diff, "r", encoding="utf-8") as fh:
            diff_text = fh.read()
    except OSError as exc:
        print(f"REJECT: cannot read diff: {exc}")
        return 1
    ok, reasons, info = check(diff_text, args.allowlist, args.rtl_root)
    print(f"files={info['files']} patch_lines={info['patch_lines']} "
          f"(+{info['added']}/-{info['removed']}) lint={info['lint']}")
    if ok:
        print("ACCEPT")
        return 0
    print("REJECT:")
    for r in reasons:
        print(f"  - {r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
