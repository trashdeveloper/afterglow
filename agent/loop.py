#!/usr/bin/env python3
"""Afterglow live agent loop: RED -> COLLECT -> DIAGNOSE -> EXPLAIN -> PATCH
-> RESIM -> VERIFY. No stubs, no mocks, no heuristics fallback.

Serves eval cases: every case in sim/targets/*.yaml (locked + stress).
Text API (Muse Spark Text) is the SOLE decider of diagnosis -> patch.
If the API is down or returns empty, the loop fails LOUDLY (exit 1, partial
bundle kept, no heuristic patch is ever applied).

Flow per run (artifacts land in runs/<run-id>/):
  RED      sim/sim.py BEFORE (subprocess) -> artifacts/boot.log +
           artifacts/verdict_before.json
  COLLECT  collect.collect (<=200 lines, <=2500 tokens, counts logged) ->
           artifacts/input_trace.txt
  DIAGNOSE live POST {SPARK_API_URL|default}/v1/chat/completions,
           MODEL_ID muse-spark-1.3-contributor, key from
           MODEL_API_KEY/SPARK_API_KEY/META_API_KEY, max_tokens 8000,
           timeout 180s -> api/text_request.json (prompt+sha+tokens) +
           api/text_response.json (raw+latency_ms+request_id+model+usage)
  EXPLAIN  agent/visual.py live image (trace_sha + diagnosis -> explain.png
           BEFORE resim; FAILED marked in meta, never silent) ->
           api/image_request.json + api/image_response.json + explain.png
  PATCH    verbatim model patch_diff first via guard.check + patch -p1
           dry-run/apply. Guard refusal saves rejected_model.diff.
           Bootstrap gated applier ONLY when the live diagnosis names a
           gate signal: line-anchored canonical restore, guard-checked,
           logged as canonical-bootstrap-gated.
  RESIM    rebuild + rerun the sim binary in the patched tree (no
           re-inject: vbackend.run_case would wipe the patch) ->
           artifacts/boot_after.log + artifacts/verdict_after.json
  VERIFY   green = tohost PASS in verdict_after.json. Exit 0 GREEN / 1 RED.

Usage:
  python3 agent/loop.py --case picosoc-uart-div [--seed 42]
      [--prompt agent/prompts/v1.txt] [--run-id ID] [--rtl path/in/tree]
  python3 agent/loop.py --spec sim/targets/uart-start-half.yaml
      [--triage|--stop-before-patch ...]
      (--spec is in-tree-only: the stem must match sim/targets/<id>.yaml
      because the RED phase dispatches sim/sim.py --case <stem>; it
      overrides --case when both are given)

  Mutant live-fire (big-N pilot): sanctioned sim/targets/mut-pilot-*.yaml
  stems fire via the same --spec flag, but RED applies the pinned
  anchor->bug line onto a runs/scratch copy of the pinned pristine tree
  (line-targeted, verified single-line) instead of dispatching sim.py,
  whose line-anchored inject would mis-fire multi-match anchors.

  FULL-AUTO BY DEFAULT: running without flags attempts the whole
  diagnose->patch->resim->verify end-to-end. --triage (or
  --stop-before-patch) selects the old stop-before-PATCH behavior
  (diagnosis saved, patch withheld, exit 2). --auto is accepted for
  compatibility and also means auto.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, AGENT_DIR)
sys.path.insert(0, os.path.join(ROOT, "sim"))
sys.path.insert(0, ROOT)

import collect  # noqa: E402
import context as context_pack  # noqa: E402
import guard  # noqa: E402
import report  # noqa: E402
import visual  # noqa: E402
import vbackend  # noqa: E402

from afterglow_core.policy import check_diff as policy_check_diff  # noqa: E402
from afterglow_core import cost as cost_ledger  # noqa: E402
from afterglow_core import fingerprint as harvest_fp  # noqa: E402
from afterglow_core import memory as fix_memory  # noqa: E402
from afterglow_core import packer as harvest_pack  # noqa: E402
from afterglow_core import slicer as harvest_slice  # noqa: E402
from afterglow_core.types import CaseSpec, Verdict  # noqa: E402
from afterglow_core.verilator import load_case_file as load_spec_case  # noqa: E402

TEXT_MODEL = "muse-spark-1.3-contributor"
TEXT_PATH = "/v1/chat/completions"
DEFAULT_BASE = "https://api.meta.ai"
TEXT_TIMEOUT_S = 180
TEXT_MAX_TOKENS = 8000
MAX_ATTEMPTS = 3  # initial + 2 retries (AGENTS.md section 2e), then RED

# Full-auto-by-default loop mode: every run attempts PATCH->RESIM->VERIFY
# (mode "live", exit 0/1) unless --triage / --stop-before-patch selects
# the old stop-before-PATCH behavior (mode "triage", exit 2). --auto is
# accepted for compatibility and also means live. A loud pre-patch
# failure in live mode (API flake with no patch, guard reject with no
# patch ever applied, --triage withhold) DEGRADEs to a triage-style
# stop (status "triage", exit 2, TRIAGE row) -- never a silent proceed,
# never a false green. A patch applied but resim still RED stays RED
# (exit 1).
MODE_TRIAGE = "triage"
MODE_LIVE = "live"
EXIT_GREEN = 0
EXIT_RED = 1
EXIT_TRIAGE = 2

# OPTIMIZE-mode (fix+opt) goal selector. Default "fix" preserves the exact
# fix-first flow (no opt pass runs). "fix+opt" runs ONE optimize pass after
# a GREEN fix; the opt patch must pass the SAME guard + resim with
# IDENTICAL checksum/tohost, else it is rejected and the GREEN fix stands
# (the mode never ungreens a fix).
GOALS_FIX = "fix"
GOALS_FIX_OPT = "fix+opt"
GOALS_CHOICES = (GOALS_FIX, GOALS_FIX_OPT)

OPT_STATUS_SKIPPED = "skipped"
OPT_STATUS_ACCEPTED = "accepted"
OPT_STATUS_REJECTED = "rejected"
OPT_STATUS_FAILED = "failed"

OPT_PROMPT_NAME = "v4opt"

# OPTIMIZE-mode honesty: this pilot measures SIMULATED CYCLES from the sim
# log only. No hardware frequency, speed, power, or energy claim is made.
OPT_DISCLAIMER = (
    "OPTIMIZE-mode honesty: SIMULATED CYCLES from the sim log only; "
    "no hardware frequency, speed, power, or energy claim.")


def resolve_mode(args) -> str:
    """Return the run mode for parsed args (pure, no I/O).

    FULL-AUTO BY DEFAULT: mode is "live" unless --triage or
    --stop-before-patch explicitly selects the old stop-before-PATCH
    behavior. --auto is accepted for compatibility and also means
    live. Ambiguity fails closed: any triage flag wins over --auto.
    """
    if getattr(args, "triage", False) or getattr(
            args, "stop_before_patch", False):
        return MODE_TRIAGE
    return MODE_LIVE


def should_withhold_patch(args) -> bool:
    """True when the run must stop after DIAGNOSE+EXPLAIN (triage)."""
    return resolve_mode(args) != MODE_LIVE


def case_to_spec(case: dict) -> "CaseSpec":
    """Adapt a vbackend case dict to a Task-1 CaseSpec for policy checks.

    Adapter defaults (documented, not silent): vbackend YAML has no
    budget_s/lint_cmd fields, so budget_s defaults to 600 and lint_cmd to
    "" (empty skips the external lint step, matching the loop's historic
    guard-only behavior). allowlist comes from the same
    agent/allowlist.txt file the loop has always used via guard.
    oracle_pass/oracle_trap/files/project come verbatim from the case.
    """
    allowlist = guard.load_allowlist()
    return CaseSpec(
        case_id=str(case.get("name", case.get("case_id", "?"))),
        project=str(case.get("project", "?")),
        files=list(case.get("files", [])),
        oracle_pass=str(case.get("oracle_pass", "")),
        oracle_trap=str(case.get("oracle_trap", "TRAP:")),
        budget_s=int(case.get("budget_s", 600)),
        allowlist=list(allowlist),
        lint_cmd=str(case.get("lint_cmd", "") or ""),
    )


class LiveAPIError(RuntimeError):
    """Live Text API down/empty/forbidden -- always fatal, never heuristic."""


class LoopError(RuntimeError):
    """Fatal loop failure (sim broken, collector cap, patch impossible)."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def api_key() -> str:
    for var in ("MODEL_API_KEY", "SPARK_API_KEY", "META_API_KEY"):
        key = os.environ.get(var, "").strip()
        if key:
            return key
    raise LiveAPIError(
        "missing text API key: set MODEL_API_KEY "
        "(SPARK_API_KEY / META_API_KEY also accepted); "
        "refusing to continue without the live decider")


def text_url() -> str:
    raw = os.environ.get("SPARK_API_URL", "").strip()
    if raw.rstrip("/").endswith(TEXT_PATH):
        return raw.rstrip("/")
    base = (raw or DEFAULT_BASE).rstrip("/")
    return base + TEXT_PATH


def write_cost_json(run_dir: str, cycles: dict | None = None) -> dict:
    """Write the per-run token+$ ledger into a NEW bundle dir.

    Costs api/text_response.json + api/image_response.json via
    afterglow_core.cost (missing files tolerate to zero) and saves
    cost.json next to meta.json. Only ever called with the current
    run's dir -- old bundles are never touched.

    cycles (OPTIMIZE-mode only): {"cycles_before", "cycles_after",
    "cycles_saved"} ledger of SIMULATED CYCLES for the opt pass; None
    (default) leaves cost.json exactly as the fix flow wrote it.
    """
    info = cost_ledger.compute_run_cost(run_dir)
    if cycles:
        info["cycles_before"] = cycles.get("cycles_before")
        info["cycles_after"] = cycles.get("cycles_after")
        info["cycles_saved"] = cycles.get("cycles_saved", 0)
    with open(os.path.join(run_dir, "cost.json"), "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps(info, indent=2) + "\n")
    return info


def ensure_dirs(run_dir: str) -> None:
    for sub in ("artifacts", "api"):
        os.makedirs(os.path.join(run_dir, sub), exist_ok=True)


# --------------------------------------------------------------------------
# RED: sim/sim.py BEFORE
# --------------------------------------------------------------------------

def run_sim_before(case: str, seed: int, boot_path: str,
                   verdict_path: str) -> tuple:
    """Run the BEFORE sim via sim/sim.py subprocess. Returns (rc, stdout)."""
    cmd = [sys.executable, os.path.join(ROOT, "sim", "sim.py"),
           "--case", case, "--seed", str(seed),
           "--boot", boot_path, "--verdict", verdict_path]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=1200)
    return proc.returncode, proc.stdout


# --------------------------------------------------------------------------
# DIAGNOSE: live Muse Spark Text
# --------------------------------------------------------------------------

def text_call(full_prompt: str, timeout: int = TEXT_TIMEOUT_S) -> dict:
    """Single live Text call. Returns parsed envelope or raises LiveAPIError.

    Envelope: {content, raw, latency_ms, request_id, model, usage}.
    Empty content (including reasoning-only responses) raises -- loudly.
    """
    key = api_key()  # missing key fails BEFORE any bundle write confusion
    url = text_url()
    payload = {
        "model": TEXT_MODEL,
        "messages": [
            {"role": "system",
             "content": "You are Afterglow, a Muse Spark RTL-debug agent. "
                        "Output JSON only, exactly the requested object."},
            {"role": "user", "content": full_prompt},
        ],
        "max_tokens": TEXT_MAX_TOKENS,
        "temperature": 0.2,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "Accept": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
            headers = dict(resp.headers.items())
            status = resp.status
    except Exception as exc:
        raise LiveAPIError("live text call failed (down/timeout): %r" % (exc,))
    latency_ms = int((time.monotonic() - t0) * 1000)
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise LiveAPIError(
            "live text call returned non-JSON (status %s): %r" % (status, exc))
    if not isinstance(parsed, dict):
        raise LiveAPIError("live text call returned non-object JSON")
    err = parsed.get("error")
    if err:
        raise LiveAPIError("live text API error: %s" % (json.dumps(err)[:500],))
    choices = parsed.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        if not content and msg.get("reasoning_content"):
            raise LiveAPIError(
                "live text call returned reasoning-only with empty content; "
                "max_tokens=%d was insufficient or model refused" % TEXT_MAX_TOKENS)
        if isinstance(content, list):  # content-block style payloads
            chunks = []
            for block in content:
                if isinstance(block, dict) and isinstance(
                        block.get("text"), str):
                    chunks.append(block["text"])
            content = "".join(chunks)
    if not (isinstance(content, str) and content.strip()):
        raise LiveAPIError(
            "live text call returned EMPTY content (latency %dms, "
            "request %s); refusing to invent a diagnosis"
            % (latency_ms, parsed.get("id", "?")))
    usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    request_id = str(parsed.get("id") or parsed.get("request_id")
                     or headers.get("x-request-id", ""))
    return {"content": content, "raw": parsed, "latency_ms": latency_ms,
            "request_id": request_id,
            "model": str(parsed.get("model") or TEXT_MODEL), "usage": usage}


TRANSPORT_RETRY_BASES_S = (5.0, 15.0)  # waits before attempt 2 and 3


def call_text_with_backoff(prompt: str, call_fn=None, sleep_fn=None,
                           rand_fn=None) -> tuple:
    """Transport-flake retry around one live Text call.

    A LiveAPIError (down/timeout/empty content) is a flake until proven
    otherwise: retry inside the attempt budget with backoff+jitter, log
    every wait, and raise only after MAX_ATTEMPTS failures. Fails loud
    and bounded -- never infinite, never silent, never heuristic.
    Returns (call_envelope, waits_s).
    """
    call_fn = call_fn or text_call
    sleep_fn = sleep_fn or time.sleep
    rand_fn = rand_fn or random.uniform
    waits: list = []
    last_exc: Exception = LiveAPIError("unreachable: no attempts ran")
    for i in range(MAX_ATTEMPTS):
        try:
            return call_fn(prompt), waits
        except LiveAPIError as exc:
            last_exc = exc
            if i < MAX_ATTEMPTS - 1:
                wait = TRANSPORT_RETRY_BASES_S[i] + rand_fn(
                    0, TRANSPORT_RETRY_BASES_S[i])
                waits.append(wait)
                print("[DIAGNOSE] transport flake on call %d of %d: %s; "
                      "retrying in %.1fs" % (i + 1, MAX_ATTEMPTS, exc, wait),
                      flush=True)
                sleep_fn(wait)
    raise last_exc


def extract_model_json(content: str) -> dict:
    """Parse the model's JSON-only output (tolerates ``` fences)."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise LoopError("model output is not a JSON object: %r"
                        % (content[:200],))
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise LoopError("model output JSON parse failed: %s" % (exc,))
    if not isinstance(obj, dict):
        raise LoopError("model output JSON is not an object")
    if not str(obj.get("diagnosis", "")).strip():
        raise LoopError("model output missing non-empty 'diagnosis'")
    if not str(obj.get("patch_diff", "")).strip():
        raise LoopError("model output missing non-empty 'patch_diff'")
    return obj


# --------------------------------------------------------------------------
# PATCH helpers: repair paths, guard, patch -p1 dry-run/apply
# --------------------------------------------------------------------------

def find_tree_file(tree: str, target: str) -> str | None:
    """Resolve a diff target to a tree-relative path (basename fallback)."""
    cand = os.path.join(tree, target)
    if os.path.isfile(cand):
        return target
    base = os.path.basename(target)
    hits = []
    for dirpath, _dirnames, filenames in os.walk(tree):
        if base in filenames:
            hits.append(os.path.relpath(os.path.join(dirpath, base), tree))
    # ignore verilator build dirs
    hits = [h for h in hits if "obj_afterglow_" not in h]
    if len(hits) == 1:
        return hits[0]
    return None


def repair_diff_paths(diff_text: str, tree: str) -> tuple:
    """Rewrite diff header paths to tree-relative paths by basename search.

    Mechanical path repair only (content untouched). Returns
    (diff_text, repaired_flag, mapping).
    """
    mapping = {}
    for target in guard.diff_files(diff_text):
        resolved = find_tree_file(tree, target)
        if resolved and resolved != target:
            mapping[target] = resolved
    if not mapping:
        return diff_text, False, {}
    out = []
    for line in diff_text.splitlines():
        for old, new in mapping.items():
            if line.startswith("--- ") and old in line:
                line = line.replace(old, new)
            elif line.startswith("+++ ") and old in line:
                line = line.replace(old, new)
            elif line.startswith("diff --git"):
                line = line.replace("a/" + old, "a/" + new).replace(
                    "b/" + old, "b/" + new)
        out.append(line)
    return "\n".join(out) + "\n", True, mapping


def patch_dry_run(tree: str, diff_path: str) -> tuple:
    proc = subprocess.run(
        ["patch", "-p1", "--dry-run", "--force", "-i", diff_path],
        cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=120)
    return proc.returncode, proc.stdout


def patch_apply(tree: str, diff_path: str) -> tuple:
    proc = subprocess.run(
        ["patch", "-p1", "--force", "-i", diff_path],
        cwd=tree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=120)
    return proc.returncode, proc.stdout


def try_apply_diff(diff_text: str, tree: str, tmp_path: str,
                    spec: "CaseSpec | None" = None) -> dict:
    """Guard -> repair -> dry-run -> apply. Returns outcome dict.

    When a Task-1 CaseSpec is supplied, the check goes through
    afterglow_core.policy.check_diff (per-project allowlist/lint_cmd,
    fail-closed on empty oracle_pass, honoring guard's verdict); with
    spec=None it falls back to agent.guard.check directly. The repair +
    re-check flow is identical either way.
    """
    out: dict = {"guard_ok": False, "reasons": [], "applied": False,
                 "repaired": False, "patch_lines": 0, "files": []}
    if spec is not None:
        ok, reasons, info = policy_check_diff(diff_text, spec, tree)
    else:
        ok, reasons, info = guard.check(diff_text, rtl_root=tree)
    out["guard_ok"] = ok
    out["reasons"] = reasons
    out["patch_lines"] = info.get("patch_lines", 0)
    out["files"] = info.get("files", [])
    if not ok:
        return out
    diff_text2, repaired, mapping = repair_diff_paths(diff_text, tree)
    out["repaired"] = repaired
    out["mapping"] = mapping
    if repaired:
        if spec is not None:
            ok2, reasons2, info2 = policy_check_diff(diff_text2, spec, tree)
        else:
            ok2, reasons2, info2 = guard.check(diff_text2, rtl_root=tree)
        if not ok2:
            out["guard_ok"] = False
            out["reasons"] = ["path-repair broke guard: %s" % reasons2]
            return out
        out["patch_lines"] = info2.get("patch_lines", out["patch_lines"])
        out["files"] = info2.get("files", out["files"])
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(diff_text2 if repaired else diff_text)
    rc, log = patch_dry_run(tree, tmp_path)
    out["dry_run_log"] = log
    if rc != 0:
        out["reasons"] = ["patch -p1 dry-run failed (rc=%d): %s"
                          % (rc, log[-800:])]
        return out
    rc, log = patch_apply(tree, tmp_path)
    out["apply_log"] = log
    if rc != 0:
        out["reasons"] = ["patch -p1 apply failed (rc=%d): %s"
                          % (rc, log[-800:])]
        return out
    # post-apply lint: every touched file must still be sane
    for rel in out["files"]:
        resolved = find_tree_file(tree, rel) or rel
        ok_lint, why = guard.lint_rtl(os.path.join(tree, resolved))
        if not ok_lint:
            out["reasons"] = ["post-apply lint failed: %s" % why]
            return out
    out["applied_diff"] = diff_text2 if repaired else diff_text
    out["applied"] = True
    return out


# --------------------------------------------------------------------------
# Bootstrap gated applier: canonical restore, ONLY on live gate signal
# --------------------------------------------------------------------------

def gate_hit(diagnosis: str, gate: str) -> str:
    """Return the matched gate keyword, or '' when diagnosis names none.

    The diagnosis must come from the LIVE text call. No keyword match means
    no bootstrap -- the run goes RED instead of guessing.
    """
    diag = (diagnosis or "").lower()
    for word in (gate or "").lower().split():
        if len(word) >= 3 and word in diag:
            return word
    return ""


def canonical_diff(tree: str, case: dict) -> tuple:
    """Line-anchored canonical restore diff (bug line -> anchor line).

    Returns (diff_text, hits). Raises LoopError when the bug line is absent.
    Mutant cases (mutant_file/mutant_line present) restore ONLY the pinned
    line: e.g. mut-pilot-01's bug "1: begin" already exists as pristine
    line 83, so an anchored replace-every-match would corrupt a healthy
    line alongside the fix. Normal cases keep the legacy anchored loop.
    """
    anchor = case["anchor"].strip()
    bug = case["bug"].strip()
    pinned = None
    if "mutant_file" in case and "mutant_line" in case:
        try:
            pinned = (str(case["mutant_file"]), int(case["mutant_line"]))
        except (TypeError, ValueError):
            raise LoopError("bootstrap: bad mutant pin %r/%r"
                            % (case.get("mutant_file"),
                               case.get("mutant_line")))
    blocks = []
    total_hits = 0
    for rel in case["files"]:
        if pinned is not None and rel != pinned[0]:
            continue
        path = os.path.join(tree, rel)
        with open(path, "r", encoding="utf-8") as fh:
            old_lines = fh.read().splitlines(keepends=True)
        new_lines = []
        hits = 0
        for i, ln in enumerate(old_lines):
            body = ln.rstrip("\n")
            if body.strip() == bug and (pinned is None
                                        or (rel, i + 1) == pinned):
                indent = body[:len(body) - len(body.lstrip())]
                new_lines.append("%s%s\n" % (indent, anchor))
                hits += 1
            else:
                new_lines.append(ln)
        if hits == 0:
            continue
        total_hits += hits
        ud = difflib.unified_diff(
            "".join(old_lines).splitlines(keepends=True),
            "".join(new_lines).splitlines(keepends=True),
            fromfile="a/" + rel, tofile="b/" + rel)
        blocks.append("diff --git a/%s b/%s\n%s" % (rel, rel, "".join(ud)))
    if total_hits == 0:
        raise LoopError("bootstrap: bug line not found in tree "
                        "(already fixed or unexpected tree state)")
    return "".join(blocks), total_hits


# --------------------------------------------------------------------------
# RESIM: rebuild + rerun WITHOUT re-inject (run_case would wipe the patch)
# --------------------------------------------------------------------------

def resim_after(tree: str, case: dict, seed: int, boot_path: str,
                verdict_path: str) -> dict:
    """Build the patched tree and rerun. Same oracle math as run_case."""
    binary = vbackend.build(tree, case)  # raises loudly on build failure
    try:
        rc, log = vbackend.sh([str(binary), "+seed=%d" % seed],
                              cwd=str(tree), timeout=300)
    except subprocess.TimeoutExpired:
        log = "- TIMEOUT after 300s\ntohost: FAIL 0xdead\nTRAP: sim timeout\n"
    with open(boot_path, "w", encoding="utf-8") as fh:
        fh.write(log)
    lines = log.splitlines()
    det = [ln for ln in lines if not ln.startswith("- ")]
    checksum = hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()
    tohost = None
    for ln in lines:
        if "tohost:" in ln:
            tohost = ln.split("tohost:", 1)[1].strip()
    passed = case["oracle_pass"] in log
    m = re.search(r"CYCLES=(\d+)", log)
    cycles = int(m.group(1)) if m else None
    verdict = {"case": case["name"], "pass": bool(passed), "tohost": tohost,
               "checksum": checksum, "cycles": cycles, "seed": int(seed),
               "sim_time_utc": utc_now()}
    with open(verdict_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(verdict, indent=2) + "\n")
    return verdict


# --------------------------------------------------------------------------
# OPTIMIZE-mode (fix+opt): ONE behavior-preserving optimize pass after GREEN
# --------------------------------------------------------------------------

def opt_prompt_path() -> str:
    """Path of the optimize prompt variant (v1/v2/v3 are never used here)."""
    return os.path.join(AGENT_DIR, "prompts", "%s.txt" % OPT_PROMPT_NAME)


def build_opt_prompt(case_id: str, rtl_rel: str, excerpt: str,
                     trace_block: str, verdict_text: str,
                     base_verdict: dict) -> tuple:
    """Assemble the ONE optimize prompt (token-capped like collection).

    Substitutes the v4opt template and asserts the <=2500-token cap;
    raises LoopError (missing template) or AssertionError (cap blown)
    loudly -- the caller records a failed opt and the GREEN fix stands.
    Returns (full_prompt, counts).
    """
    try:
        with open(opt_prompt_path(), encoding="utf-8") as fh:
            template = fh.read()
    except OSError as exc:
        raise LoopError("cannot read opt prompt %s: %s"
                        % (opt_prompt_path(), exc))
    baseline = (
        "tohost=%s checksum=%s SIMULATED cycles=%s\n%s"
        % (base_verdict.get("tohost"), base_verdict.get("checksum"),
           base_verdict.get("cycles"), OPT_DISCLAIMER))
    prompt = (template.replace("{{CASE}}", str(case_id)).replace(
        "{{RTL_FILE}}", str(rtl_rel)).replace(
        "{{RTL_EXCERPT}}", str(excerpt)).replace(
        "{{OPT_BASELINE}}", baseline).replace(
        "{{TRACE}}", str(trace_block)).replace(
        "{{VERDICT}}", str(verdict_text)))
    total = collect.approx_tokens(prompt)
    assert total <= collect.MAX_PROMPT_TOKENS, (
        "opt prompt %d tokens exceeds cap %d"
        % (total, collect.MAX_PROMPT_TOKENS))
    counts = {"opt_prompt_tokens": total,
              "opt_prompt_sha256": sha256_text(template)}
    return prompt, counts


def evaluate_opt_result(opt_verdict: dict, base_verdict: dict) -> tuple:
    """Pure accept predicate for the optimize resim (no I/O).

    Accept ONLY when ALL hold: opt resim PASS, tohost IDENTICAL,
    checksum IDENTICAL, and SIMULATED cycles strictly lower (both
    known ints). Anything else rejects with a reason; the GREEN fix
    (base_verdict) is untouched by construction.
    Returns (accept: bool, reason: str).
    """
    if not isinstance(opt_verdict, dict) or not opt_verdict.get("pass"):
        return False, ("opt resim not PASS (tohost=%s); GREEN fix stands"
                       % ((opt_verdict or {}).get("tohost"),))
    if opt_verdict.get("tohost") != base_verdict.get("tohost"):
        return False, ("behavior-change: tohost %r != GREEN %r; "
                       "GREEN fix stands"
                       % (opt_verdict.get("tohost"),
                          base_verdict.get("tohost")))
    if opt_verdict.get("checksum") != base_verdict.get("checksum"):
        return False, ("behavior-change: checksum %r != GREEN %r; "
                       "GREEN fix stands"
                       % (opt_verdict.get("checksum"),
                          base_verdict.get("checksum")))
    before = base_verdict.get("cycles")
    after = opt_verdict.get("cycles")
    if not isinstance(before, int) or not isinstance(after, int):
        return False, ("SIMULATED cycles unknown (before=%r after=%r); "
                       "cannot prove a saving, GREEN fix stands"
                       % (before, after))
    if after >= before:
        return False, ("no SIMULATED-cycle saving (before=%d after=%d); "
                       "GREEN fix stands" % (before, after))
    return True, ("saved %d SIMULATED cycles (before=%d after=%d); "
                  "no hardware claim" % (before - after, before, after))


def parse_opt_output(content: str) -> tuple:
    """Parse ONE optimize model output (parse-tolerant, accept-strict).

    Returns (model_obj, parse_note) with model_obj holding non-empty
    "diagnosis" and "patch_diff" (plus "expected", possibly ""). Raises
    LoopError on garbage -- the caller records failed and the GREEN fix
    stands, never ungreened.

    Pass 1 (strict): extract_model_json -- bare JSON, ``` fences OK.
    Pass 2 (tolerant): prose + ONE ```/```diff fenced unified-diff
    block. The diagnosis is the prose outside the fences; the patch
    candidate is the fenced block verbatim. The candidate still flows
    through the SAME guard (try_apply_diff) + SAME equivalence gate
    (evaluate_opt_result: PASS + identical tohost/checksum + strictly
    lower SIMULATED cycles) downstream -- tolerance is in parsing only,
    never in acceptance. Anything without a recoverable diff (truncated
    JSON, empty output, prose with no diff block) raises.
    """
    if content is None or not str(content).strip():
        raise LoopError("empty opt output: no diagnosis, no diff; "
                        "refusing to invent a patch")
    text = str(content)
    try:
        return extract_model_json(text), "strict-json"
    except LoopError as strict_exc:
        strict_reason = str(strict_exc)
    blocks = [m.group(2) for m in
              re.finditer(r"```(\w*)[ \t]*\r?\n(.*?)```", text, re.DOTALL)]
    candidate = ""
    for block in blocks:
        stripped = block.strip()
        if "diff --git" in stripped:
            candidate = stripped
            break
        if ("@@" in stripped and "---" in stripped
                and "+++" in stripped and not candidate):
            candidate = stripped
    if not candidate:
        raise LoopError("no unified-diff block found (%d fenced blocks "
                        "scanned); strict parse failed: %s"
                        % (len(blocks), strict_reason))
    prose = re.sub(r"```(\w*)[ \t]*\r?\n.*?```", "", text,
                   flags=re.DOTALL).strip()
    if not prose:
        raise LoopError("no prose diagnosis outside the fenced diff block; "
                        "refusing to invent one")
    return ({"diagnosis": prose, "patch_diff": candidate, "expected": ""},
            "tolerant-fenced-diff (%d fenced blocks scanned)"
            % (len(blocks),))


def run_optimize_pass(run_dir: str, tree: str, case: dict, spec,
                      seed: int, base_verdict: dict, trace_block: str,
                      green_verdict_text: str, rtl_rel: str) -> dict:
    """Run ONE optimize pass after a GREEN fix; never raises, never ungreens.

    Guard + resim gates mirror the fix flow (same try_apply_diff with the
    same spec, resim into boot_opt.log/verdict_opt.json so the fix proof
    bundle is untouched). Any failure or behavior change records
    rejected_opt.diff (guard reject) or the applied-but-rejected diff and
    returns status rejected/failed with cycles_saved 0 -- the GREEN fix
    (patch.diff + boot_after.log + verdict_after.json) stands as recorded.

    Returns info dict with status (accepted/rejected/failed), reason,
    cycles_before (GREEN SIMULATED cycles), cycles_after (opt resim
    SIMULATED cycles or None when no resim ran), cycles_saved (0 unless
    accepted), patch_lines, and opt prompt proof counts.
    """
    info: dict = {"status": OPT_STATUS_FAILED, "reason": "",
                  "cycles_before": base_verdict.get("cycles"),
                  "cycles_after": None, "cycles_saved": 0,
                  "patch_lines": 0}

    def _fail(reason: str) -> dict:
        info["status"] = OPT_STATUS_FAILED
        info["reason"] = reason
        return info

    def _reject(reason: str) -> dict:
        info["status"] = OPT_STATUS_REJECTED
        info["reason"] = reason
        return info

    opt_req_path = os.path.join(run_dir, "api", "opt_text_request.json")
    opt_resp_path = os.path.join(run_dir, "api", "opt_text_response.json")
    try:
        excerpt = rtl_excerpt(tree, rtl_rel, case)
    except Exception as exc:  # noqa: BLE001 -- opt never breaks GREEN
        return _fail("opt excerpt unreadable: %s" % (exc,))
    try:
        prompt, counts = build_opt_prompt(
            case.get("name", "?"), rtl_rel, excerpt, trace_block,
            green_verdict_text, base_verdict)
    except (OSError, LoopError, AssertionError) as exc:
        return _fail("opt prompt failed: %s" % (exc,))
    info.update(counts)
    with open(opt_req_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"model": TEXT_MODEL, "endpoint": text_url(),
             "prompt": prompt,
             "prompt_sha256": counts["opt_prompt_sha256"],
             "prompt_file": opt_prompt_path(),
             "prompt_tokens": len(prompt.split()),
             "case": case.get("name"), "seed": seed,
             "max_tokens": TEXT_MAX_TOKENS,
             "goals": GOALS_FIX_OPT,
             "honesty": OPT_DISCLAIMER}, indent=2) + "\n")
    try:
        call, _waits = call_text_with_backoff(prompt)
    except LiveAPIError as exc:
        with open(opt_resp_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"error": str(exc),
                                 "status": "FAILED"}, indent=2) + "\n")
        return _fail("opt text call failed: %s" % (exc,))
    try:
        model_obj, parse_note = parse_opt_output(call["content"])
    except LoopError as exc:
        with open(opt_resp_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"error": "unparseable opt output: %s" % exc,
                                 "status": "FAILED"}, indent=2) + "\n")
        return _fail("opt output unparseable: %s" % (exc,))
    opt_diagnosis = str(model_obj["diagnosis"])
    opt_diff = str(model_obj["patch_diff"])
    with open(opt_resp_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"raw": call["raw"], "content": call["content"],
             "diagnosis": opt_diagnosis,
             "expected": str(model_obj.get("expected", "")),
             "parsed_keys": sorted(model_obj.keys()),
             "parse": parse_note,
             "latency_ms": call["latency_ms"],
             "request_id": call["request_id"],
             "model": call["model"], "usage": call["usage"],
             "honesty": OPT_DISCLAIMER}, indent=2) + "\n")
    info["opt_text_latency_ms"] = call["latency_ms"]
    info["opt_text_request_id"] = call["request_id"]
    outcome = try_apply_diff(
        opt_diff, tree, os.path.join(run_dir, "opt_attempt.diff"), spec)
    if not outcome["applied"]:
        with open(os.path.join(run_dir, "rejected_opt.diff"), "w",
                  encoding="utf-8") as fh:
            fh.write(opt_diff if opt_diff.endswith("\n")
                      else opt_diff + "\n")
        return _reject("opt guard refused: %s; GREEN fix stands"
                       % (outcome["reasons"],))
    info["patch_lines"] = outcome.get("patch_lines", 0)
    try:
        opt_verdict = resim_after(
            tree, case, seed,
            os.path.join(run_dir, "artifacts", "boot_opt.log"),
            os.path.join(run_dir, "artifacts", "verdict_opt.json"))
    except Exception as exc:  # noqa: BLE001 -- resim crash, GREEN stands
        with open(os.path.join(run_dir, "rejected_opt.diff"), "w",
                  encoding="utf-8") as fh:
            fh.write(outcome.get("applied_diff", opt_diff))
        return _fail("opt RESIM crashed: %r; GREEN fix stands" % (exc,))
    info["cycles_after"] = opt_verdict.get("cycles")
    accept, reason = evaluate_opt_result(opt_verdict, base_verdict)
    if not accept:
        with open(os.path.join(run_dir, "rejected_opt.diff"), "w",
                  encoding="utf-8") as fh:
            fh.write(outcome.get("applied_diff", opt_diff))
        return _reject("%s" % (reason,))
    with open(os.path.join(run_dir, "opt_patch.diff"), "w",
              encoding="utf-8") as fh:
        # NOTE (provenance): on accept, the working tree keeps the opt patch
        # but patch.diff/boot_after.log/verdict_after.json still record the
        # fix-only run. Read opt_patch.diff + verdict_opt.json for the opt.
        applied = outcome.get("applied_diff", opt_diff)
        fh.write(applied if applied.endswith("\n") else applied + "\n")
    info["status"] = OPT_STATUS_ACCEPTED
    info["reason"] = reason
    info["cycles_saved"] = (info["cycles_before"] - info["cycles_after"])
    return info


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------

LOOP_VERSION = "0.2.0"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Afterglow live agent loop.")
    ap.add_argument("--version", action="version",
                    version="afterglow %s" % LOOP_VERSION)
    ap.add_argument("--case", default=None,
                    help="sim target in sim/targets/<case>.yaml "
                         "(or omit when --spec is given)")
    ap.add_argument("--spec", default=None,
                    help="case YAML under sim/targets/ (in-tree only: "
                         "the stem must match sim/targets/<id>.yaml "
                         "because RED dispatches sim/sim.py --case "
                         "<stem>; unknown stems are rejected before "
                         "any sim/model work; overrides --case when "
                         "both are given -- Task 4 `afterglow run`)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--prompt", default=os.path.join(
        AGENT_DIR, "prompts", "v2.txt"),
        help="prompt template (default: agent/prompts/v2.txt; pass v3 "
             "or agent/prompts/v3.txt for the harvest pack; v1 pinned "
             "by repro/run.sh stays available)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--rtl", default=None,
                    help="override RTL file under repair (tree-relative)")
    ap.add_argument("--auto", action="store_true", default=False,
                    help="attempt PATCH->RESIM->VERIFY (this is the "
                         "default; flag kept for compatibility)")
    ap.add_argument("--triage", action="store_true", default=False,
                    help="stop after DIAGNOSE+EXPLAIN with the diagnosis "
                         "saved and the patch withheld (exit 2; "
                         "fail-closed on ambiguity with --auto)")
    ap.add_argument("--stop-before-patch", dest="stop_before_patch",
                    action="store_true", default=False,
                    help="alias for --triage: stop before PATCH (exit 2)")
    ap.add_argument("--goals", default=GOALS_FIX, choices=GOALS_CHOICES,
                    help="fix (default): fix-first flow only, unchanged; "
                         "fix+opt: after a GREEN fix, run ONE optimize "
                         "pass that must reduce SIMULATED CYCLES with "
                         "IDENTICAL checksum/tohost (rejected opt never "
                         "ungreens the fix)")
    return ap.parse_args(argv)


def bug_line_no(tree: str, rel: str, case: dict) -> int:
    """1-based line number of the case bug (fallback: anchor, then 1)."""
    path = os.path.join(tree, rel)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return 1
    bug = case["bug"].strip()
    anchor = case["anchor"].strip()
    idx = next((i for i, l in enumerate(lines) if l.strip() == bug), -1)
    if idx < 0:
        idx = next((i for i, l in enumerate(lines) if l.strip() == anchor),
                   0)
    return idx + 1


# --------------------------------------------------------------------------
# mutant live-fire (big-N pilot, option c): line-targeted fault onto a
# scratch copy of the pinned pristine tree. The in-tree-only SPEC rule is
# untouched (sanctioned sim/targets/mut-pilot-*.yaml stems exist, so the
# stem check passes unchanged) and no /tmp tree is ever trusted: the
# fault is rebuilt from the spec's own anchor/bug + pinned line.
# --------------------------------------------------------------------------

def is_mutant_case(case: dict) -> bool:
    """True iff the case pins a single-point mutant fault.

    Sanctioned mut-pilot-*.yaml specs carry mutant_file + mutant_line
    alongside the usual anchor/bug; normal eval cases lack them.
    """
    return (isinstance(case, dict) and "mutant_file" in case
            and "mutant_line" in case)


def prepare_mutant_tree(case: dict, pristine_tree: str, dest: str,
                        force: bool = False) -> dict:
    """Copy pristine_tree -> dest, apply anchor->bug at the PINNED LINE
    ONLY, verify the single-line diff. The pristine tree is never written.

    Fails loudly (LoopError) when: dest exists without force, the pinned
    file is missing, the pinned line is out of range, or the pinned
    line's stripped text is not the anchor (wrong pristine revision).
    Returns the mutation record {file, line, before, after, seed, op}.

    Line-targeted, NOT line-anchored like vbackend.inject: pilot anchors
    mut-pilot-02 ("recv_state <= 0;") and mut-pilot-05
    ("send_pattern <= ~0;") each occur TWICE in pristine simpleuart.v,
    so a replace-every-match inject would build DOUBLE faults -- the
    wrong fault distribution for big-N fix-rate stats. Only the pinned
    line is touched; the post-apply walk asserts every other line of
    every other file is byte-identical to pristine.
    """
    try:
        rel = str(case.get("mutant_file") or (case.get("files") or [""])[0])
        lineno = int(case.get("mutant_line"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise LoopError("mutant case needs mutant_file + integer "
                        "mutant_line, got %r/%r"
                        % (case.get("mutant_file"),
                           case.get("mutant_line")))
    anchor = str(case.get("anchor", "")).strip()
    bug = str(case.get("bug", "")).strip()
    if not anchor or not bug:
        raise LoopError("mutant case needs non-blank anchor+bug")
    if anchor == bug:
        raise LoopError("mutant anchor == bug (no fault to apply)")
    if lineno < 1:
        raise LoopError("mutant_line out of range: %d" % lineno)
    if os.path.exists(dest):
        if not force:
            raise LoopError("mutant scratch exists (refusing): %s" % dest)
        shutil.rmtree(dest)
    if not os.path.isdir(pristine_tree):
        raise LoopError("pristine tree absent: %s" % pristine_tree)

    def _ignore(_dir, names):
        return [n for n in names
                if n in (".git", "__pycache__")
                or n.startswith("obj_afterglow_")
                or n.endswith(".orig")]

    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)
    # Copy first; every read below happens inside dest, every write below
    # happens inside dest. The pristine tree is never opened for writing.
    shutil.copytree(pristine_tree, dest, ignore=_ignore)
    path = os.path.join(dest, rel)
    if not os.path.isfile(path):
        raise LoopError("mutant file missing in pristine tree: %s" % rel)
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if lineno > len(lines):
        raise LoopError("mutant_line %d out of range (%d lines in %s)"
                        % (lineno, len(lines), rel))
    cur = lines[lineno - 1]
    if cur.strip() != anchor:
        raise LoopError(
            "pristine %s:%d is %r, not anchor %r "
            "(wrong pristine revision? refusing)" % (rel, lineno,
                                                     cur.strip(), anchor))
    indent = cur[:len(cur) - len(cur.lstrip())]
    lines[lineno - 1] = "%s%s" % (indent, bug)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    # Verify: exactly one line differs in the pinned file, and every
    # other file is byte-identical to pristine (no cross-contamination,
    # no double-fault from duplicate anchor text elsewhere).
    with open(os.path.join(pristine_tree, rel), "r",
              encoding="utf-8") as fh:
        pristine_lines = fh.read().splitlines()
    diffs = [i for i, (a, b) in enumerate(zip(pristine_lines, lines))
             if a != b]
    if (len(pristine_lines) != len(lines) or len(diffs) != 1
            or diffs[0] != lineno - 1
            or lines[lineno - 1].strip() != bug):
        raise LoopError("mutant apply left unexpected diff in %s "
                        "(want exactly line %d -> bug)" % (rel, lineno))
    for dirpath, _dirnames, filenames in os.walk(dest):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            tree_rel = os.path.relpath(full, dest)
            if tree_rel == rel:
                continue
            with open(full, "rb") as fh:
                got = fh.read()
            with open(os.path.join(pristine_tree, tree_rel), "rb") as fh:
                want = fh.read()
            if got != want:
                raise LoopError("scratch file differs from pristine "
                                "outside the pinned fault: %s" % tree_rel)
    return {"file": rel, "line": lineno, "before": anchor, "after": bug,
            "seed": case.get("mutant_seed"), "op": case.get("mutant_op")}


PROMPT_VERSIONS = ("v1", "v2", "v3")


def resolve_prompt_file(raw: str) -> str:
    """Resolve --prompt shorthand ("v1"/"v2"/"v3") to a template path.

    Full paths pass through untouched (backward compatible with
    --prompt agent/prompts/vX.txt and repro/run.sh's explicit v1 pin).
    "v4opt" (the OPTIMIZE-mode prompt variant) resolves to
    agent/prompts/v4opt.txt; "v3opt" still resolves to the kept
    agent/prompts/v3opt.txt for old-bundle provenance; v1/v2/v3 mappings
    are untouched.
    """
    name = str(raw or "").strip()
    if name in PROMPT_VERSIONS:
        return os.path.join(AGENT_DIR, "prompts", "%s.txt" % name)
    if name in ("v1.txt", "v2.txt", "v3.txt"):
        return os.path.join(AGENT_DIR, "prompts", name)
    if name in ("v3opt", "v3opt.txt"):
        return os.path.join(AGENT_DIR, "prompts", "v3opt.txt")
    if name in ("v4opt", "v4opt.txt"):
        return os.path.join(AGENT_DIR, "prompts", "v4opt.txt")
    return str(raw)


def assemble_harvest(trace_block: str, verdict_text: str, tree: str,
                     rtl_file_list: list, runs_dir: str) -> tuple:
    """Assemble the v3 HARVEST block (fingerprint + cone slice + pack).

    Returns (harvest_text, counts, fp_record). Budgets are enforced
    inside harvest_pack.build_v3_pack (200 lines / 2500 tokens hard
    caps, PACK_BUDGET_TOKENS scored merge); the advisory recall section
    below is dropped first under pressure and is never load-bearing.
    Fix-memory recall is ADVISORY ONLY: similar failure signatures with
    Jaccard scores, no patch text -- re-sim decides, recall can never
    override the guard (which only ever sees diffs).
    """
    fp_record = harvest_fp.fingerprint_for_failure(trace_block,
                                                   verdict_text)
    signals = list(fp_record.get("signals") or [])
    seed = signals[0] if signals else ""
    if seed:
        cone_lines, cone_report = harvest_slice.backward_slice(
            tree, seed, list(rtl_file_list or []))
    else:
        cone_lines, cone_report = [], {
            "seed": "(none)", "resolved": 0, "sought": 0,
            "unparsed": 0, "lines": 0, "files": []}
    pack_text, pack_counts = harvest_pack.build_v3_pack(
        fp_record, cone_lines, cone_report, verdict_text)
    try:
        recalled = fix_memory.recall_similar(signals, runs_dir, k=2)
    except Exception:  # recall is advisory: never break collection
        recalled = []
    recall_rows = []
    recall_lines = []
    for run_id, score, rec in recalled or []:
        if score <= 0:
            continue
        recall_rows.append((run_id, round(float(score), 4)))
        recall_lines.append(
            "- %s (jaccard %.2f): trap `%s` | oracle `%s`" % (
                run_id, float(score),
                str(rec.get("trap_line", ""))[:160],
                str(rec.get("oracle_sig", ""))[:160]))
    harvest_text = pack_text
    recall_dropped = False
    if recall_lines:
        advisory = (
            "### PAST_FAILURES [advisory only -- similar fingerprints "
            "from our own bundles; patterns, not answers; re-sim "
            "decides; never overrides guard]\n" + "\n".join(recall_lines))
        if harvest_pack.approx_tokens(
                harvest_text + "\n\n" + advisory
                ) <= harvest_pack.PACK_BUDGET_TOKENS:
            harvest_text = harvest_text + "\n\n" + advisory
        else:
            recall_dropped = True  # scores still logged in counts below
    counts = dict(pack_counts)
    counts.update({
        "seed": seed,
        "cone_files": len(cone_report.get("files", [])),
        "harvest_lines": len(harvest_text.splitlines()),
        "harvest_tokens": harvest_pack.approx_tokens(harvest_text),
        "recall": recall_rows,
        "recall_dropped": recall_dropped,
    })
    return harvest_text, counts, fp_record


def rtl_files(tree: str, cap: int = 25) -> list:
    """Tree-relative .v/.sv files for localization, sorted, capped."""
    found = []
    for root, _dirs, files in os.walk(tree):
        for fn in sorted(files):
            if fn.endswith(".v") or fn.endswith(".sv"):
                found.append(os.path.relpath(os.path.join(root, fn), tree))
    return sorted(found)[:cap]


def rtl_excerpt(tree: str, rel: str, case: dict, max_lines: int = 40) -> str:
    """<=40 line-numbered lines around the injected bug line."""
    path = os.path.join(tree, rel)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        raise LoopError("cannot read RTL for excerpt: %s" % (exc,))
    bug = case["bug"].strip()
    anchor = case["anchor"].strip()
    idx = next((i for i, ln in enumerate(lines) if ln.strip() == bug), -1)
    if idx < 0:
        idx = next((i for i, ln in enumerate(lines) if ln.strip() == anchor),
                   0)
    half = max_lines // 2
    start = max(0, min(idx - half, len(lines) - max_lines))
    window = lines[start:start + max_lines]
    return "\n".join("line %d: %s" % (start + i + 1, ln)
                     for i, ln in enumerate(window))


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.case and not args.spec:
        print("FATAL: need --case <id> or --spec <path>",
              file=sys.stderr)
        return 2
    if args.spec:
        # --spec names the exact file: normalize --case to its stem so
        # every downstream use (run-id, bundle meta, sim dispatch) keeps
        # working unchanged; the spec source is recorded in meta below.
        if args.case:
            print("note: --spec %s overrides --case %s"
                  % (args.spec, args.case), flush=True)
        args.case = os.path.splitext(os.path.basename(args.spec))[0]
    wall_start = utc_now()
    t_start = time.monotonic()
    run_id = args.run_id or ("%s-%s" % (
        args.case, datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ")))
    run_dir = os.path.join(ROOT, "runs", run_id)
    ensure_dirs(run_dir)
    art = lambda *p: os.path.join(run_dir, "artifacts", *p)  # noqa: E731
    api = lambda *p: os.path.join(run_dir, "api", *p)  # noqa: E731

    meta: dict = {
        "run_id": run_id, "case_id": args.case, "case": args.case,
        "mode": resolve_mode(args), "status": "red", "retries": 0,
        "human_touches": 0,
        "wall_start_utc": wall_start, "wall_end_utc": "",
        "sim_seed": args.seed, "prompt_file": args.prompt,
        "model_id": TEXT_MODEL, "image_model": visual.IMAGE_MODEL,
        "diagnosed": False, "fixed_unaided": False,
        "patch_lines": 0, "patch_source": "none", "explain": "MISSING",
        "attempts": [],
        "goals": getattr(args, "goals", GOALS_FIX),
        "opt_status": OPT_STATUS_SKIPPED,
    }

    def finish(status: str, extra: dict | None = None) -> int:
        meta["status"] = status
        meta["wall_end_utc"] = utc_now()
        meta["time_to_green_s"] = round(time.monotonic() - t_start, 1)
        if extra:
            meta.update(extra)
        try:
            # Ledger BEFORE meta write so meta carries the headline number.
            # Cost must never mask the verdict: tolerate to zero on error.
            # OPTIMIZE-mode: ledger the SIMULATED-cycle comparison too.
            opt_cycles = None
            if meta.get("opt_status") in (OPT_STATUS_ACCEPTED,
                                           OPT_STATUS_REJECTED,
                                           OPT_STATUS_FAILED):
                opt_cycles = {
                    # before = GREEN-fix baseline, after = final
                    # effective cycles (opt value only when accepted,
                    # else the GREEN-fix value stands).
                    "cycles_before": meta.get("opt_cycles_before"),
                    "cycles_after": meta.get("cycles_after"),
                    "cycles_saved": meta.get("cycles_saved", 0)}
            cost_info = write_cost_json(run_dir, cycles=opt_cycles)
            meta["est_usd"] = cost_info["est_usd"]
            meta["text_tokens"] = cost_info["text_tokens"]
            meta["image_calls"] = cost_info["image_calls"]
        except Exception as exc:  # noqa: BLE001
            print("cost ledger failed (non-fatal): %r" % (exc,),
                  file=sys.stderr)
        with open(os.path.join(run_dir, "meta.json"), "w",
                  encoding="utf-8") as fh:
            fh.write(json.dumps(meta, indent=2) + "\n")
        try:
            report.build_report(run_dir)
        except Exception as exc:  # report must never mask the verdict
            print("report build failed (non-fatal): %r" % (exc,),
                  file=sys.stderr)
        if status == "green":
            return EXIT_GREEN
        if status == "triage":
            return EXIT_TRIAGE
        return EXIT_RED

    # ---- case + tree location (no ensure_tree: it would reset the inject)
    try:
        if args.spec:
            case = load_spec_case(args.spec)
        else:
            case = vbackend.load_case(args.case)
    except Exception as exc:
        print("FATAL: unknown case %r: %s" % (args.case, exc), file=sys.stderr)
        meta["error"] = "unknown case: %s" % (exc,)
        return finish("red")
    if args.spec:
        meta["case_source"] = "spec"
        meta["spec_path"] = os.path.abspath(args.spec)
        # In-tree-only --spec (Task 4 fix): the tree/patch/policy content
        # above comes from the spec file, but RED always dispatches
        # sim/sim.py --case <stem>. A stem with no sim/targets/<stem>.yaml
        # would RED the wrong case (or crash mid-pipeline), so reject it
        # here -- before any sim build or model call.
        stem_yaml = os.path.join(
            str(vbackend.SIMDIR), "targets", args.case + ".yaml")
        if not os.path.isfile(stem_yaml):
            meta["error"] = (
                "unknown --spec stem %r: no %s (--spec is in-tree-only; "
                "RED dispatches sim/sim.py --case <stem>)" % (
                    args.case, stem_yaml))
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            return finish("red")
    pins = vbackend.load_pins()
    if case["project"] not in pins:
        meta["error"] = "unknown project %r" % (case["project"],)
        print("FATAL: %s" % meta["error"], file=sys.stderr)
        return finish("red")
    _url, sha = pins[case["project"]]
    tree = str(vbackend.tree_dir(case["project"], sha))
    meta["sim_project"] = case["project"]
    meta["sim_tree_sha"] = sha
    mutant_record = None
    if is_mutant_case(case):
        # Mutant live-fire: the pinned pristine tree is NEVER written.
        # The fault is applied line-targeted onto a scratch copy (no
        # ensure_tree here -- it would need a network fetch; the pinned
        # tree must already exist, as for any loop run).
        pristine_tree = tree
        if not os.path.isdir(pristine_tree):
            meta["error"] = (
                "mutant case %s needs pinned pristine tree %s "
                "(absent; restore it with network access, e.g. run the "
                "parent-case sim once, then refire)" % (
                    args.case, pristine_tree))
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            return finish("red")
        scratch = os.path.join(ROOT, "runs", "scratch", run_id)
        try:
            mutant_record = prepare_mutant_tree(
                case, pristine_tree, scratch)
        except LoopError as exc:
            meta["error"] = "mutant prep failed: %s" % (exc,)
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            return finish("red")
        tree = scratch
        meta["mutant_tree"] = tree
        meta["mutant_record"] = mutant_record
        meta["pristine_tree"] = pristine_tree
    rtl_rel = args.rtl or case["files"][0]
    meta["rtl_file"] = rtl_rel
    spec = case_to_spec(case)
    meta["spec_case_id"] = spec.case_id
    meta["spec_oracle_pass"] = spec.oracle_pass

    # ---- RED: sim/sim.py BEFORE (mutants: scratch tree, same schema)
    if mutant_record is not None:
        print("[RED] mutant scratch %s --seed %d" % (tree, args.seed),
              flush=True)
        try:
            verdict_before = resim_after(
                tree, case, args.seed,
                art("boot.log"), art("verdict_before.json"))
        except Exception as exc:
            meta["error"] = "BEFORE sim crashed: %r" % (exc,)
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            return finish("red")
        rc_before = 0 if verdict_before.get("pass") else 1
        sim_out = ("%s %s tohost=%s checksum=%s cycles=%s seed=%s "
                   "(mutant scratch x1)" % (
                       args.case, "PASS" if rc_before == 0 else "RED",
                       verdict_before.get("tohost"),
                       (verdict_before.get("checksum") or "")[:12],
                       verdict_before.get("cycles"), args.seed))
    else:
        print("[RED] sim/sim.py --case %s --seed %d" % (args.case, args.seed),
              flush=True)
        try:
            rc_before, sim_out = run_sim_before(
                args.case, args.seed, art("boot.log"), art("verdict_before.json"))
        except Exception as exc:
            meta["error"] = "BEFORE sim crashed: %r" % (exc,)
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            return finish("red")
    print(sim_out, flush=True)
    try:
        with open(art("verdict_before.json"), encoding="utf-8") as fh:
            verdict_before = json.load(fh)
    except (OSError, ValueError) as exc:
        meta["error"] = "BEFORE verdict unreadable: %s" % (exc,)
        print("FATAL: %s" % meta["error"], file=sys.stderr)
        return finish("red")
    meta["tohost_before"] = verdict_before.get("tohost")
    meta["checksum_before"] = verdict_before.get("checksum")
    meta["cycles_before"] = verdict_before.get("cycles")
    before_verdict = Verdict(
        **{"pass": bool(verdict_before.get("pass")),
           "evidence": {"tohost": verdict_before.get("tohost"),
                        "checksum": verdict_before.get("checksum"),
                        "source": "verdict_before.json"}})
    if getattr(before_verdict, "pass"):
        meta["error"] = ("BEFORE run already PASS (nothing to diagnose); "
                         "inject may not reproduce this case")
        print("FATAL: %s" % meta["error"], file=sys.stderr)
        return finish("red")

    # ---- COLLECT
    args.prompt = resolve_prompt_file(args.prompt)
    try:
        with open(args.prompt, encoding="utf-8") as fh:
            template = fh.read()
    except OSError as exc:
        meta["error"] = "cannot read prompt %s: %s" % (args.prompt, exc)
        print("FATAL: %s" % meta["error"], file=sys.stderr)
        return finish("red")
    prompt_hash = sha256_text(template)
    meta["prompt_hash"] = prompt_hash
    harvest_info: dict | None = None
    try:
        with open(art("boot.log"), encoding="utf-8") as fh:
            boot_log = fh.read()
        with open(art("verdict_before.json"), encoding="utf-8") as fh:
            verdict_json = fh.read()
        excerpt = rtl_excerpt(tree, rtl_rel, case)
        staged = template.replace("{{CASE}}", args.case).replace(
            "{{RTL_FILE}}", rtl_rel).replace("{{RTL_EXCERPT}}", excerpt)
        bundle = collect.collect(boot_log, verdict_json, staged)
        if "{{HARVEST}}" in bundle["full_prompt"]:
            # prompt v3: harvest pack (fingerprint + cone slice + verdict,
            # budgets enforced inside harvest_pack). v3 is opt-in via
            # --prompt v3; the default stays v2 until ablation crowns a
            # winner (no silent default change). Any harvest exception
            # degrades to v2 collection -- the loop never dies on harvest
            # (cost-hook pattern: non-fatal, old bundles untouched).
            try:
                harvest_text, harvest_counts, fp_record = assemble_harvest(
                    bundle["trace_block"], verdict_json, tree,
                    rtl_files(tree), os.path.join(ROOT, "runs"))
                # NEW bundle only (fix_memory.write_fingerprint creates
                # parent dirs for run_dir; old bundles are never touched).
                fix_memory.write_fingerprint(run_dir, fp_record)
                full_with_harvest = bundle["full_prompt"].replace(
                    "{{HARVEST}}", harvest_text)
                total = collect.approx_tokens(full_with_harvest)
                assert total <= collect.MAX_PROMPT_TOKENS, (
                    "prompt v3 with harvest pack %d tokens exceeds cap %d"
                    % (total, collect.MAX_PROMPT_TOKENS))
                bundle = dict(bundle, full_prompt=full_with_harvest)
                bundle["counts"] = dict(
                    bundle["counts"], prompt_tokens=total,
                    harvest_tokens=harvest_counts["pack_tokens"],
                    harvest_dropped=harvest_counts["dropped"])
                harvest_info = dict(harvest_counts, status="ok")
            except Exception as exc:  # noqa: BLE001 -- degrade, never die
                print("[COLLECT] harvest failed (%r); degrading to v2 "
                      "collection" % (exc,), flush=True)
                fallback = "HARVEST FAILED (%s)" % (exc,)
                try:
                    v2_pack, v2_counts = context_pack.build_pack(
                        bundle["trace_block"], tree, rtl_files(tree),
                        os.path.join(ROOT, "runs"),
                        bug_line_no(tree, rtl_rel, case), rtl_rel)
                    if v2_pack:
                        fallback = (
                            "HARVEST FAILED (%s); degraded to v2-style "
                            "context:\n%s" % (exc, v2_pack))
                except Exception as exc2:  # noqa: BLE001 -- v2 also failed
                    v2_counts = {}
                    fallback = ("HARVEST FAILED (%s; v2 fallback also "
                                "failed: %s)" % (exc, exc2))
                cand = bundle["full_prompt"].replace("{{HARVEST}}",
                                                     fallback)
                if collect.approx_tokens(cand) > collect.MAX_PROMPT_TOKENS:
                    # Drop the v2 pack under pressure: the FAILED marker
                    # alone always fits wherever the v1 baseline fits.
                    fallback = "HARVEST FAILED (%s)" % (exc,)
                    cand = bundle["full_prompt"].replace("{{HARVEST}}",
                                                         fallback)
                total = collect.approx_tokens(cand)
                assert total <= collect.MAX_PROMPT_TOKENS, (
                    "prompt v3 degraded fallback %d tokens exceeds cap %d"
                    % (total, collect.MAX_PROMPT_TOKENS))
                bundle = dict(bundle, full_prompt=cand)
                bundle["counts"] = dict(bundle["counts"],
                                        prompt_tokens=total)
                harvest_info = {"status": "FAILED",
                                "error": str(exc)[:200],
                                "fallback": "v2-context",
                                "fingerprint": "",
                                "cone_lines": 0, "cone_files": 0,
                                "pack_lines": 0, "pack_tokens": 0}
        elif "{{CONTEXT}}" in bundle["full_prompt"]:
            # prompt v2 (default): ground the model with the budgeted
            # context pack.
            pack, pack_counts = context_pack.build_pack(
                bundle["trace_block"], tree, rtl_files(tree),
                os.path.join(ROOT, "runs"),
                bug_line_no(tree, rtl_rel, case), rtl_rel)
            full_with_pack = bundle["full_prompt"].replace(
                "{{CONTEXT}}", pack)
            total = collect.approx_tokens(full_with_pack)
            assert total <= collect.MAX_PROMPT_TOKENS, (
                "prompt v2 with context pack %d tokens exceeds cap %d"
                % (total, collect.MAX_PROMPT_TOKENS))
            bundle = dict(bundle, full_prompt=full_with_pack)
            bundle["counts"] = dict(
                bundle["counts"], prompt_tokens=total,
                context_tokens=pack_counts["pack_tokens"],
                context_dropped=pack_counts["dropped"],
                localization_top1=pack_counts["top1_file"],
                top1_is_true=pack_counts["top1_is_true"],
                n_signals=pack_counts["n_signals"])
    except AssertionError as exc:
        meta["error"] = "collector cap violated (fail loudly): %s" % (exc,)
        print("FATAL: %s" % meta["error"], file=sys.stderr)
        return finish("red")
    except (OSError, LoopError) as exc:
        meta["error"] = "COLLECT failed: %s" % (exc,)
        print("FATAL: %s" % meta["error"], file=sys.stderr)
        return finish("red")
    if "{{OPT_BASELINE}}" in staged:
        prompt_version = OPT_PROMPT_NAME
    elif "{{HARVEST}}" in staged:
        prompt_version = "v3"
    elif "{{CONTEXT}}" in staged:
        prompt_version = "v2"
    else:
        prompt_version = "v1"
    meta["prompt_version"] = prompt_version
    meta["harvest"] = harvest_info if harvest_info is not None else {
        "status": "n/a"}
    full_prompt = bundle["full_prompt"]
    counts = bundle["counts"]
    with open(art("input_trace.txt"), "w", encoding="utf-8") as fh:
        fh.write(bundle["trace_block"] + "\n")
    trace_sha = sha256_file(art("input_trace.txt"))
    print("[COLLECT] lines=%d tokens=%d (trace %d lines/%d tok, prompt %d)" % (
        counts["boot_lines"], counts["prompt_tokens"],
        counts["trace_lines"], counts["trace_tokens"],
        counts["prompt_tokens"]), flush=True)
    meta.update({"trace_lines": counts["trace_lines"],
                 "trace_tokens": counts["trace_tokens"],
                 "prompt_tokens": counts["prompt_tokens"],
                 "context_tokens": counts.get("context_tokens", 0),
                 "context_dropped": counts.get("context_dropped", "n/a"),
                 "localization_top1": counts.get("localization_top1", ""),
                 "top1_is_true": counts.get("top1_is_true", ""),
                 "trace_sha256": trace_sha})

    # ---- DIAGNOSE -> EXPLAIN -> PATCH -> RESIM (<=2 retries, then
    # DEGRADE in live mode only when no patch was ever applied: a
    # pre-patch failure stops triage-style instead of RED).
    # Pre-COLLECT failures above stay RED: with no diagnosis there is
    # nothing to triage. Post-patch resim-still-RED stays RED.
    final_patch = ""
    final_source = "none"
    diagnosis_text = ""
    expected_text = ""
    last_attempt: dict = {}
    green = False
    verdict_after: dict = {}
    reached_live_phase = True
    for attempt in range(MAX_ATTEMPTS):
        meta["retries"] = attempt
        note = ""
        if attempt > 0:
            prev = last_attempt.get("summary", "previous attempt failed")
            note = ("\n\nRETRY NOTE (attempt %d of %d): the previous patch "
                    "failed: %s. Diagnose again from the SAME trace and "
                    "propose a corrected unified diff."
                    % (attempt + 1, MAX_ATTEMPTS, prev[:600]))
        attempt_prompt = full_prompt + note
        # DIAGNOSE (live only; transport flakes retry with backoff)
        try:
            call, transport_waits = call_text_with_backoff(attempt_prompt)
        except LiveAPIError as exc:
            meta["error"] = ("live text API failed after %d attempts: %s "
                             "(no heuristic fallback)" % (MAX_ATTEMPTS, exc))
            meta["transport_retries"] = []
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            # persist the request for proof even though the call died;
            # the shared bundle tail below writes the remaining
            # placeholders, then the live run DEGRADEs to triage.
            with open(api("text_request.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"model": TEXT_MODEL, "endpoint": text_url(),
                     "prompt": attempt_prompt, "prompt_sha256": prompt_hash,
                     "prompt_file": args.prompt,
                     "prompt_tokens": len(attempt_prompt.split()),
                     "trace_lines": counts["trace_lines"],
                     "trace_tokens": counts["trace_tokens"],
                     "case": args.case, "seed": args.seed,
                     "max_tokens": TEXT_MAX_TOKENS, "attempt": attempt + 1,
                     "error": str(exc)}, indent=2) + "\n")
            break
        try:
            model_obj = extract_model_json(call["content"])
        except LoopError as exc:
            last_attempt = {"attempt": attempt + 1,
                            "summary": "unparseable model output: %s" % exc,
                            "text_latency_ms": call["latency_ms"],
                            "text_request_id": call["request_id"]}
            meta["attempts"].append(last_attempt)
            print("[DIAGNOSE] attempt %d unparseable: %s"
                  % (attempt + 1, exc), flush=True)
            continue
        diagnosis_text = str(model_obj["diagnosis"])
        expected_text = str(model_obj.get("expected", ""))
        model_diff = str(model_obj["patch_diff"])
        meta["diagnosed"] = True
        meta["transport_retries"] = transport_waits  # proof of flakes survived
        print("[DIAGNOSE] attempt %d latency=%dms req=%s diagnosis=%.120s"
              % (attempt + 1, call["latency_ms"], call["request_id"],
                 diagnosis_text.replace("\n", " ")), flush=True)

        # EXPLAIN (live image; failure recorded, never silent)
        img_prompt = visual.build_prompt(args.case, trace_sha, diagnosis_text)
        with open(api("image_request.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"model": visual.IMAGE_MODEL, "endpoint": visual.image_url(),
                 "prompt": img_prompt,
                 "prompt_sha256": sha256_text(img_prompt),
                 "trace_sha256": trace_sha,
                 "diagnosis_sha256": sha256_text(diagnosis_text),
                 "case": args.case, "attempt": attempt + 1}, indent=2) + "\n")
        try:
            img_info = visual.generate(
                img_prompt, os.path.join(run_dir, "explain.png"))
            meta["explain"] = "ok"
            meta["image_latency_ms"] = img_info["latency_ms"]
            meta["image_request_id"] = img_info["request_id"]
            meta["image_bytes"] = img_info["bytes"]
            img_resp = {"model": img_info["model"],
                        "request_id": img_info["request_id"],
                        "latency_ms": img_info["latency_ms"],
                        "bytes": img_info["bytes"],
                        "sha256": img_info["sha256"],
                        "transport": img_info["transport"],
                        "raw_note": "b64 payload replaced by sha256+bytes; "
                                    "explain.png is the provenance artifact"}
        except visual.LiveImageError as exc:
            meta["explain"] = "FAILED"
            img_resp = {"error": str(exc), "status": "FAILED"}
            print("[EXPLAIN] FAILED (recorded, continuing): %s" % exc,
                  flush=True)
        with open(api("image_response.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(img_resp, indent=2) + "\n")

        # TRIAGE STOP (opt-in): --triage / --stop-before-patch ends the
        # run after DIAGNOSE+EXPLAIN. Persist the deciding text call for
        # proof, write an empty patch.diff, and exit 2. Default auto
        # runs skip this and patch below.
        if should_withhold_patch(args):
            with open(api("text_request.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"model": TEXT_MODEL, "endpoint": text_url(),
                     "prompt": attempt_prompt, "prompt_sha256": prompt_hash,
                     "prompt_file": args.prompt,
                     "prompt_tokens": len(attempt_prompt.split()),
                     "trace_lines": counts["trace_lines"],
                     "trace_tokens": counts["trace_tokens"],
                     "case": args.case, "seed": args.seed,
                     "max_tokens": TEXT_MAX_TOKENS,
                     "attempt": attempt + 1}, indent=2) + "\n")
            with open(api("text_response.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"raw": call["raw"], "content": call["content"],
                     "diagnosis": diagnosis_text, "expected": expected_text,
                     "parsed_keys": sorted(model_obj.keys()),
                     "latency_ms": call["latency_ms"],
                     "request_id": call["request_id"],
                     "model": call["model"], "usage": call["usage"]},
                    indent=2) + "\n")
            meta["diagnosed"] = True
            meta["text_latency_ms"] = call["latency_ms"]
            meta["text_request_id"] = call["request_id"]
            meta["model_id"] = call["model"]
            meta["api_usage"] = call["usage"]
            meta["retries"] = attempt
            meta["attempts"].append(
                {"attempt": attempt + 1,
                 "text_latency_ms": call["latency_ms"],
                 "text_request_id": call["request_id"],
                 "diagnosis_chars": len(diagnosis_text),
                 "patch_source_try": "withheld-triage",
                 "summary": "triage stop: diagnosis saved, patch withheld"})
            with open(os.path.join(run_dir, "patch.diff"), "w",
                      encoding="utf-8") as fh:
                fh.write("")
            print("TRIAGE: diagnosis saved to %s "
                  "(patch withheld; --triage stop selected)" % (
                      api("text_response.json"),), flush=True)
            return finish("triage")

        # PATCH: verbatim model diff first (policy-gated via CaseSpec)
        outcome = try_apply_diff(
            model_diff, tree, os.path.join(run_dir, "attempt.diff"), spec)
        summary = ("guard_ok=%s applied=%s reasons=%s"
                   % (outcome["guard_ok"], outcome["applied"],
                      outcome["reasons"]))
        last_attempt = {"attempt": attempt + 1,
                        "text_latency_ms": call["latency_ms"],
                        "text_request_id": call["request_id"],
                        "diagnosis_chars": len(diagnosis_text),
                        "guard_ok": outcome["guard_ok"],
                        "patch_source_try": "model-verbatim",
                        "summary": summary}
        if not outcome["applied"]:
            # trust evidence: keep the rejected verbatim diff
            with open(os.path.join(run_dir, "rejected_model.diff"), "w",
                      encoding="utf-8") as fh:
                fh.write(model_diff if model_diff.endswith("\n")
                         else model_diff + "\n")
            print("[PATCH] verbatim REJECTED: %s" % outcome["reasons"],
                  flush=True)
            # bootstrap: ONLY on a live gate signal, line-anchored + guarded
            hit = gate_hit(diagnosis_text, case.get("gate", ""))
            if not hit:
                last_attempt["summary"] += (
                    "; bootstrap refused (diagnosis names no gate signal)")
                meta["attempts"].append(last_attempt)
                continue
            try:
                canon, _hits = canonical_diff(tree, case)
            except LoopError as exc:
                last_attempt["summary"] += "; bootstrap impossible: %s" % exc
                meta["attempts"].append(last_attempt)
                continue
            bout = try_apply_diff(
                canon, tree, os.path.join(run_dir, "bootstrap.diff"), spec)
            last_attempt["patch_source_try"] = "canonical-bootstrap-gated"
            last_attempt["gate_signal"] = hit
            if not bout["applied"]:
                last_attempt["summary"] += (
                    "; bootstrap REJECTED: %s" % bout["reasons"])
                meta["attempts"].append(last_attempt)
                continue
            outcome = bout
            outcome["bootstrap_gate"] = hit
            final_source = "canonical-bootstrap-gated"
            print("[PATCH] bootstrap gated applier applied "
                  "(gate signal %r)" % hit, flush=True)
        else:
            final_source = "model-verbatim"
            print("[PATCH] verbatim model diff applied: %s"
                  % outcome["files"], flush=True)
        final_patch = outcome.get("applied_diff", "")
        meta["patch_lines"] = outcome.get("patch_lines", 0)
        last_attempt["summary"] += "; applied via %s" % final_source
        meta["attempts"].append(last_attempt)
        # persist the deciding text call (this attempt produced the patch)
        with open(api("text_request.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"model": TEXT_MODEL, "endpoint": text_url(),
                 "prompt": attempt_prompt, "prompt_sha256": prompt_hash,
                 "prompt_file": args.prompt,
                 "prompt_tokens": len(attempt_prompt.split()),
                 "trace_lines": counts["trace_lines"],
                 "trace_tokens": counts["trace_tokens"],
                 "case": args.case, "seed": args.seed,
                 "max_tokens": TEXT_MAX_TOKENS,
                 "attempt": attempt + 1}, indent=2) + "\n")
        with open(api("text_response.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"raw": call["raw"], "content": call["content"],
                 "diagnosis": diagnosis_text, "expected": expected_text,
                 "parsed_keys": sorted(model_obj.keys()),
                 "latency_ms": call["latency_ms"],
                 "request_id": call["request_id"],
                 "model": call["model"], "usage": call["usage"]},
                indent=2) + "\n")
        meta["text_latency_ms"] = call["latency_ms"]
        meta["text_request_id"] = call["request_id"]
        meta["model_id"] = call["model"]
        meta["api_usage"] = call["usage"]
        meta["retries"] = attempt

        # RESIM + VERIFY
        print("[RESIM] rebuilding patched tree...", flush=True)
        try:
            verdict_after = resim_after(
                tree, case, args.seed,
                art("boot_after.log"), art("verdict_after.json"))
        except Exception as exc:
            last_attempt["summary"] += "; RESIM crashed: %r" % (exc,)
            meta["error"] = "RESIM failed: %r" % (exc,)
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            break
        meta["tohost_after"] = verdict_after.get("tohost")
        meta["checksum_after"] = verdict_after.get("checksum")
        meta["cycles_after"] = verdict_after.get("cycles")
        print("[VERIFY] tohost=%s checksum=%s cycles=%s" % (
            verdict_after.get("tohost"), verdict_after.get("checksum"),
            verdict_after.get("cycles")), flush=True)
        after_verdict = Verdict(
            **{"pass": bool(verdict_after.get("pass")),
               "evidence": {"tohost": verdict_after.get("tohost"),
                            "checksum": verdict_after.get("checksum"),
                            "source": "verdict_after.json"}})
        if getattr(after_verdict, "pass"):
            green = True
            break
        last_attempt["summary"] += (
            "; RESIM still RED (tohost=%s)" % verdict_after.get("tohost"))
        # fall through to next attempt (retry with feedback note)

    # ---- bundle tail
    if final_patch:
        with open(os.path.join(run_dir, "patch.diff"), "w",
                  encoding="utf-8") as fh:
            fh.write(final_patch if final_patch.endswith("\n")
                     else final_patch + "\n")
        meta["patch_source"] = final_source
        try:
            with open(art("boot_after.log"), encoding="utf-8") as fh:
                _ = fh.read()
        except OSError:
            with open(art("boot_after.log"), "w", encoding="utf-8") as fh:
                fh.write("(RESIM never completed)\n")
        if not verdict_after:
            with open(art("verdict_after.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({"case": args.case, "pass": False,
                                     "tohost": None, "checksum": None,
                                     "cycles": None, "seed": args.seed,
                                     "sim_time_utc": utc_now(),
                                     "note": "resim never completed"},
                                    indent=2) + "\n")
    else:
        # no patch ever applied: still write placeholders so the RED bundle
        # keeps its shape (proof of the failure, not a silent gap)
        for name, payload in (
                ("patch.diff", ""),
                ("boot_after.log", "(no patch applied; RESIM skipped)\n")):
            path = (os.path.join(run_dir, name) if name == "patch.diff"
                    else art(name))
            if not os.path.isfile(path):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(payload)
        if not os.path.isfile(art("verdict_after.json")):
            with open(art("verdict_after.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({"case": args.case, "pass": False,
                                     "tohost": None, "checksum": None,
                                     "cycles": None, "seed": args.seed,
                                     "sim_time_utc": utc_now(),
                                     "note": "no patch applied"}, indent=2)
                         + "\n")
        if not os.path.isfile(api("text_response.json")):
            with open(api("text_response.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({"error": "no successful text call",
                                     "attempts": meta["attempts"]},
                                    indent=2) + "\n")
        if not os.path.isfile(api("text_request.json")):
            with open(api("text_request.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({"model": TEXT_MODEL,
                                     "endpoint": text_url(),
                                     "prompt_sha256": prompt_hash,
                                     "prompt_file": args.prompt,
                                     "case": args.case, "seed": args.seed,
                                     "max_tokens": TEXT_MAX_TOKENS,
                                     "note": "no attempt produced an applied "
                                             "patch; see attempts log",
                                     "attempts": len(meta["attempts"])},
                                    indent=2) + "\n")
    if green:
        # `fixed unaided` counts only full-auto runs: green is reachable
        # only in live mode (triage withholds the patch), so green here
        # implies auto. human_touches is always 0.
        meta["fixed_unaided"] = bool(
            resolve_mode(args) == MODE_LIVE)  # human_touches is always 0
        print("GREEN: case %s fixed (%s, %d patch lines)" % (
            args.case, meta["patch_source"], meta["patch_lines"]), flush=True)
        # OPTIMIZE-mode: ONE opt pass after GREEN. Any opt outcome
        # (accepted/rejected/failed) leaves the GREEN fix standing;
        # the whole pass is wrapped so an opt harness error can never
        # ungreen the fix (fail-closed to the recorded GREEN bundle).
        if getattr(args, "goals", GOALS_FIX) == GOALS_FIX_OPT:
            try:
                with open(art("verdict_after.json"),
                          encoding="utf-8") as fh:
                    green_text = fh.read()
                opt_info = run_optimize_pass(
                    run_dir, tree, case, spec, args.seed,
                    dict(verdict_after), bundle["trace_block"],
                    green_text, rtl_rel)
            except Exception as exc:  # noqa: BLE001 -- GREEN must stand
                opt_info = {"status": OPT_STATUS_FAILED,
                            "reason": "opt harness error: %r" % (exc,),
                            "cycles_before": verdict_after.get("cycles"),
                            "cycles_after": None, "cycles_saved": 0,
                            "patch_lines": 0}
            meta["opt_status"] = opt_info.get("status", OPT_STATUS_FAILED)
            meta["opt_reason"] = opt_info.get("reason", "")
            meta["opt_note"] = OPT_DISCLAIMER
            meta["opt_cycles_before"] = opt_info.get("cycles_before")
            meta["opt_cycles_after"] = opt_info.get("cycles_after")
            meta["opt_cycles_saved"] = opt_info.get("cycles_saved", 0)
            meta["cycles_saved"] = opt_info.get("cycles_saved", 0)
            meta["opt_patch_lines"] = opt_info.get("patch_lines", 0)
            # Prompt provenance beside the fix flow's prompt_version/hash:
            # the same sha already lives in api/opt_text_request.json.
            meta["opt_prompt_file"] = opt_prompt_path()
            meta["opt_prompt_sha256"] = opt_info.get("opt_prompt_sha256")
            if meta["opt_status"] == OPT_STATUS_ACCEPTED:
                meta["cycles_after"] = opt_info.get("cycles_after")
                print("OPTIMIZE: accepted, saved %d SIMULATED cycles "
                      "(%s -> %s; same checksum/tohost)" % (
                          meta["cycles_saved"],
                          meta["opt_cycles_before"],
                          meta["opt_cycles_after"]), flush=True)
            else:
                print("OPTIMIZE: %s (%s); GREEN fix stands" % (
                    meta["opt_status"], meta["opt_reason"]), flush=True)
        return finish("green")
    if "error" not in meta:
        meta["error"] = ("still RED after %d attempt(s); "
                         "see attempts log" % len(meta["attempts"]))
    if resolve_mode(args) == MODE_LIVE and reached_live_phase and not final_patch:
        # Auto DEGRADE (pre-patch only): the live run reached DIAGNOSE
        # but no patch was ever applied (API flake with no patch, guard
        # reject with no patch, --triage withhold). Stop triage-style
        # -- exit 2 with a TRIAGE row that keeps the error -- instead
        # of RED. A patch applied but resim still RED falls through to
        # RED below. Guard + resim + checksum gates above are untouched:
        # no patch counts unless it passed them all.
        meta["degraded"] = True
        meta["degrade_reason"] = meta["error"]
        print("DEGRADED TO TRIAGE: live run did not go green: %s "
              "(stopping with patch %s; exit 2)" % (
                  meta["error"],
                  "applied" if final_patch else "withheld"),
              file=sys.stderr)
        return finish("triage")
    print("RED: case %s not fixed" % args.case, file=sys.stderr)
    return finish("red")


if __name__ == "__main__":
    sys.exit(main())
