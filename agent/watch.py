#!/usr/bin/env python3
"""`afterglow watch`: capture a user's sim command into a draft CaseSpec.

Serves every case generically: runs the user's own sim command, captures
exit code + full log + argv, drafts ``sim/targets/<name>.yaml`` (RTL
files from ``.v``/``.sv`` argv entries, oracle markers from the
strongest FAIL-like log line with an exit-code fallback when the log
carries no markers), prints the draft for user confirm (``--yes`` skips),
writes the YAML, then chains into the existing triage path
(``agent.loop.main --spec <yaml> --triage``).

No model calls here, no live API: the draft is heuristic +
user-confirmed plumbing. Model diagnosis happens in the chained triage
run. Placeholders the draft cannot know (top/tb/anchor/bug) are loud
``WATCH_FILL_ME_*`` markers -- the confirm step shows them, and the
chained triage fails loudly (never silently) until the user fills them.

Usage:
  afterglow watch [--name ID] [--project P] [--seed N] [--timeout S]
                  [--yes] [--force] -- <cmd> [args ...]

Options must precede ``--``; everything after ``--`` is the user's sim
command verbatim (use ``--`` so leading-dash sim flags are never eaten
by this parser).
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(AGENT_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent import loop  # noqa: E402

WATCH_VERSION = "1.0.0"

# Strongest FAIL-like marker first: the first pattern (in this order)
# found anywhere in the captured log picks the oracle line.
FAIL_PATTERNS = (
    "tohost: FAIL",
    "TEST FAILED",
    "FAILED",
    "FAIL",
    "MISMATCH",
    "mismatch",
    "Assertion",
    "ASSERT",
    "ERROR",
    "Error",
    "TRAP:",
    "TIMEOUT",
    "Timeout",
)

PLACEHOLDER_TOP = "WATCH_FILL_ME_TOP"
PLACEHOLDER_TB = "WATCH_FILL_ME_TB.sv"
PLACEHOLDER_ANCHOR = "WATCH_FILL_ME_ANCHOR"
PLACEHOLDER_BUG = "WATCH_FILL_ME_BUG"
PLACEHOLDER_FILE = "WATCH_FILL_ME.v"

DEFAULT_ORACLE_PASS = "tohost: PASS"
DEFAULT_ORACLE_TRAP = "TRAP:"
DEFAULT_PROJECT = "picorv32"
DEFAULT_TIMEOUT_S = 600

_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PREFIX_RE = re.compile(
    r"^(\s*cycle\s+\d+\s*:|\s*\[\s*\d+[^\]]*\]\s*|\s*@\s*\d+\s*:?)\s*")


class WatchError(RuntimeError):
    """Fatal watch-plumbing failure (spawn, timeout, bad name, refusal)."""


def extract_rtl_files(argv: list) -> list:
    """RTL file guesses from the user's sim argv (pure, no I/O).

    Every token (or ``key=value`` suffix, or comma-separated part)
    ending in ``.v``/``.sv`` is kept. Relative paths stay tree-relative
    (``rtl/uart.v``); absolute paths collapse to basenames. Order kept,
    duplicates dropped. Non-HDL tokens are ignored.
    """
    files: list = []
    for tok in argv:
        text = str(tok).strip().strip("\"'")
        if "=" in text:
            text = text.rsplit("=", 1)[1].strip().strip("\"'")
        parts = text.split(",") if "," in text else [text]
        for part in parts:
            part = part.strip()
            if not (part.endswith(".v") or part.endswith(".sv")):
                continue
            rel = (os.path.basename(part) if os.path.isabs(part)
                   else os.path.normpath(part))
            if rel and rel not in files:
                files.append(rel)
    return files


def _derive_pass_marker(fail_line: str) -> str:
    """Guess the PASS counterpart of a FAIL-like line (pure).

    ``tohost:`` lines map to the canonical ``tohost: PASS``; FAIL/MATCH
    tokens are swapped in place; anything else falls back to the
    canonical default. Leading ``cycle N:``/``[N]``/``@N`` prefixes are
    stripped first so the marker can plausibly recur in a passing log.
    Truncated to 120 chars. Always non-blank.
    """
    line = _PREFIX_RE.sub("", fail_line.strip()).strip()
    if "tohost:" in line:
        return DEFAULT_ORACLE_PASS
    for bad, good in (("FAIL", "PASS"), ("MISMATCH", "MATCH"),
                      ("mismatch", "match")):
        if bad in line:
            return line.replace(bad, good, 1)[:120].strip() \
                or DEFAULT_ORACLE_PASS
    return DEFAULT_ORACLE_PASS


def pick_markers(log_text: str, exit_code: int) -> dict:
    """Oracle markers from the captured log (pure, no I/O).

    Returns ``{fail_line, oracle_pass, oracle_trap, oracle_source}``.
    The strongest FAIL-like pattern present picks ``fail_line``; with
    no FAIL-like line at all the exit code is the fallback source and
    ``oracle_pass`` is the canonical default (non-blank, fail closed --
    the draft stays RED until a real pass marker is confirmed).
    """
    lines = log_text.splitlines()
    fail_line = None
    hit = None
    for pat in FAIL_PATTERNS:
        for ln in lines:
            if pat in ln:
                fail_line = ln.strip()
                hit = pat
                break
        if fail_line is not None:
            break
    if fail_line is not None:
        oracle_pass = _derive_pass_marker(fail_line)
        source = "marker:%s" % hit
    else:
        oracle_pass = DEFAULT_ORACLE_PASS
        source = "exit-code-fallback:exit=%d (no FAIL-like marker)" \
            % (exit_code,)
    return {"fail_line": fail_line, "oracle_pass": oracle_pass,
            "oracle_trap": DEFAULT_ORACLE_TRAP, "oracle_source": source}


def default_name(argv: list) -> str:
    """Spec-stem guess from the sim command (pure): sanitized basename
    of ``argv[0]`` without extension, ``watch-case`` when empty."""
    base = os.path.basename(str(argv[0])) if argv else ""
    stem = os.path.splitext(base)[0].strip()
    name = _NAME_RE.sub("-", stem).strip(".-")
    return name or "watch-case"


def sanitize_name(name: str) -> str:
    """Filename-safe spec stem (pure): no separators, no traversal."""
    clean = _NAME_RE.sub("-", str(name)).strip(".-")
    if not clean:
        raise WatchError("bad --name %r: empty after sanitizing" % (name,))
    return clean


def run_cmd(cmd: list, timeout: int = DEFAULT_TIMEOUT_S) -> tuple:
    """Run the user's sim command, capture exit code + full log.

    stdout+stderr merged (one full log, like the board backend).
    Spawn failures and timeouts raise WatchError loudly -- no partial
    draft is ever produced from an unknown exit.
    """
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise WatchError("cannot run %r: %s" % (cmd[0] if cmd else "?", exc))
    except OSError as exc:
        raise WatchError("cannot run %r: %s" % (cmd[0] if cmd else "?", exc))
    except subprocess.TimeoutExpired:
        raise WatchError("command timed out after %ds (exit unknown): "
                         "no spec drafted" % (timeout,))
    return proc.returncode, proc.stdout


def draft_spec(name: str, argv: list, log_text: str, exit_code: int,
               project: str = DEFAULT_PROJECT) -> dict:
    """Draft case dict: derived files + markers, loud placeholders else."""
    files = extract_rtl_files(argv)
    markers = pick_markers(log_text, exit_code)
    if not files:
        files = [PLACEHOLDER_FILE]
    hit = markers["oracle_source"]
    gate = "watch-captured exit=%d %s" % (
        exit_code, hit.replace("marker:", "").strip()
        if hit.startswith("marker:") else "no-marker")
    return {"name": name, "project": project, "files": files,
            "top": PLACEHOLDER_TOP, "tb": PLACEHOLDER_TB,
            "anchor": PLACEHOLDER_ANCHOR, "bug": PLACEHOLDER_BUG,
            "gate": gate, "oracle_pass": markers["oracle_pass"],
            "oracle_trap": markers["oracle_trap"], "vflags": [],
            "_provenance": {
                "argv": list(argv),
                "exit_code": exit_code,
                "log_lines": len(log_text.splitlines()),
                "log_bytes": len(log_text.encode("utf-8")),
                "fail_line": markers["fail_line"],
                "oracle_source": markers["oracle_source"],
            }}


def _quote(value) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % text


def render_yaml(draft: dict) -> str:
    """Render the draft as flat-case YAML (stdlib only).

    Provenance rides in ``#`` comments (both flat parsers skip them);
    keys stay flat so ``load_case_file`` parses the file verbatim.
    """
    prov = draft.get("_provenance", {})
    lines = [
        "# afterglow watch draft %s -- heuristic + user-confirmed, "
        "NOT a model diagnosis." % draft.get("name", "?"),
        "# command: %s" % shlex.join(
            [str(a) for a in prov.get("argv", [])]),
        "# exit: %s  log_lines: %s  log_bytes: %s" % (
            prov.get("exit_code", "?"), prov.get("log_lines", "?"),
            prov.get("log_bytes", "?")),
        "# fail_marker: %s" % (prov.get("fail_line") or "none"),
        "# oracle_source: %s" % prov.get("oracle_source", "?"),
        "# NOTE: top/tb/anchor/bug are WATCH_FILL_ME placeholders -- "
        "edit before trusting triage.",
    ]
    for key in ("project", "top", "tb", "anchor", "bug", "gate",
                "oracle_pass", "oracle_trap"):
        lines.append("%s: %s" % (key, _quote(draft.get(key, ""))))
    files = ", ".join(_quote(f) for f in draft.get("files", []))
    lines.append("files: [%s]" % files)
    vflags = draft.get("vflags", [])
    lines.append("vflags: [%s]" % ", ".join(_quote(f) for f in vflags))
    return "\n".join(lines) + "\n"


def write_spec(root: str, name: str, text: str,
               force: bool = False) -> str:
    """Write ``sim/targets/<name>.yaml`` under root (fail closed).

    Refuses to overwrite an existing stem without ``--force``; the
    sanitized name can never escape the targets dir.
    """
    if os.path.basename(name) != name or not name:
        raise WatchError("bad spec name %r (must be a bare stem)" % (name,))
    targets = os.path.join(str(root), "sim", "targets")
    os.makedirs(targets, exist_ok=True)
    path = os.path.join(targets, name + ".yaml")
    if os.path.exists(path) and not force:
        raise WatchError("spec %s exists: pass --force to overwrite, "
                         "or pick another --name" % (path,))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def chain_to_triage(spec_path: str, seed: int = 42) -> int:
    """Hand off to the existing triage path (no loop.py changes)."""
    return loop.main(["--spec", spec_path, "--triage",
                      "--seed", str(seed)])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="afterglow watch",
        description="Run a sim command, draft sim/targets/<name>.yaml "
                    "from its log, confirm, then chain into triage "
                    "(heuristic draft + user confirm; no model calls here).")
    ap.add_argument("--version", action="version",
                    version="afterglow-watch %s" % WATCH_VERSION)
    ap.add_argument("--name", default=None,
                    help="spec stem for sim/targets/<name>.yaml "
                         "(default: sanitized argv[0] basename)")
    ap.add_argument("--project", default=DEFAULT_PROJECT,
                    help="pinned project for the chained triage run "
                         "(must be in targets.pin; default: %s)"
                         % DEFAULT_PROJECT)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                    help="sim command timeout in seconds (default: %d)"
                         % DEFAULT_TIMEOUT_S)
    ap.add_argument("--yes", action="store_true", default=False,
                    help="skip the confirm prompt (print draft, write, "
                         "chain)")
    ap.add_argument("--force", action="store_true", default=False,
                    help="overwrite an existing sim/targets/<name>.yaml")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="sim command after --, e.g. -- ./obj_dir/Vtb "
                         "+seed=1 (options must precede --)")
    return ap


def main(argv=None, root: str = ROOT) -> int:
    args = build_parser().parse_args(argv)
    cmd = list(args.cmd or [])
    while cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("FATAL: need a sim command: "
              "afterglow watch [--yes] -- <cmd> [args ...]",
              file=sys.stderr)
        return 2
    try:
        name = sanitize_name(args.name) if args.name \
            else sanitize_name(default_name(cmd))
    except WatchError as exc:
        print("FATAL: %s" % (exc,), file=sys.stderr)
        return 2
    try:
        exit_code, log_text = run_cmd(cmd, timeout=args.timeout)
    except WatchError as exc:
        print("FATAL: %s" % (exc,), file=sys.stderr)
        return 1
    draft = draft_spec(name, cmd, log_text, exit_code,
                       project=args.project)
    text = render_yaml(draft)
    prov = draft["_provenance"]
    print("watch: command exited %d (%d lines, %d bytes)" % (
        exit_code, prov["log_lines"], prov["log_bytes"]))
    print("watch: oracle_pass %r (source: %s)" % (
        draft["oracle_pass"], prov["oracle_source"]))
    print("--- draft sim/targets/%s.yaml ---" % name)
    print(text, end="")
    print("--- end draft ---")
    if not args.yes:
        try:
            answer = input("Write sim/targets/%s.yaml and run triage? "
                           "[y/N] " % name)
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("watch: aborted -- spec not written, triage not run.")
            return 1
    try:
        from afterglow_core import verilator as verilator_mod  # noqa: E402
        spec_path = write_spec(root, name, text, force=args.force)
        case = verilator_mod.load_case_file(spec_path)
        if not str(case.get("oracle_pass", "")).strip():
            raise WatchError("drafted blank oracle_pass (refusing to "
                             "chain fail-open)")
    except (WatchError, OSError, ValueError, KeyError) as exc:
        print("FATAL: cannot write spec: %s" % (exc,), file=sys.stderr)
        return 1
    print("watch: wrote %s -- chaining to triage "
          "(afterglow run --spec %s --triage)" % (spec_path, spec_path),
          flush=True)
    return chain_to_triage(spec_path, seed=args.seed)
