#!/usr/bin/env python3
"""Task H3: design-agnostic case spec (plan item 83, Wave H GENERALITY).

Builds a Task-1 CaseSpec from ANY Verilog design + testbench + oracle
command, with zero loop/guard changes: the caller passes plain paths and
gets back a validated ``afterglow_core.types.CaseSpec`` that judges
verdicts through its ``oracle_pass`` marker (exit code ignored, per the
core contract).

Interfaces (plan binding):
  from_any(verilog_files, tb, oracle_cmd, *, case_id="generic-any",
           project="generic", oracle_pass="tohost: PASS",
           oracle_trap="TRAP:", budget_s=600, allowlist=(), lint_cmd="")
    -> CaseSpec

Validation (fail-closed, loud -- never silent defaults):
  - verilog_files: non-empty list/tuple of paths; every entry must be
    an existing file. Empty, blank, or missing entries raise loudly.
  - tb: a single path that must be an existing file (the testbench, in
    whatever shape the oracle understands: .sv, script, ...).
  - oracle_cmd: str (shell words) or list/tuple argv whose program must
    be executable: an absolute/relative path must name an executable
    file; a bare name must resolve via PATH (shutil.which). Blank or
    missing programs raise loudly -- an un-runnable oracle is never
    packed into a spec.
  - oracle_pass: non-blank string (a blank marker matches every log, so
    it is refused here -- same fail-closed rule as
    afterglow_core.verilator.load_spec and afterglow_core.policy).
  - budget_s: positive int (bool rejected).
  - allowlist: sequence of paths, packed as a list (default empty).
  - The spec's ``files`` is [verilog_files..., tb] in order.

Honesty contract:
  - from_any validates only; it builds nothing, runs nothing, writes
    no files (returns a CaseSpec; the caller runs the oracle).
  - Inputs are never mutated; allowlist is copied.

Default-off contract (Wave H header: additive + default-off): this
module runs ONLY on explicit call. It imports nothing from
agent/loop.py or agent/guard.py, never changes guard verdicts, writes
no files, and no caller is wired.

Pure stdlib (os, shlex, shutil) plus the existing
afterglow_core/types.py CaseSpec contract; no API, no keys, no network.
"""

import os
import shlex
import shutil
import sys

_DISCOVER_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_DISCOVER_DIR)
_ROOT = os.path.dirname(_AGENT_DIR)
sys.path.insert(0, _ROOT)

from afterglow_core.types import CaseSpec  # noqa: E402

__all__ = ["from_any"]

DEFAULT_CASE_ID = "generic-any"
DEFAULT_PROJECT = "generic"
DEFAULT_ORACLE_PASS = "tohost: PASS"
DEFAULT_ORACLE_TRAP = "TRAP:"
DEFAULT_BUDGET_S = 600


def _require_file(path, role):
    if isinstance(path, bool) or not isinstance(path, str) or not path:
        raise TypeError(
            "%s must be a path string, got %r: refusing to spec "
            "a non-file" % (role, path))
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "%s does not exist: %r: refusing to spec a missing "
            "file" % (role, path))
    return path


def _require_verilog_files(verilog_files):
    if isinstance(verilog_files, (str, bytes)) or not isinstance(
            verilog_files, (list, tuple)):
        raise TypeError(
            "verilog_files must be a list/tuple of paths, got %r: "
            "refusing to spec without a file list" % (verilog_files,))
    if not verilog_files:
        raise ValueError(
            "verilog_files must be non-empty: refusing to spec an "
            "empty design")
    return [_require_file(p, "verilog_files entry") for p in verilog_files]


def _require_oracle_program(oracle_cmd):
    if isinstance(oracle_cmd, str):
        if not oracle_cmd.strip():
            raise ValueError(
                "oracle_cmd must be non-blank: refusing to spec "
                "an un-runnable oracle")
        argv = shlex.split(oracle_cmd)
    elif isinstance(oracle_cmd, (list, tuple)) and oracle_cmd and all(
            isinstance(a, str) and a for a in oracle_cmd):
        argv = list(oracle_cmd)
    else:
        raise TypeError(
            "oracle_cmd must be shell words or an argv list, got %r: "
            "refusing to spec an un-runnable oracle" % (oracle_cmd,))
    program = argv[0]
    if program.startswith((".", os.sep)) or os.sep in program:
        if not os.path.isfile(program):
            raise FileNotFoundError(
                "oracle program does not exist: %r: refusing to "
                "spec a missing oracle" % (program,))
        if not os.access(program, os.X_OK):
            raise PermissionError(
                "oracle program is not executable: %r: refusing to "
                "spec an un-runnable oracle" % (program,))
    elif shutil.which(program) is None:
        raise FileNotFoundError(
            "oracle program not on PATH: %r: refusing to spec an "
            "un-runnable oracle" % (program,))
    return oracle_cmd


def _require_oracle_pass(oracle_pass):
    if not isinstance(oracle_pass, str) or not oracle_pass.strip():
        raise ValueError(
            "oracle_pass must be a non-blank marker: refusing to judge "
            "without a pass marker (fail closed)")
    return oracle_pass


def _require_budget(budget_s):
    if isinstance(budget_s, bool) or not isinstance(budget_s, int):
        raise TypeError(
            "budget_s must be an int, got %r: refusing to spec "
            "without a budget" % (budget_s,))
    if budget_s <= 0:
        raise ValueError(
            "budget_s must be > 0, got %r: refusing to spec a "
            "zero budget" % (budget_s,))
    return budget_s


def from_any(verilog_files, tb, oracle_cmd, *,
             case_id=DEFAULT_CASE_ID, project=DEFAULT_PROJECT,
             oracle_pass=DEFAULT_ORACLE_PASS,
             oracle_trap=DEFAULT_ORACLE_TRAP,
             budget_s=DEFAULT_BUDGET_S, allowlist=(), lint_cmd=""):
    """Build a validated CaseSpec for any Verilog design + TB + oracle.

    verilog_files: non-empty list/tuple of existing RTL paths.
    tb: path to the existing testbench file.
    oracle_cmd: executable oracle (shell words or argv list).
    Returns a CaseSpec whose files are [verilog_files..., tb] and whose
    oracle_pass marker judges the oracle log (exit code ignored).
    Anything unvalidatable raises loudly; nothing is built, run, or
    written.
    """
    files = _require_verilog_files(verilog_files)
    tb_path = _require_file(tb, "tb")
    _require_oracle_program(oracle_cmd)
    marker = _require_oracle_pass(oracle_pass)
    budget = _require_budget(budget_s)
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(
            "case_id must be a non-empty str, got %r" % (case_id,))
    if not isinstance(project, str) or not project:
        raise ValueError(
            "project must be a non-empty str, got %r" % (project,))
    if not isinstance(oracle_trap, str):
        raise TypeError(
            "oracle_trap must be a str, got %r" % (oracle_trap,))
    if isinstance(allowlist, (str, bytes)):
        raise TypeError(
            "allowlist must be a sequence of paths, got %r" % (allowlist,))
    allow = list(allowlist)
    if not isinstance(lint_cmd, str):
        raise TypeError(
            "lint_cmd must be a str, got %r" % (lint_cmd,))
    return CaseSpec(
        case_id=case_id,
        project=project,
        files=files + [tb_path],
        oracle_pass=marker,
        oracle_trap=oracle_trap,
        budget_s=budget,
        allowlist=allow,
        lint_cmd=lint_cmd,
    )
