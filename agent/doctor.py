#!/usr/bin/env python3
"""`afterglow doctor` / `afterglow demo` / `afterglow init --here`.

ONBOARD-SIMPLE one-shot helpers. No loop/guard/score/bundle semantics
changes, no keys, stdlib only. Every check returns a plain
``{"name", "status", "message", "fix"}`` dict -- status is ``"PASS"``,
``"FAIL"`` or ``"SKIP"`` -- so the CLI prints a helpful fix instead of
a traceback. Key values are NEVER printed (presence only).

- doctor: one checklist line per first-run need (python version,
  ``verilator --version``, model-key presence, pins readable, pinned
  trees cached, suite smoke). Exit 0 when no FAIL; exit 1 listing each
  miss + the one command that fixes it.
- demo: zero-decision triage of the fastest locked GREEN case in
  ``eval/results.md`` (parsed at runtime, never guessed). Triage DOES
  call the model once (~$0.05 -- stated upfront); with no key it fails
  loud with the exact fix and never calls the loop (no fake run).
- init --here: non-interactive starter CaseSpec from the current
  directory's ``.v``/``.sv`` files, reusing agent/watch.py draft logic
  (extract_rtl_files -> draft_spec -> render_yaml), never duplicated.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(AGENT_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent import loop  # noqa: E402
from agent import watch  # noqa: E402
from afterglow_core import initenv  # noqa: E402
from afterglow_core import slicer  # noqa: E402

MIN_PYTHON = (3, 10)

# Fastest locked GREEN case in eval/results.md, re-verified live
# (Nov-4 board: fetch-misalign 57.9s < uart-div 61.2s).
# Runtime fallback only -- pick_demo_case() parses the file, so the
# board can overrule.
DEMO_CASE = "picorv32-fetch-misalign"
DEMO_COST_NOTE = "~$0.05 (one triage diagnosis model call)"

_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(locked|stress)\s*\|\s*(GREEN|RED)\s*\|"
    r"\s*(\S+)\s*\|\s*([0-9.\-]+)", re.MULTILINE)


def check_python(py_version=None) -> dict:
    """Interpreter >= 3.10? (runtime `X | Y` unions need it)."""
    if py_version is None:
        v = sys.version_info
        py_version = (v.major, v.minor, v.micro)
    cur = tuple(py_version[:3])
    text = "%d.%d.%d" % cur
    if cur >= MIN_PYTHON:
        return {"name": "python", "status": "PASS",
                "message": "PASS: python %s (>= %s.%s)" % (
                    text, MIN_PYTHON[0], MIN_PYTHON[1]),
                "fix": ""}
    return {"name": "python", "status": "FAIL",
            "message": "FAIL: python %s is older than %s.%s "
                       "(runtime `X | Y` unions need %s.%s+)" % (
                           text, MIN_PYTHON[0], MIN_PYTHON[1],
                           MIN_PYTHON[0], MIN_PYTHON[1]),
            "fix": "install Python >= %s.%s, e.g. "
                   "`sudo apt install python3.12` (Debian/Ubuntu) or "
                   "`brew install python@3.12` (macOS)" % MIN_PYTHON}


def check_verilator(which=None, run=None) -> dict:
    """Verilator on PATH and `verilator --version` runs?"""
    base = initenv.check_verilator(which=which)
    if base["status"] != "ok":
        return {"name": "verilator", "status": "FAIL",
                "message": base["message"],
                "fix": "`sudo apt install verilator` (Debian/Ubuntu) "
                       "or `brew install verilator` (macOS), then "
                       "re-run `afterglow doctor`"}
    finder = which or shutil.which
    exe = finder("verilator")
    runner = run or (lambda cmd: _run_version(cmd))
    try:
        rc, out = runner([exe or "verilator", "--version"])
    except OSError as exc:
        return {"name": "verilator", "status": "FAIL",
                "message": "FAIL: verilator found at %s but "
                           "`--version` did not run: %s" % (exe, exc),
                "fix": "reinstall Verilator: `sudo apt install "
                       "--reinstall verilator` (Debian/Ubuntu) or "
                       "`brew reinstall verilator` (macOS)"}
    first = (out or "").strip().splitlines()
    if rc != 0 or not first:
        return {"name": "verilator", "status": "FAIL",
                "message": "FAIL: `verilator --version` exits %s "
                           "(found at %s)" % (rc, exe),
                "fix": "reinstall Verilator: `sudo apt install "
                       "--reinstall verilator` (Debian/Ubuntu) or "
                       "`brew reinstall verilator` (macOS)"}
    return {"name": "verilator", "status": "PASS",
            "message": "PASS: %s (at %s)" % (first[0].strip(), exe),
            "fix": ""}


def _run_version(cmd: list) -> tuple:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          timeout=60)
    return proc.returncode, proc.stdout


def check_api_key(env=None, dotenv=None) -> dict:
    """Model key in env or .env? Presence only -- value never printed."""
    base = initenv.check_api_key(env=env, dotenv=dotenv)
    if base["status"] == "ok":
        return {"name": "api-key", "status": "PASS",
                "message": base["message"]
                           + " (presence only, value never shown)",
                "fix": ""}
    return {"name": "api-key", "status": "FAIL",
            "message": base["message"],
            "fix": "export MODEL_API_KEY=... (SPARK_API_KEY / "
                   "META_API_KEY also accepted) or add MODEL_API_KEY=... "
                   "to .env in the project root"}


def check_pins(root: str) -> list:
    """foundation.pin + targets.pin readable and non-empty?"""
    out = []
    for res in initenv.check_pins(root):
        if res["status"] == "ok":
            out.append({"name": res["name"], "status": "PASS",
                        "message": "PASS: %s" % res["message"],
                        "fix": ""})
        else:
            out.append({"name": res["name"], "status": "FAIL",
                        "message": "FAIL: %s" % res["message"],
                        "fix": "re-clone the project root or restore %s "
                               "(it records the frozen Weft reference)" % (
                                   res["name"],)})
    return out


def _pin_projects(pin_path: str) -> list:
    projects = []
    with open(pin_path, "r", encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[0] not in projects:
                projects.append(parts[0])
    return projects


def check_pinned_trees(root: str, pin_path=None, work_dir=None) -> list:
    """Pinned RTL work-trees cached (read-only check, never clones)?"""
    pin = pin_path or os.path.join(root, "targets.pin")
    try:
        projects = _pin_projects(pin)
    except OSError:
        return [{"name": "pinned-trees", "status": "SKIP",
                 "message": "SKIP: pinned-tree check needs targets.pin "
                            "(see the pins line above)",
                 "fix": ""}]
    if not projects:
        return [{"name": "pinned-trees", "status": "FAIL",
                 "message": "FAIL: no projects pinned in %s" % pin,
                 "fix": "restore targets.pin (frozen Weft reference), "
                        "then re-run `afterglow doctor`"}]
    out = []
    for project in projects:
        try:
            tree = slicer.resolve_pinned_tree(
                project, pin_path=pin, work_dir=work_dir)
        except (FileNotFoundError, KeyError, OSError) as exc:
            out.append({"name": "pinned-tree:%s" % project,
                        "status": "FAIL",
                        "message": "FAIL: pinned tree for %r not cached: "
                                   "%s" % (project, exc),
                        "fix": "python3 -c \"import sys; "
                               "sys.path.insert(0, 'sim'); import vbackend; "
                               "vbackend.ensure_tree('%s')\" "
                               "(from the repo root; first run auto-clones "
                               "it, this only warms the cache)" % project})
        else:
            out.append({"name": "pinned-tree:%s" % project,
                        "status": "PASS",
                        "message": "PASS: pinned tree for %r cached at %s"
                                   % (project, tree),
                        "fix": ""})
    return out


def check_suite_smoke() -> dict:
    """Full suite is slow: skip it, report the one command that runs it."""
    return {"name": "suite-smoke", "status": "SKIP",
            "message": "SKIP: suite smoke not run (1917 tests are slow) "
                       "-- run `python3 -m unittest discover -s tests` "
                       "for the full gate",
            "fix": ""}


def format_line(res: dict) -> str:
    """Render one checklist result as a PASS:/FAIL:/SKIP: line."""
    return res["message"]


def run_doctor(root=None, py_version=None, which=None, run=None,
               env=None, dotenv=None, pin_path=None,
               work_dir=None) -> tuple:
    """Run every check; return (results, exit_code).

    Exit 0 when no FAIL line; exit 1 otherwise. SKIP never fails.
    Fixture hooks (which/run/env/dotenv/py_version/pin_path/work_dir)
    exist for tests; production passes none.
    """
    root = root or ROOT
    dotenv = initenv.read_dotenv(root) \
        if dotenv is None else dotenv
    env = dict(os.environ) if env is None else env
    results = [check_python(py_version=py_version),
               check_verilator(which=which, run=run),
               check_api_key(env=env, dotenv=dotenv)]
    results.extend(check_pins(root))
    results.extend(check_pinned_trees(root, pin_path=pin_path,
                                      work_dir=work_dir))
    results.append(check_suite_smoke())
    rc = 1 if any(r["status"] == "FAIL" for r in results) else 0
    return results, rc


def pick_demo_case(results_path=None) -> str:
    """Fastest locked GREEN case in eval/results.md (never a guess)."""
    path = results_path or os.path.join(ROOT, "eval", "results.md")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return DEMO_CASE
    best, best_t = None, None
    for match in _ROW_RE.finditer(text):
        case, tier, status, _run, secs = match.groups()
        if tier != "locked" or status != "GREEN":
            continue
        try:
            secs = float(secs)
        except ValueError:
            continue
        if best is None or secs < best_t:
            best, best_t = case.strip(), secs
    return best or DEMO_CASE


def run_demo(root=None, env=None, dotenv=None, seed: int = 42) -> int:
    """Zero-decision triage of the fastest locked GREEN case.

    Prints the cost upfront (triage burns ONE diagnosis call), fails
    loud with the exact fix when no key is present (loop never called,
    no fake run), else chains into the triage path and propagates its
    exit code.
    """
    root = root or ROOT
    case = pick_demo_case(os.path.join(root, "eval", "results.md"))
    spec = os.path.join(root, "sim", "targets", case + ".yaml")
    print("afterglow demo: triage-only run of %s -- this burns ONE "
          "model diagnosis call (cost %s); patch withheld, diagnosis "
          "saved" % (case, DEMO_COST_NOTE), flush=True)
    # Gate on the ENVIRONMENT only -- loop.main reads os.environ
    # (agent/loop.py), never .env, so a .env-only key must NOT green
    # this gate or demo would burn minutes of retries then fail. The
    # dotenv kwarg stays for callers, but it never gates the run.
    env = dict(os.environ) if env is None else env
    key = check_api_key(env=env, dotenv={})
    if key["status"] != "PASS":
        print("FAIL: demo needs a model key in the environment "
              "(triage calls the live decider once, cost %s; "
              "`afterglow run` reads the environment, a .env entry "
              "alone is not auto-loaded)." % DEMO_COST_NOTE, flush=True)
        print("fix: %s" % ("export MODEL_API_KEY=... (or add "
                            "MODEL_API_KEY=... to .env AND export it), "
                            "then re-run `afterglow demo`"), flush=True)
        return 1
    if not os.path.isfile(spec):
        print("FAIL: demo spec missing: %s (expected the fastest "
              "locked GREEN case from eval/results.md)" % spec,
              file=sys.stderr, flush=True)
        return 1
    print("afterglow demo: spec %s (case %s)" % (spec, case),
          flush=True)
    return loop.main(["--spec", spec, "--triage", "--seed", str(seed)])


def draft_here_spec(dirname: str, force: bool = False) -> str:
    """Draft `<dirname>/<stem>.yaml` from its .v/.sv files (no prompt).

    Reuses watch.extract_rtl_files -> watch.draft_spec ->
    watch.render_yaml (empty log: exit-code-fallback markers, canonical
    pass marker). Non-interactive: refuses to overwrite without
    force=True. Raises watch.WatchError loudly (no HDL, bad dir, or
    existing file) -- no partial file is ever left behind.
    """
    here = os.path.abspath(dirname)
    if not os.path.isdir(here):
        raise watch.WatchError("not a directory: %s" % dirname)
    hdl = sorted(f for f in os.listdir(here)
                 if f.endswith((".v", ".sv"))
                 and os.path.isfile(os.path.join(here, f)))
    if not hdl:
        raise watch.WatchError(
            "no .v/.sv files in %s: `init --here` drafts from the "
            "current directory's HDL sources" % here)
    stem = os.path.basename(here).strip()
    name = watch.sanitize_name(stem) if stem else "my-case"
    draft = watch.draft_spec(name, hdl, "", 0)
    text = watch.render_yaml(draft)
    dest = os.path.join(here, name + ".yaml")
    if os.path.exists(dest) and not force:
        raise watch.WatchError(
            "spec %s exists: re-run with --force to overwrite, or "
            "rename it first" % dest)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    return dest
