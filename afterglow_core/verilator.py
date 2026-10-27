"""Verilator SimBackend + --spec loading for the `afterglow` CLI (Task 4).

Serves eval cases: every sim/targets/*.yaml (locked + stress). Wraps the
REAL build/run in sim/vbackend.py -- no stubs, no mocks:

- build: ``verilator --binary --timing`` via :func:`vbackend.build`.
- run: the built binary with ``+seed=<N>``; the Verdict comes ONLY from
  the oracle marker in the log via :func:`decide_verdict` (never from
  the process exit code), and the checksum covers deterministic lines
  only (log lines not starting with ``"- "``, same convention as
  ``vbackend.run_case`` and ``agent/loop.py:resim_after``).
- ``resim``: crash-safe RESIM seam for any SimBackend. A backend whose
  run() raises becomes a RED bundle (exit 1, meta.json records the
  error) -- a crash is never green.

CaseSpec loading (``load_spec``/``load_case_file``) parses the same flat
``key: value`` YAML schema as :func:`vbackend.load_case`, but from an
arbitrary ``--spec`` path so the CLI can run a stranger case. Adapter
defaults (documented, not silent -- mirrors ``loop.case_to_spec``):
``budget_s`` defaults to 600, ``lint_cmd`` defaults to ``""`` (empty
skips the external lint step), and the allowlist comes from the same
``agent/allowlist.txt`` file the loop has always used.

NOTE: ``pass`` is a Python keyword: every Verdict here is built as
``Verdict(**{"pass": ..., "evidence": ...})`` and read via
``getattr(v, "pass")`` -- never positional, never ``.pass`` access.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "sim")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import vbackend  # noqa: E402

from agent import guard  # noqa: E402
from afterglow_core.types import CaseSpec, Verdict, decide_verdict  # noqa: E402

EXIT_GREEN = 0
EXIT_RED = 1
RUN_TIMEOUT_S = 300

# Same required flat keys as vbackend.load_case; oracle_trap/vflags keep
# vbackend's defaults.
_REQUIRED_KEYS = ("project", "files", "top", "tb", "anchor", "bug", "gate",
                  "oracle_pass")


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def load_case_file(path: str) -> dict:
    """Parse a flat ``key: value`` case YAML at any path (stdlib only).

    Same schema as :func:`vbackend.load_case` (which only reads
    ``sim/targets/<name>.yaml``); the case ``name`` is the file stem.
    Raises OSError on unreadable files, ValueError on bad lines,
    KeyError on missing required keys -- loudly, never silent defaults
    for the oracle marker.
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    case: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("bad yaml line in %s: %r" % (path, raw))
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = []
            if inner:
                items = [_unquote(x) for x in inner.split(",")]
            case[key] = items
        else:
            case[key] = _unquote(val)
    for req in _REQUIRED_KEYS:
        if req not in case:
            raise KeyError("spec %s missing key %s" % (path, req))
    if isinstance(case["files"], str):
        case["files"] = [case["files"]]
    case.setdefault("oracle_trap", "TRAP:")
    case.setdefault("vflags", [])
    if isinstance(case["vflags"], str):
        case["vflags"] = [case["vflags"]] if case["vflags"] else []
    stem = os.path.splitext(os.path.basename(path))[0]
    case["name"] = stem
    case["spec_path"] = os.path.abspath(path)
    return case


def load_spec(path: str) -> CaseSpec:
    """Load a Task-1 CaseSpec (8 fields) from a ``--spec`` YAML path.

    Fail-closed: a blank ``oracle_pass`` matches every log, so the spec
    is refused here with ValueError (same rule as
    :func:`afterglow_core.policy.check_diff`). The ``--spec`` path must
    additionally be in-tree (``sim/targets/<stem>.yaml``): the loop's RED
    phase dispatches ``sim/sim.py --case <stem>``, so unknown stems are
    rejected fast by the caller before any sim/model work.
    """
    case = load_case_file(path)
    if not str(case.get("oracle_pass", "")).strip():
        raise ValueError(
            "spec %s has blank oracle_pass: refusing to judge without "
            "a pass marker (fail closed)" % path)
    return CaseSpec(
        case_id=str(case.get("name", "?")),
        project=str(case.get("project", "?")),
        files=list(case.get("files", [])),
        oracle_pass=str(case.get("oracle_pass", "")),
        oracle_trap=str(case.get("oracle_trap", "TRAP:")),
        budget_s=int(case.get("budget_s", 600)),
        allowlist=list(guard.load_allowlist()),
        lint_cmd=str(case.get("lint_cmd", "") or ""),
    )


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def deterministic_checksum(log_text: str) -> str:
    """Checksum over deterministic lines only (drops ``- `` log lines)."""
    det = [ln for ln in log_text.splitlines() if not ln.startswith("- ")]
    return hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()


def parse_tohost(log_text: str) -> str | None:
    for ln in log_text.splitlines():
        if "tohost:" in ln:
            return ln.split("tohost:", 1)[1].strip()
    return None


def parse_cycles(log_text: str) -> int | None:
    match = re.search(r"CYCLES=(\d+)", log_text)
    return int(match.group(1)) if match else None


class VerilatorBackend:
    """SimBackend over the REAL Verilator board (Task-1 protocol).

    ``build(tree)`` runs ``verilator --binary --timing`` via
    :func:`vbackend.build`; ``run(seed)`` executes the built binary with
    ``+seed`` and judges the log by the oracle marker only.
    """

    def __init__(self, case: dict) -> None:
        if not str(case.get("oracle_pass", "")).strip():
            raise ValueError(
                "VerilatorBackend requires a non-blank oracle_pass "
                "(fail closed: a blank marker matches every log, so "
                "decide_verdict would report green on anything)")
        self.case = case
        self.tree: str | None = None
        self.binary: str | None = None

    def build(self, tree) -> None:
        self.tree = str(tree)
        self.binary = str(vbackend.build(tree, self.case))

    def run(self, seed: int) -> Verdict:
        if not self.binary:
            raise RuntimeError(
                "VerilatorBackend.run before build: no sim binary "
                "(call build(tree) first)")
        try:
            proc = subprocess.run(
                [self.binary, "+seed=%d" % int(seed)],
                cwd=str(self.tree or "."),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=RUN_TIMEOUT_S)
            log_text = proc.stdout
        except subprocess.TimeoutExpired:
            log_text = ("- TIMEOUT after %ds\ntohost: FAIL 0xdead\n"
                        "TRAP: sim timeout\n" % RUN_TIMEOUT_S)
        oracle = str(self.case.get("oracle_pass", ""))
        decided = decide_verdict(log_text, oracle)
        evidence = dict(decided.evidence)
        evidence.update({
            # Explicit (not just inherited from decide_verdict): resim's
            # fail-closed gate keys on evidence["oracle_pass"], so run()
            # must always declare the oracle it judged by -- even if
            # decide_verdict's evidence shape ever changes.
            "oracle_pass": oracle,
            "tohost": parse_tohost(log_text),
            "checksum": deterministic_checksum(log_text),
            "cycles": parse_cycles(log_text),
            "seed": int(seed),
            "binary": self.binary,
            "log": log_text,
        })
        return Verdict(
            **{"pass": bool(getattr(decided, "pass")),
               "evidence": evidence})


def resim(backend, seed: int, run_dir: str) -> int:
    """RESIM through any SimBackend into a runs/<id>/ bundle tail.

    Writes ``artifacts/boot_after.log``, ``artifacts/verdict_after.json``
    and ``meta.json`` (merged over any existing keys). Returns
    EXIT_GREEN (0) only when the backend Verdict passes; any exception
    (sim crash, timeout, non-Verdict return) becomes status ``"red"``
    with the crash recorded in ``meta.json["error"]`` -- exit 1, never
    green. Fail-closed on a blank ``oracle_pass`` declared in the
    verdict evidence: a blank marker matches every log, so an
    explicitly blank oracle forces RED (exit 1) even when the backend
    claims pass. Backends that do not report an oracle in evidence are
    judged on their verdict alone (``VerilatorBackend`` always reports
    its oracle and refuses blanks at construction).
    """
    os.makedirs(os.path.join(run_dir, "artifacts"), exist_ok=True)
    meta_path = os.path.join(run_dir, "meta.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        if not isinstance(meta, dict):
            meta = {}
    except (OSError, ValueError):
        meta = {}
    meta["sim_seed"] = int(seed)

    def _write_after(log_text: str, verdict_after: dict) -> None:
        with open(os.path.join(run_dir, "artifacts", "boot_after.log"),
                  "w", encoding="utf-8") as fh:
            fh.write(log_text)
        with open(os.path.join(run_dir, "artifacts",
                               "verdict_after.json"),
                  "w", encoding="utf-8") as fh:
            fh.write(json.dumps(verdict_after, indent=2) + "\n")
        with open(meta_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, indent=2) + "\n")

    try:
        verdict = backend.run(int(seed))
        passed = bool(getattr(verdict, "pass"))
        evidence = dict(getattr(verdict, "evidence", {}) or {})
    except Exception as exc:
        crashed = ("(RESIM crashed: %r)\n" % (exc,))
        meta["status"] = "red"
        meta["error"] = "RESIM crashed: %r" % (exc,)
        meta["wall_end_utc"] = utc_now()
        _write_after(crashed, {
            "pass": False, "tohost": None, "checksum": None,
            "cycles": None, "seed": int(seed),
            "sim_time_utc": meta["wall_end_utc"],
            "note": "backend run() raised; see meta.json error",
        })
        return EXIT_RED

    declared = evidence.get("oracle_pass")
    if declared is not None and not str(declared).strip():
        # Fail closed: "" in <any log> is True, so a blank declared
        # oracle would manufacture green. Refuse, loudly, as RED.
        meta["status"] = "red"
        meta["error"] = ("empty oracle_pass: refusing to judge without "
                         "a pass marker (fail closed)")
        meta["wall_end_utc"] = utc_now()
        _write_after("(RESIM refused: empty oracle_pass)\n", {
            "pass": False, "tohost": evidence.get("tohost"),
            "checksum": None, "cycles": evidence.get("cycles"),
            "seed": int(seed), "sim_time_utc": meta["wall_end_utc"],
            "note": "blank oracle_pass declared; see meta.json error",
        })
        return EXIT_RED

    log_text = evidence.get("log")
    if not isinstance(log_text, str):
        log_text = "(backend returned no log text)\n"
    tohost = evidence.get("tohost", parse_tohost(log_text))
    cycles = evidence.get("cycles", parse_cycles(log_text))
    checksum = evidence.get("checksum",
                            deterministic_checksum(log_text))
    verdict_after = {
        "pass": bool(passed), "tohost": tohost, "checksum": checksum,
        "cycles": cycles, "seed": int(seed),
        "sim_time_utc": utc_now(),
    }
    meta["status"] = "green" if passed else "red"
    if not passed:
        meta["note"] = "backend returned RED verdict (no crash)"
    meta["tohost_after"] = tohost
    meta["checksum_after"] = checksum
    meta["cycles_after"] = cycles
    meta["wall_end_utc"] = verdict_after["sim_time_utc"]
    _write_after(log_text if log_text.endswith("\n") else log_text + "\n",
                 verdict_after)
    return EXIT_GREEN if passed else EXIT_RED
