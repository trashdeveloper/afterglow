"""Task C9: Lint-before-resim gate (plan item 29, Wave C).

Runs Verilator ``--lint-only`` on the patched tree BEFORE any full
rebuild. A lint failure skips the resim (saves the build+run time) and
is NOT counted as an attempt; a lint pass runs the resim exactly once
(counted as an attempt).

Interface::

  lint_command(tree, case) -> list[str]
  lint_tree(tree, case, run_fn=None, timeout=LINT_TIMEOUT_S)
    -> {"lint_ok", "rc", "log", "command"}
  gate_resim(tree, case, resim_fn, run_fn=None)
    -> {"lint_ok", "skipped_resim", "counts_as_attempt",
        "resim_called", "verdict", "lint"}

``lint_tree`` lints the case RTL sources (``case["files"]`` resolved
under ``tree``) with ``verilator --lint-only --top-module <top>`` plus
the case ``vflags``. The testbench is NOT on the lint command: it is
C++, not Verilog, and ``--lint-only`` needs no elaborated binary.
``run_fn(cmd, cwd, timeout) -> (rc, log)`` defaults to the existing
sim/vbackend.py runner (real Verilator, no forked machinery); a
nonzero ``rc`` is lint-FAIL (returned, never raised), while a runner
exception propagates loudly (never a fabricated ``lint_ok``).

``gate_resim`` runs the lint first: on fail the resim is skipped
(``resim_fn`` NEVER called, ``counts_as_attempt`` False,
``verdict`` None); on pass ``resim_fn(tree, case)`` runs exactly once
and its verdict (or exception) is forwarded verbatim.

Honesty contract (mirrors equiv/blindspot/seeddiff/timing):
- Blank tree, non-dict case, missing top/files, a missing RTL source
  on disk, or ``resim_fn`` None all raise loudly (REFUSE the verdict;
  never fabricate lint_ok True/False over bad inputs).
- A resim error after a lint pass propagates; the gate never swallows
  it into a fake skip or a fake verdict.

Default-off contract (Wave C header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, and no
caller is wired: agent/loop.py and agent/guard.py are untouched.

Pure stdlib (``os``) plus the existing sim/vbackend.py ``sh`` runner;
no API, no keys, no network.
"""

import os
import sys

_REPAIR_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_REPAIR_DIR)
_ROOT = os.path.dirname(_AGENT_DIR)
sys.path.insert(0, os.path.join(_ROOT, "sim"))
sys.path.insert(0, _ROOT)

import vbackend  # noqa: E402

LINT_TIMEOUT_S = 300


def _require_tree(tree):
    if isinstance(tree, os.PathLike):
        tree = os.fspath(tree)
    if not isinstance(tree, (str, bytes)) or not str(tree).strip():
        raise ValueError(
            "lintgate needs a non-blank tree path, got %r: refusing "
            "to lint an unnamed tree" % (tree,))
    return str(tree)


def _require_case(case):
    if not isinstance(case, dict):
        raise ValueError(
            "lintgate needs a vbackend case dict, got %r" % (case,))
    top = case.get("top")
    if not isinstance(top, str) or not top.strip():
        raise ValueError(
            "lintgate needs case['top'] non-blank, got %r: refusing "
            "to guess the top module" % (top,))
    files = case.get("files")
    if (not isinstance(files, (list, tuple)) or not files
            or not all(isinstance(f, str) and f.strip() for f in files)):
        raise ValueError(
            "lintgate needs case['files'] as a non-empty string list, "
            "got %r" % (files,))
    return top.strip(), list(files)


def _resolve_sources(tree, files):
    """Absolute RTL sources; a missing file raises loudly (fail closed)."""
    srcs = []
    for rel in files:
        p = os.path.join(tree, rel)
        if not os.path.isfile(p):
            raise FileNotFoundError(
                "lintgate: missing RTL source: %s (refusing to lint "
                "a tree without its sources)" % p)
        srcs.append(p)
    return srcs


def lint_command(tree, case):
    """Build the ``verilator --lint-only`` command (no --binary: no build)."""
    tree = _require_tree(tree)
    top, files = _require_case(case)
    srcs = _resolve_sources(tree, files)
    cmd = ["verilator", "--lint-only", "--top-module", top]
    cmd += srcs
    for flag in case.get("vflags", []) or []:
        cmd.append(str(flag))
    return cmd


def lint_tree(tree, case, run_fn=None, timeout=LINT_TIMEOUT_S):
    """Lint the patched tree; return lint evidence (never raise on fail).

    ``run_fn(cmd, cwd, timeout) -> (rc, log)`` defaults to
    ``vbackend.sh`` (real Verilator). A nonzero ``rc`` yields
    ``lint_ok`` False; a runner exception propagates (missing
    verilator, timeout, ...) instead of fabricating a verdict.
    """
    tree = _require_tree(tree)
    cmd = lint_command(tree, case)
    run = run_fn or vbackend.sh
    rc, log = run(cmd, cwd=tree, timeout=timeout)
    if not isinstance(rc, int):
        raise TypeError(
            "run_fn must return (int rc, str log), got rc=%r" % (rc,))
    if not isinstance(log, str):
        raise TypeError(
            "run_fn must return (int rc, str log), got log=%r" % (type(log),))
    return {"lint_ok": rc == 0, "rc": rc, "log": log, "command": list(cmd)}


def gate_resim(tree, case, resim_fn, run_fn=None):
    """Lint first; skip the resim (uncounted) on lint failure.

    On lint pass, ``resim_fn(tree, case)`` runs exactly once and its
    verdict is forwarded verbatim (``counts_as_attempt`` True). On
    lint fail the resim never runs (``skipped_resim`` True,
    ``counts_as_attempt`` False, ``verdict`` None) so a syntax-broken
    patch costs a seconds-long lint, not a full rebuild+rerun.
    ``resim_fn`` None raises loudly; a resim exception after a lint
    pass propagates untouched.
    """
    tree = _require_tree(tree)
    if resim_fn is None:
        raise ValueError(
            "gate_resim needs resim_fn(tree, case) -> verdict, got "
            "None: refusing to gate a resim that cannot run")
    if not callable(resim_fn):
        raise ValueError(
            "gate_resim needs resim_fn callable, got %r" % (resim_fn,))
    lint = lint_tree(tree, case, run_fn=run_fn)
    if not lint["lint_ok"]:
        return {"lint_ok": False, "skipped_resim": True,
                "counts_as_attempt": False, "resim_called": False,
                "verdict": None, "lint": lint}
    verdict = resim_fn(tree, case)
    return {"lint_ok": True, "skipped_resim": False,
            "counts_as_attempt": True, "resim_called": True,
            "verdict": verdict, "lint": lint}
