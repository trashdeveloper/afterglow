"""Per-project guard policy: check_diff over a CaseSpec.

Same accept rules as :func:`agent.guard.check` (allowlist subset,
at most 40 changed lines, real unified diff, no whitespace-only or
binary change, no forbidden patterns, per-file lint), except the
allowlist and lint come from the per-project ``CaseSpec`` instead of
global files:

- allowlist: ``spec.allowlist`` (basenames compared, as in guard).
- per-file lint: ``spec.lint_cmd`` run once per touched file with
  ``cwd=rtl_root``. ``{file}`` in the command is replaced by the
  touched path as declared in the diff. Empty ``spec.lint_cmd``
  skips this external lint (guard's own ``lint_rtl`` still applies).

Fail-closed: an empty or blank-only ``spec.oracle_pass`` matches nearly
every log, so the diff is rejected with reason ``"empty oracle_pass"``
before the diff is even inspected.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Dict, List, Tuple

from agent import guard

from afterglow_core.types import CaseSpec

LINT_TIMEOUT_S = 60


def check_diff(
    diff_text: str, spec: CaseSpec, rtl_root: str
) -> Tuple[bool, List[str], Dict]:
    """Validate a unified diff against a per-project spec.

    Returns (ok, reasons, info); info carries guard's keys plus
    ``lint_cmd`` mapping each touched file to its external-lint result.
    """
    allowed_bases = sorted(
        {
            os.path.basename(str(a).strip())
            for a in (spec.allowlist or [])
            if str(a).strip()
        }
    )
    if not (spec.oracle_pass or "").strip():
        info: Dict = {
            "files": [],
            "patch_lines": 0,
            "added": 0,
            "removed": 0,
            "allowlist": allowed_bases,
            "lint": {},
            "lint_cmd": {},
        }
        return (
            False,
            ["empty oracle_pass: refusing to judge without a pass marker (fail closed)"],
            info,
        )

    guard_ok, reasons, info = guard.check(
        diff_text, allowlist=spec.allowlist, rtl_root=rtl_root
    )
    reasons = list(reasons)
    n_guard_reasons = len(reasons)

    lint_cmd_results: Dict[str, bool] = {}
    cmd_tpl = (spec.lint_cmd or "").strip()
    if cmd_tpl:
        argv = shlex.split(cmd_tpl)
        for touched in info.get("files", []):
            cmd = [token.replace("{file}", touched) for token in argv]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=rtl_root,
                    capture_output=True,
                    text=True,
                    timeout=LINT_TIMEOUT_S,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                lint_cmd_results[touched] = False
                reasons.append(f"lint_cmd failed for {touched}: {exc}")
                continue
            passed = proc.returncode == 0
            lint_cmd_results[touched] = passed
            if not passed:
                output = (proc.stderr or proc.stdout or "").strip().splitlines()
                tail = output[-1][:200] if output else ""
                suffix = f": {tail}" if tail else ""
                reasons.append(
                    f"lint_cmd failed for {touched}: exit {proc.returncode}{suffix}"
                )
    info["lint_cmd"] = lint_cmd_results

    # Honor guard's own verdict; the lint step only vetoes (appends reasons).
    lint_ok = len(reasons) == n_guard_reasons
    return (guard_ok and lint_ok), reasons, info
