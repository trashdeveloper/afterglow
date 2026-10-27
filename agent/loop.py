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
# Transport-flake accounting (wave-2): exhausted call_text_with_backoff
# sequences (e.g. 3 EMPTYs) are transport failures, NOT diagnostic
# attempts. Only completed model responses (parseable or salvaged)
# consume MAX_ATTEMPTS. Persistent transport death still fails loud and
# bounded via this cap, then degrades to triage.
MAX_TRANSPORT_FAILURES = 3
# Attempt-diversity accounting (wave-2): at most one duplicate-direction
# re-ask per run (extra model ask, no resim burned).
MAX_DUPLICATE_REASKS = 1
# Escalating attempt ladder 3->5: attempts 1-3 behave EXACTLY as before;
# attempts 4-5 unlock ONLY when attempts 1-3 all produced completed
# model responses that all went RED with observable behavioral signal,
# in three distinct directions, with zero transport failures (flakes
# never count) and accumulated est cost under cap. Rationale: with
# diversity + failure-feedback + flake accounting in place, ladder
# attempts carry strictly more signal (full prior-attempts block +
# STRATEGY-CHANGE directive) than attempts 1-3 ever had.
LADDER_MAX_ATTEMPTS = 5
LADDER_COST_CAP_USD = 0.50
# Honest worst-case per-attempt proxy when no cost signal exists yet:
# full TEXT_MAX_TOKENS output + full prompt-cap input + one image
# (2500*0.002 + 8000*0.008 + 0.03 = ~$0.10).
LADDER_PROXY_USD_PER_ATTEMPT = 0.10
STRATEGY_CHANGE_DIRECTIVE = (
    "STRATEGY-CHANGE: two/three distinct directions failed; propose a "
    "qualitatively different repair class \u2014 e.g. if prior patches edited "
    "combinational logic, consider sequential/reset/enable logic, and vice versa.")

# Consensus-diagnosis pilot (opt-in --consensus): per attempt, request up
# to 3 INDEPENDENT diagnoses (same prompt, fresh calls; transport flakes
# retried inside call_text_with_backoff and never counted as votes) and
# PATCH only on 2-of-3 agreement. Agreement = same target file AND
# overlapping line window (+/- CONSENSUS_WINDOW_LINES). Cost discipline
# reuses the ladder cost chain (ladder_cost_ok vs LADDER_COST_CAP_USD);
# over cap degrades to single-diagnosis with OFF-cost-cap logged.
CONSENSUS_WINDOW_LINES = 10
CONSENSUS_VOTES = 3

# Multimodal feedback pilot (opt-in --multimodal): after explain.png is
# generated, feed it BACK into a second diagnosis call (same trace window
# + first diagnosis + the diagram) that either CONFIRMs or REVISEs the
# first diagnosis. REVISE supersedes for PATCH (both + delta logged);
# CONFIRM proceeds with the identical patch. Cost gate reuses
# ladder_cost_ok vs LADDER_COST_CAP_USD; over cap (or image failure, or
# second-call failure) degrades to single-diagnosis, never blocks.
# Default OFF keeps the proven single path byte-identical.

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
    # Proof for flake-vs-signal accounting: the exhausted sequence
    # keeps the waits it survived so the caller can log transport
    # retries separately from consumed diagnostic attempts.
    last_exc.transport_waits = list(waits)  # noqa: SLF -- proof attr
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


def _salvage_candidates(body: str) -> list:
    """Propose repaired strings for a truncated JSON body, best first.

    (1) every balanced-brace close position, largest first (extract the
    largest {...} substring); (2) the truncation repair: close an open
    string (dropping one dangling escape backslash so the closer is not
    itself escaped), then close every still-open bracket/brace.
    json.loads is the real judge downstream -- this only proposes.
    """
    stack: list = []
    balanced_ends: list = []
    in_str = False
    esc = False
    for i, ch in enumerate(body):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{" or ch == "[":
            stack.append(ch)
        elif ch == "}" or ch == "]":
            if stack and ((ch == "}" and stack[-1] == "{")
                          or (ch == "]" and stack[-1] == "[")):
                stack.pop()
                if ch == "}" and not [o for o in stack if o == "{"]:
                    balanced_ends.append(i + 1)
    cands = [body[:e] for e in sorted(set(balanced_ends), reverse=True)]
    tail = body
    still_open = list(stack)
    if in_str:
        trailing_bs = len(tail) - len(tail.rstrip("\\"))
        if trailing_bs % 2 == 1:
            tail = tail[:-1]  # dangling escape: drop so " closes
        tail += '"'
    closer = {"{": "}", "[": "]"}
    tail += "".join(closer[o] for o in reversed(still_open))
    if tail not in cands:
        cands.append(tail)
    return cands


def salvage_model_json(content: str) -> dict:
    """Repair a TRUNCATED model JSON output (max_tokens cut). Never invents.

    Strict extract_model_json fails two truncation shapes: (a) no closing
    brace at all (cut inside a string, e.g. v3-21 attempt-2); (b) braces
    present but json.loads dies on the cut tail. Salvage tries the
    largest balanced {...} substring first, then the truncation repair
    (close open string + open brackets/braces). The first candidate that
    parses AND validates (non-empty diagnosis + non-empty patch_diff --
    the same gate as strict) wins. Anything else raises LoopError: a
    diagnosis-only repair is unusable (no diff to apply), and inventing
    one would violate the live-decider rule, so that retry is correctly
    burned.
    """
    if content is None or not str(content).strip():
        raise LoopError("salvage failed: empty model output, "
                        "nothing to repair")
    text = str(content).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return extract_model_json(content)
    except LoopError as exc:
        strict_reason = str(exc)
    start = text.find("{")
    if start < 0:
        raise LoopError("salvage failed: no JSON object to repair "
                        "(strict: %s)" % strict_reason)
    for cand in _salvage_candidates(text[start:]):
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if (isinstance(obj, dict)
                and str(obj.get("diagnosis", "")).strip()
                and str(obj.get("patch_diff", "")).strip()):
            return obj
    raise LoopError("salvage failed: surviving bytes yield no complete "
                    "diagnosis+diff (strict: %s)" % strict_reason)


def parse_model_output(content: str) -> tuple:
    """Strict parse, else truncation salvage. Returns (obj, salvaged).

    salvaged False: strict JSON, nothing repaired. True: a truncation
    repair yielded a complete diagnosis+diff, usable WITHOUT burning a
    retry. Raises LoopError only when both fail (garbage, or cut before
    patch_diff was emitted -- salvage never invents a diff, so that
    retry is correctly burned).
    """
    try:
        return extract_model_json(content), False
    except LoopError:
        return salvage_model_json(content), True


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
# RESIM integrity (mutbigN-v3-21): hash inputs -> build -> rehash, plus a
# no-op-patch detector. Attempt-1 of mutbigN-v3-21 applied cleanly
# (guard_ok=True applied=True) yet RESIM returned an IDENTICAL checksum
# before/after -- behaviorally impossible for that edit, pointing at a
# stale build / wrong tree / cached objects, which the old loop recorded
# as an ordinary RED with no hash evidence and no retry signal.
# --------------------------------------------------------------------------

NO_BEHAVIORAL_EFFECT_MSG = (
    "PATCH HAD NO BEHAVIORAL EFFECT — suspect no-op edit or stale build")


def detect_no_behavioral_effect(verdict_before: dict | None,
                                verdict_after: dict | None) -> bool:
    """Pure predicate: identical checksum AND unchanged tohost => null resim.

    A checksum that is missing/empty on either side never flags (broken
    sim verdicts must not read as "no effect"). Either side not a dict
    is also False. Same-checksum alone does NOT prove staleness -- a
    behaviorally-null edit for this oracle reads identically -- so the
    caller records this as *suspect* (flag + retry feedback), never as
    proof, and never as green.
    """
    if not isinstance(verdict_before, dict):
        return False
    if not isinstance(verdict_after, dict):
        return False
    before_sum = verdict_before.get("checksum")
    after_sum = verdict_after.get("checksum")
    if not before_sum or not after_sum:
        return False
    if before_sum != after_sum:
        return False
    return verdict_before.get("tohost") == verdict_after.get("tohost")


def snapshot_file_hashes(tree: str, rels: list) -> dict:
    """Content-hash the named tree-relative files. Missing files are
    skipped (never crash the loop on provenance)."""
    out: dict = {}
    for rel in rels or []:
        path = os.path.join(str(tree), str(rel))
        if os.path.isfile(path):
            out[str(rel)] = sha256_file(path)
    return out


def stash_patched_sources(tree: str, rels: list, run_dir: str) -> dict:
    """Copy the patched tree file(s) into the run bundle BEFORE resim as
    patched_source.<ext> (patched_source.<n>.<ext> when several share an
    extension) AND return their content hashes.

    The bundle copy pins what the build was supposed to compile; the
    returned hashes are the pre-build baseline for the post-VERIFY
    re-hash. Missing files are skipped, never fatal.
    """
    hashes: dict = {}
    used: set = set()
    for rel in rels or []:
        src = os.path.join(str(tree), str(rel))
        if not os.path.isfile(src):
            continue
        hashes[str(rel)] = sha256_file(src)
        _root, ext = os.path.splitext(os.path.basename(str(rel)))
        dest_name = "patched_source" + ext
        n = 1
        while dest_name in used:
            n += 1
            dest_name = "patched_source.%d%s" % (n, ext)
        used.add(dest_name)
        shutil.copyfile(src, os.path.join(run_dir, dest_name))
    return hashes


def hashes_match(pre: dict, post: dict) -> tuple:
    """Pure comparison of two {rel: sha256} snapshots.

    Returns (match: bool, detail: str). Missing-vs-present counts as a
    mismatch (the tree drifted under the build either way).
    """
    if pre == post:
        return True, "all %d file(s) unchanged" % len(pre)
    changed = sorted(
        {r for r in set(pre) | set(post) if pre.get(r) != post.get(r)})
    return False, "tree drifted under build: %s" % (changed,)


def build_loop_feedback(attempt_no: int, verdict_before: dict,
                        verdict_after: dict) -> str:
    """Retry-context sentence for a behaviorally-null resim.

    Threaded into the NEXT attempt's prompt as a separate LOOP FEEDBACK
    block (logged verbatim in text_request.json under "loop_feedback"),
    NOT baked into any prompt template -- so prompt files and their
    versioned hashes are untouched (no v3->v4 bump for this).
    """
    return (
        "%s (attempt %d: checksum %s == %s, tohost %s unchanged). "
        "Diagnose again from the SAME trace and propose a corrected "
        "unified diff against the CURRENT tree state."
        % (NO_BEHAVIORAL_EFFECT_MSG, int(attempt_no),
           verdict_before.get("checksum"), verdict_after.get("checksum"),
           verdict_after.get("tohost")))


def resim_with_integrity(tree: str, case: dict, seed: int, boot_path: str,
                         verdict_path: str, run_dir: str | None = None,
                         verdict_before: dict | None = None,
                         resim_fn=None) -> tuple:
    """Snapshot -> resim -> re-hash, with at most ONE clean rebuild.

    - BEFORE the build: stash the patched file(s) into the run bundle
      (when run_dir is given) and record pre-build content hashes.
    - AFTER VERIFY: re-hash the tree files. On mismatch mark
      integrity["stale-tree-suspect"]=True, drop the incremental
      obj_afterglow_<case> dir, and resim EXACTLY once more (clean
      rebuild retry); the second verdict stands, whatever it says.
    - When verdict_before is given, set
      integrity["no_behavioral_effect"] via detect_no_behavioral_effect.

    resim_fn (default: resim_after, resolved at call time so tests can
    mock loop.resim_after) must take
    (tree, case, seed, boot_path, verdict_path) and return a verdict
    dict. Returns (verdict, integrity). Raises only when resim_fn does.
    """
    if resim_fn is None:
        resim_fn = resim_after  # call-time lookup: honors mock.patch
    rels = list(case.get("files", []))
    if run_dir is not None:
        pre = stash_patched_sources(tree, rels, run_dir)
    else:
        pre = snapshot_file_hashes(tree, rels)
    verdict = resim_fn(tree, case, seed, boot_path, verdict_path)
    post = snapshot_file_hashes(tree, rels)
    match, detail = hashes_match(pre, post)
    integrity: dict = {"pre_hashes": dict(pre), "post_hashes": dict(post),
                       "integrity_detail": detail,
                       "stale-tree-suspect": False, "clean_rebuilds": 0,
                       "no_behavioral_effect": False}
    if not match:
        integrity["stale-tree-suspect"] = True
        mdir = os.path.join(str(tree),
                            "obj_afterglow_%s" % case.get("name"))
        shutil.rmtree(mdir, ignore_errors=True)
        integrity["clean_rebuilds"] = 1
        verdict = resim_fn(tree, case, seed, boot_path, verdict_path)
        post2 = snapshot_file_hashes(tree, rels)
        integrity["post_hashes"] = dict(post2)
        integrity["integrity_detail"] = (
            "%s; clean rebuild forced (obj dir dropped, resim x2)"
            % (detail,))
    if verdict_before is not None:
        integrity["no_behavioral_effect"] = detect_no_behavioral_effect(
            verdict_before, verdict)
    return verdict, integrity


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
    ap.add_argument("--consensus", action="store_true", default=False,
                    help="consensus-diagnosis pilot (opt-in): per attempt, "
                         "request up to 3 independent diagnoses and PATCH "
                         "only on 2-of-3 agreement (same file + overlapping "
                         "+/-10-line window). Splits consume the attempt "
                         "with no patch/resim. Over the $0.50 cost cap it "
                         "degrades to single-diagnosis. Default OFF keeps "
                         "the proven single path byte-identical.")
    ap.add_argument("--recall-past-fix", dest="recall_past_fix",
                    action="store_true", default=False,
                    help="past-fix recall pilot (opt-in): rank past GREEN "
                         "runs/ bundles by file+signal overlap and append "
                         "the top-1 fix (file:lines + one-line lesson, "
                         "capped tokens, provenance run-id) as a "
                         "PAST_FIX_RECALL context block. Retrieval is "
                         "read-only over runs/; a miss logs recall: MISS "
                         "and changes nothing. Default OFF keeps the "
                         "proven prompt path byte-identical.")
    ap.add_argument("--multimodal", action="store_true", default=False,
                    help="multimodal-feedback pilot (opt-in): after "
                         "explain.png, run one second diagnosis call "
                         "(same trace + first diagnosis + diagram) that "
                         "CONFIRMs or REVISEs; REVISE supersedes for "
                         "PATCH. Over the $0.50 cost cap, on image "
                         "failure, or on second-call failure it degrades "
                         "to single-diagnosis. Default OFF keeps the "
                         "proven single path byte-identical.")
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


def hash_tree(tree: str) -> str:
    """Deterministic sha256 over a scratch tree's files.

    Sorted rel-path + NUL + bytes per file; ignores .git, __pycache__,
    verilator obj_afterglow_* dirs, and patch debris (*.orig, *.rej) so
    build/apply byproducts never perturb a RED-baseline comparison.
    """
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in (".git", "__pycache__")
            and not d.startswith("obj_afterglow_"))
        for fn in sorted(filenames):
            if fn.endswith(".orig") or fn.endswith(".rej"):
                continue
            full = os.path.join(dirpath, fn)
            digest.update(os.path.relpath(full, tree).encode("utf-8")
                          + b"\0")
            with open(full, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def reset_tree_for_attempt(case: dict, pristine_tree: str, tree: str,
                           baseline_sha256: str | None = None) -> dict:
    """Clean-slate reset before attempt N>1 (mutant cases only).

    Re-applies the pinned anchor->bug fault onto a FRESH copy of the
    recorded pristine tree via prepare_mutant_tree(force=True), so the
    attempt diagnoses the RED baseline instead of the previous
    attempt's patch debris (v3-21: attempt-1's edit stayed in-tree and
    broke attempt-3's dry-run at the same line). Returns
    {"tree_reset": True, "tree_sha256": <post-reset hash>,
    "tree_reset_verified": bool}. Fails loudly (LoopError): non-mutant
    case (no recorded pristine baseline), missing pristine tree, or
    post-reset hash != baseline (refusing to debug unknown tree state).
    """
    if not is_mutant_case(case):
        raise LoopError("clean-slate reset needs a mutant case with "
                        "mutant_file/mutant_line (non-mutant trees have "
                        "no recorded pristine baseline)")
    if not pristine_tree or not os.path.isdir(pristine_tree):
        raise LoopError("clean-slate reset: pristine tree absent: %r"
                        % (pristine_tree,))
    prepare_mutant_tree(case, pristine_tree, tree, force=True)
    sha = hash_tree(tree)
    if baseline_sha256 is not None and sha != baseline_sha256:
        raise LoopError(
            "clean-slate reset mismatch: post-reset %s != RED baseline "
            "%s (refusing to debug unknown tree state)"
            % (sha[:16], str(baseline_sha256)[:16]))
    return {"tree_reset": True, "tree_sha256": sha,
            "tree_reset_verified": baseline_sha256 is not None}


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


def patch_one_liner(diff_text: str) -> str:
    """One-line summary of a unified diff: @@ hunk + first +/- line."""
    hunk = ""
    change = ""
    for ln in (diff_text or "").splitlines():
        s = ln.strip()
        if s.startswith("@@") and not hunk:
            hunk = s
        elif ((s.startswith("+") and not s.startswith("+++"))
              or (s.startswith("-") and not s.startswith("---"))):
            if not change:
                change = s
        if hunk and change:
            break
    core = " / ".join(p for p in (hunk, change) if p)
    if not core:
        first = (diff_text or "").strip().splitlines()
        core = first[0].strip() if first else "(empty diff)"
    return core[:160]


def format_prior_attempts_block(priors) -> str:
    """Compact PRIOR_ATTEMPTS block for attempt N>1 diagnose prompts.

    One row per earlier failed attempt: patch one-liner + resim outcome
    + checksum delta (the v3-21 lesson: attempt-1's applied-but-ineffective
    patch must be visible to attempt 2, not just "previous patch failed").
    Empty list -> "" (attempt 1 prompts stay unchanged). Capped so the
    feedback can never blow the prompt token budget.
    """
    rows = []
    for p in priors or []:
        rows.append("- attempt %s: patch `%s` -> resim %s; "
                    "checksum changed: %s"
                    % (p.get("attempt", "?"), p.get("patch", "?"),
                       p.get("resim", "?"),
                       p.get("checksum_changed", "?")))
    if not rows:
        return ""
    block = ("### PRIOR_ATTEMPTS [earlier patches FAILED -- do NOT repeat "
             "them; diagnose again from the SAME trace]\n"
             + "\n".join(rows))
    return block[:600]


def direction_key(diff_text: str) -> str:
    """Normalized change-direction key for one proposed patch.

    ``file + sorted changed-line numbers + op-word`` per file, ``|``
    joined across files sorted by path. Line numbers are NEW-file lines
    touched by the hunk (added lines; a deletion site records the line
    it was removed before). Op-word is add/remove/replace (unknown when
    the text carries no +/- lines, e.g. a bare one-liner -- the hunk
    start then anchors the key so distinct hunks still differ).
    Full diffs and bare one-liners both work; equal keys mean the same
    direction (the v3-21 attempts-1-and-3 same-patch lesson).
    """
    text = diff_text or ""
    if not text.strip():
        return "(empty)"
    files: dict = {}

    def ensure(path: str) -> dict:
        if path not in files:
            files[path] = {"lines": set(), "starts": set(),
                           "add": False, "del": False}
        return files[path]

    cur: str | None = None
    new_lineno = 0
    for ln in text.splitlines():
        if ln.startswith("+++ "):
            path = ln[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path in ("/dev/null", "dev/null"):
                continue  # deleted file: keep the --- path slot instead
            cur = path or "unknown"
            ensure(cur)
            continue
        if ln.startswith("--- "):
            if cur is None:
                path = ln[4:].strip()
                if path.startswith("a/"):
                    path = path[2:]
                if path not in ("/dev/null", "dev/null"):
                    cur = path or "unknown"
                    ensure(cur)
            continue
        if ln.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", ln)
            if m:
                new_lineno = int(m.group(1))
                if cur is None:
                    cur = "unknown"
                ensure(cur)["starts"].add(new_lineno)
            continue
        if cur is None:
            continue
        if ln.startswith("+") and not ln.startswith("+++"):
            files[cur]["lines"].add(new_lineno)
            files[cur]["add"] = True
            new_lineno += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            files[cur]["lines"].add(new_lineno)
            files[cur]["del"] = True
        else:
            new_lineno += 1  # context line advances the new-file cursor
    if not files:
        return "(empty)"
    segs = []
    for path in sorted(files):
        info = files[path]
        lines = sorted(info["lines"]) or sorted(info["starts"])
        if info["add"] and info["del"]:
            op = "replace"
        elif info["add"]:
            op = "add"
        elif info["del"]:
            op = "remove"
        else:
            op = "unknown"
        nums = ",".join(str(n) for n in lines) if lines else "none"
        segs.append("%s:%s:%s" % (path, nums, op))
    return "|".join(segs)


def _prior_direction(entry: dict) -> str:
    """Direction key for one prior_attempts entry.

    Entries recorded with a full diff carry the stored ``direction``
    (exact); legacy / fabricated one-liner-only entries fall back to
    keying the one-liner text itself (hunk-anchored, still comparable).
    """
    if not isinstance(entry, dict):
        return "(empty)"
    stored = entry.get("direction")
    if isinstance(stored, str) and stored:
        return stored
    return direction_key(str(entry.get("patch", "")))


def is_duplicate_direction(diff_text: str, priors) -> bool:
    """True when the proposed patch repeats a prior attempt's direction."""
    try:
        key = direction_key(diff_text)
    except Exception:  # noqa: BLE001 -- unparseable text is never a dup
        return False
    if key in ("(empty)", "(unparseable)"):
        return False
    for prior in priors or []:
        if key == _prior_direction(prior):
            return True
    return False


def ladder_text_usd(usage, model=None) -> float:
    """Estimated USD for one completed Text call (pure; ledger math).

    Mirrors afterglow_core.cost pricing (input/output per-1k-token
    rates for the deciding model). Empty/foreign usage estimates to
    zero -- the caller falls back to the per-attempt proxy then.
    """
    rates = cost_ledger.PRICING.get(
        model or TEXT_MODEL,
        cost_ledger.PRICING[cost_ledger.TEXT_MODEL_DEFAULT])

    def _n(key: str) -> int:
        try:
            value = int((usage or {}).get(key, 0))
        except (TypeError, ValueError):
            return 0
        return max(value, 0)

    return round(_n("prompt_tokens") / 1000
                 * float(rates.get("input_usd_per_1k", 0.0))
                 + _n("completion_tokens") / 1000
                 * float(rates.get("output_usd_per_1k", 0.0)), 6)


def ladder_cost_ok(run_dir: str, tracked_usd: float,
                   attempts_consumed: int) -> tuple:
    """Ladder cost gate: accumulated est must be under the cap.

    The explicit cost ledger (run_dir/cost.json) wins when present;
    else the in-loop tracked accumulation when it carries signal;
    else an honest worst-case per-attempt proxy. Returns
    (ok, est_usd, source) with source in ledger/tracked/proxy.
    """
    try:
        with open(os.path.join(str(run_dir), "cost.json"),
                  encoding="utf-8") as fh:
            est = float((json.load(fh) or {}).get("est_usd", 0.0))
        return est < LADDER_COST_CAP_USD, est, "ledger"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        tracked = float(tracked_usd or 0.0)
    except (TypeError, ValueError):
        tracked = 0.0
    if tracked > 0:
        return tracked < LADDER_COST_CAP_USD, tracked, "tracked"
    proxy = float(attempts_consumed) * LADDER_PROXY_USD_PER_ATTEMPT
    return proxy < LADDER_COST_CAP_USD, proxy, "proxy"


def ladder_eligible(prior_attempts, attempts_consumed: int,
                    transport_failures: int) -> tuple:
    """Pure ladder unlock predicate (no I/O).

    True ONLY when attempts 1-3 all produced completed model responses
    that all went RED with observable behavioral signal, in three
    distinct directions, with zero transport failures: consumed==3,
    no flakes, every prior row a RED resim (guard-rejected and
    unparseable rows carry no resim signal), every checksum changed
    (a checksum-unchanged resim is a no-op/stale-build, not signal),
    and three distinct direction keys (so the STRATEGY-CHANGE claim
    of distinct failed directions is true). Returns (ok, reason);
    the reason is recorded verbatim in meta["ladder_reason"].
    """
    if attempts_consumed != MAX_ATTEMPTS:
        return False, "base-budget-not-exhausted"
    if transport_failures:
        return False, ("transport-failures-present "
                        "(flakes never unlock the ladder)")
    priors = list(prior_attempts or [])
    if len(priors) != MAX_ATTEMPTS:
        return False, "prior-attempts-lack-3-completed"
    for entry in priors:
        if not str(entry.get("resim", "")).startswith("RED"):
            return False, ("prior-attempts-lack-3-RED "
                           "(guard-rejected/unparseable rows carry "
                           "no resim signal)")
    for entry in priors:
        if entry.get("checksum_changed") != "yes":
            return False, ("prior-attempts-lack-behavioral-signal "
                           "(a checksum-unchanged resim is a "
                           "no-op/stale-build, not signal)")
    directions = [_prior_direction(entry) for entry in priors]
    if len(set(directions)) != MAX_ATTEMPTS:
        return False, ("prior-attempts-lack-diversity "
                        "(STRATEGY-CHANGE claims distinct directions)")
    return True, "unlocked-3-signal-RED"


def consensus_file_lines(diff_text: str) -> dict:
    """Map each diff target file to its sorted touched NEW-file lines.

    Same hunk walk as direction_key (``+++ b/<path>`` headers anchor the
    file, ``@@ -a,b +c,d @@`` anchors the new-file cursor, ``+`` lines
    record the cursor, ``-`` lines record the deletion site without
    advancing). Empty/unparseable diffs yield {} (never agree).
    """
    out: dict = {}
    cur: str | None = None
    new_lineno = 0
    for ln in (diff_text or "").splitlines():
        if ln.startswith("+++ "):
            path = ln[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path in ("/dev/null", "dev/null"):
                cur = None
                continue
            cur = path or "unknown"
            out.setdefault(cur, set())
            continue
        if ln.startswith("--- "):
            if cur is None:
                path = ln[4:].strip()
                if path.startswith("a/"):
                    path = path[2:]
                if path not in ("/dev/null", "dev/null"):
                    cur = path or "unknown"
                    out.setdefault(cur, set())
            continue
        if ln.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", ln)
            if m:
                new_lineno = int(m.group(1))
            continue
        if cur is None:
            continue
        if ln.startswith("+") and not ln.startswith("+++"):
            out[cur].add(new_lineno)
            new_lineno += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            out[cur].add(new_lineno)
        else:
            new_lineno += 1  # context line advances the new-file cursor
    return {k: sorted(v) for k, v in out.items() if v}


def consensus_agree(diff_a: str, diff_b: str,
                    window: int = CONSENSUS_WINDOW_LINES) -> bool:
    """Agreement predicate: same target file AND overlapping line window.

    True iff the two diffs share at least one target file with a touched
    line within +/- window of each other. Empty/unparseable diffs (no
    file->lines map) never agree.
    """
    fa = consensus_file_lines(diff_a)
    fb = consensus_file_lines(diff_b)
    if not fa or not fb:
        return False
    for path, lines_a in fa.items():
        lines_b = fb.get(path)
        if not lines_b:
            continue
        for la in lines_a:
            for lb in lines_b:
                if abs(int(la) - int(lb)) <= int(window):
                    return True
    return False


def consensus_summarize(diff_text: str) -> str:
    """Compact per-vote locator: '<file>:<first-line> ...' (or '(empty)')."""
    mapping = consensus_file_lines(diff_text)
    if not mapping:
        return "(empty)"
    return " ".join("%s:%s" % (path, lines[0])
                     for path, lines in sorted(mapping.items()))


def consensus_majority(diffs: list) -> tuple:
    """2-of-3 agreement verdict over vote diffs. Returns (status, winner, note).

    status is "unanimous" (3-0, every pair agrees), "majority" (exactly
    one agreeing pair -- the 2-1 case -- or 2 agreeing votes when a flake
    was excluded), or "split" (no pair agrees, or fewer than 2 usable
    votes). winner is the index into diffs of the majority diagnosis
    (first vote of the first agreeing pair: pairs checked in order
    (0,1), (0,2), (1,2)); unanimous winners are vote 0). Never raises:
    unparseable input reads as disagreement, never as agreement.
    """
    items = list(diffs or [])
    if len(items) < 2:
        return ("split", -1, "split: fewer than 2 usable votes")
    pairs = [(0, 1)]
    if len(items) >= 3:
        pairs = [(0, 1), (0, 2), (1, 2)]
    agreeing = [p for p in pairs
                if consensus_agree(items[p[0]], items[p[1]])]
    if len(items) >= 3 and len(agreeing) == 3:
        return ("unanimous", 0,
                "unanimous 3-0 on %s" % consensus_summarize(items[0]))
    if agreeing:
        w = agreeing[0][0]
        return ("majority", w,
                "majority 2-1 on %s (winner vote %d of %d)"
                % (consensus_summarize(items[w]), w + 1, len(items)))
    detail = " | ".join(consensus_summarize(d) for d in items)
    return ("split", -1, "split: no agreeing pair (%s)" % detail)


def build_multimodal_prompt(case: str, trace_block: str,
                            first_diagnosis: str, first_diff: str,
                            image_sha256: str) -> str:
    """Second-diagnosis prompt: same trace + first diagnosis + diagram ref.

    Pure string function -- no API call, fully testable offline. The
    diagram itself travels out-of-band as runs/<id>/explain.png; the
    prompt binds it by sha256 so the proof bundle stays verifiable.
    Asks for JSON-only {decision, diagnosis, patch_diff, reason} where
    decision is CONFIRM or REVISE.
    """
    trace = (trace_block or "").strip()
    if len(trace) > 4000:
        trace = trace[:4000] + "\n...[truncated]"
    diag = (first_diagnosis or "").strip()
    if len(diag) > 1500:
        diag = diag[:1500] + "...[truncated]"
    diff = (first_diff or "").strip()
    if len(diff) > 4000:
        diff = diff[:4000] + "\n...[truncated]"
    digest = (image_sha256 or "").strip()[:16]
    return (
        "You are a hardware-debug reviewer. A first debug agent diagnosed "
        "an RISC-V RTL failure and an explain.png diagram was drawn from "
        "that diagnosis (explain.png sha256 first 16 hex: %s).\n"
        "Case: %s\n"
        "Failing trace window (same window the first diagnosis saw):\n%s\n"
        "First diagnosis (verbatim):\n%s\n"
        "First patch_diff:\n%s\n"
        "Look at the diagram (explain.png in the run bundle) together with "
        "the trace above. Either CONFIRM the first diagnosis (it explains "
        "the failing signal and the diff fixes it) or REVISE it (it "
        "misnames the signal or the diff is wrong). Reply JSON ONLY with "
        "keys: decision (exactly \"CONFIRM\" or \"REVISE\"), diagnosis "
        "(final diagnosis text), patch_diff (final unified diff), reason "
        "(one sentence: why the diagram confirms or what it corrects)."
        % (digest, case, trace, diag, diff)
    )


def parse_multimodal_output(content: str, fallback_diagnosis: str = "",
                            fallback_diff: str = "") -> dict:
    """Parse the second-diagnosis reply into a CONFIRM/REVISE verdict.

    Decision rule: REVISE supersedes ONLY when decision is literal
    REVISE with a non-empty diagnosis AND a non-empty
    patch_diff DIFFERENT from the first diff; everything else CONFIRMs
    and keeps the first diagnosis. Never raises: unparseable or
    incomplete replies read as CONFIRM (keep the proven first path).
    """
    first_diag = str(fallback_diagnosis or "")
    first_diff = str(fallback_diff or "")
    try:
        text = str(content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object")
        obj = json.loads(text[start:end + 1])
        if not isinstance(obj, dict):
            raise ValueError("not an object")
    except Exception as exc:
        return {"decision": "CONFIRM", "diagnosis": first_diag,
                "patch_diff": first_diff, "reason": (
                    "unparseable second call (%s); keeping first "
                    "diagnosis" % (exc,)),
                "revised": False}
    decision = str(obj.get("decision", "") or "").strip()
    diag = str(obj.get("diagnosis", "") or "").strip()
    diff = str(obj.get("patch_diff", "") or "").strip()
    reason = str(obj.get("reason", "") or "").strip()
    if (decision == "REVISE" and diag and diff
            and re.sub(r"\s+", "", diff) != re.sub(
                r"\s+", "", first_diff)):
        return {"decision": "REVISE", "diagnosis": diag,
                "patch_diff": diff,
                "reason": reason or "second call revises the diagnosis",
                "revised": True}
    if decision == "REVISE" and (not diag or not diff):
        return {"decision": "CONFIRM", "diagnosis": first_diag,
                "patch_diff": first_diff, "reason": (
                    "REVISE without complete diagnosis+diff; keeping "
                    "first diagnosis"),
                "revised": False}
    if decision == "REVISE":
        return {"decision": "CONFIRM", "diagnosis": first_diag,
                "patch_diff": first_diff, "reason": (
                    "REVISE with identical diff; keeping first diagnosis"),
                "revised": False}
    return {"decision": "CONFIRM", "diagnosis": first_diag,
            "patch_diff": first_diff,
            "reason": reason or "second call confirms the first diagnosis",
            "revised": False}


def _bundle_case(runs_dir: str, run_id: str) -> str:
    """Case id recorded in a prior bundle's meta.json ("" when unknown)."""
    try:
        with open(os.path.join(runs_dir, run_id, "meta.json"),
                  encoding="utf-8") as fh:
            meta = json.load(fh)
        return str(meta.get("case") or meta.get("case_id") or "")
    except (OSError, ValueError):
        return ""


_RECALL_TOP_K = 2
_RECALL_OVERFETCH = 4


def assemble_harvest(trace_block: str, verdict_text: str, tree: str,
                     rtl_file_list: list, runs_dir: str,
                     current_run_id=None, current_case=None) -> tuple:
    """Assemble the v3 HARVEST block (fingerprint + cone slice + pack).

    Returns (harvest_text, counts, fp_record). Budgets are enforced
    inside harvest_pack.build_v3_pack (200 lines / 2500 tokens hard
    caps, PACK_BUDGET_TOKENS scored merge); the advisory recall section
    below is dropped first under pressure and is never load-bearing.
    Fix-memory recall is ADVISORY ONLY: similar failure signatures with
    Jaccard scores, no patch text -- re-sim decides, recall can never
    override the guard (which only ever sees diffs).

    Recall hygiene: the CURRENT run-id (reruns reuse run-ids) and bundles
    of the SAME case (same-fault self-match, e.g. mutbigN-v3-02 vs its
    rerun) are excluded from recall; drops are counted in
    ``recall_excluded``. The static cone slice falls back to a +/-30-line
    window around the TOP localization suspect when it yields 0 lines;
    ``cone_source`` (static | fallback-window | empty) is always logged.
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
    cone_source = "static"
    if not cone_lines:
        # Empty static slice (e.g. picorv32 runs): window around the TOP
        # localization suspect so the model still gets grounded file:line
        # context instead of a silent empty CONE_SLICE.
        try:
            ranked = context_pack.localize(
                tree, list(rtl_file_list or []), signals)
        except Exception:  # localization never breaks collection
            ranked = []
        static_unparsed = cone_report.get("unparsed", 0)
        fb_lines: list = []
        fb_report: dict = {}
        for rel, hits, _shown in ranked:
            if hits <= 0:
                continue
            for sig in signals:
                fb_lines, fb_report = harvest_slice.fallback_window(
                    tree, rel, sig)
                if fb_lines:
                    break
            if fb_lines:
                break
        if fb_lines:
            cone_lines = fb_lines
            cone_report = dict(fb_report)
            cone_report["unparsed"] = static_unparsed
            cone_source = "fallback-window"
        else:
            cone_source = "empty"
    cone_report["cone_source"] = cone_source
    pack_text, pack_counts = harvest_pack.build_v3_pack(
        fp_record, cone_lines, cone_report, verdict_text)
    excluded_ids = {current_run_id} if current_run_id else set()
    try:
        recalled = fix_memory.recall_similar(
            signals, runs_dir, k=_RECALL_TOP_K + _RECALL_OVERFETCH,
            exclude=excluded_ids)
    except Exception:  # recall is advisory: never break collection
        recalled = []
    recall_rows = []
    recall_lines = []
    recall_excluded = 0
    if (current_run_id and fix_memory.load_fingerprint(
            os.path.join(runs_dir, current_run_id)) is not None):
        # recall_similar dropped it via exclude= above: count the hygiene.
        recall_excluded += 1
    for run_id, score, rec in recalled or []:
        if run_id in excluded_ids:
            recall_excluded += 1  # defense in depth (memory also filters)
            continue
        if (current_case and _bundle_case(runs_dir, run_id)
                == current_case):
            recall_excluded += 1  # same-case self-match is not signal
            continue
        if score <= 0:
            continue
        recall_rows.append((run_id, round(float(score), 4)))
        recall_lines.append(
            "- %s (jaccard %.2f): trap `%s` | oracle `%s`" % (
                run_id, float(score),
                str(rec.get("trap_line", ""))[:160],
                str(rec.get("oracle_sig", ""))[:160]))
        if len(recall_rows) >= _RECALL_TOP_K:
            break
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
        "cone_source": cone_source,
        "cone_drivers": cone_report.get("drivers", 0),
        "harvest_lines": len(harvest_text.splitlines()),
        "harvest_tokens": harvest_pack.approx_tokens(harvest_text),
        "recall": recall_rows,
        "recall_dropped": recall_dropped,
        "recall_excluded": recall_excluded,
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
        # Resim-integrity default: set True (attempt + meta) only when a
        # resim returns identical checksum + unchanged tohost.
        "no_behavioral_effect": False,
        # Ladder 3->5 accounting: logged in EVERY meta (init defaults
        # cover all pre-loop exits; the gate updates them once the
        # base budget is exhausted).
        "ladder_used": False,
        "ladder_reason": "not-evaluated (base budget not yet exhausted)",
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
    pristine_tree = None
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
    if mutant_record is not None:
        # Clean-slate anchor: hash of the RED scratch tree. Attempt N>1
        # resets to this baseline before diagnosing (v3-21: attempt-1's
        # patch stayed in-tree and broke attempt-3's dry-run at 1446).
        meta["red_baseline_tree_sha256"] = hash_tree(tree)

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
                    rtl_files(tree), os.path.join(ROOT, "runs"),
                    current_run_id=run_id, current_case=args.case)
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
                                "cone_drivers": 0,
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
    # PAST-FIX RECALL (opt-in --recall-past-fix, default OFF: without
    # the flag this whole block is skipped so the proven prompt path
    # stays byte-identical). Read-only retrieval over runs/ (never
    # writes bundles); the top-1 past GREEN fix is appended as a
    # separate context block (prompt templates untouched, prompt hash
    # stable), provenance lands in meta["recall_past_fix"], and the
    # block is logged verbatim in text_request.json via attempt_prompt
    # below. A miss (or a retriever error) logs recall: MISS and leaves
    # the prompt unchanged (fail-closed: recall never breaks a run).
    recall_info: dict = {"status": "disabled",
                         "reason": "flag OFF (default)"}
    if bool(getattr(args, "recall_past_fix", False)):
        try:
            from afterglow_core import recall as recall_mod
            _rblock, recall_info = recall_mod.recall_for_red(
                bundle["trace_block"], verdict_json, [rtl_rel],
                os.path.join(ROOT, "runs"),
                exclude_case_ids={args.case})
            if _rblock:
                _rprompt, _applied, _why = recall_mod.apply_to_prompt(
                    full_prompt, _rblock,
                    max_prompt_tokens=collect.MAX_PROMPT_TOKENS)
                if _applied:
                    full_prompt = _rprompt
                    counts = dict(
                        counts,
                        prompt_tokens=collect.approx_tokens(full_prompt))
                else:
                    recall_info = {"status": "MISS",
                                   "run_id": recall_info.get("run_id", ""),
                                   "score": recall_info.get("score", 0.0),
                                   "fix_ref": recall_info.get("fix_ref", ""),
                                   "reason": _why}
                print("[RECALL] %s" % _why, flush=True)
            else:
                print("[RECALL] recall: MISS (%s)"
                      % (recall_info.get("reason", ""),), flush=True)
        except Exception as _rexc:  # noqa: BLE001 -- fail-closed, never die
            recall_info = {"status": "MISS", "run_id": "",
                           "score": 0.0, "fix_ref": "",
                           "reason": "retriever error: %s" % (_rexc,)}
            print("[RECALL] recall: MISS (retriever error: %s)"
                  % (_rexc,), flush=True)
    meta["recall_past_fix"] = recall_info
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

    # ---- DIAGNOSE -> EXPLAIN -> PATCH -> RESIM (<=2 retries = 3 base
    # attempts, then the ladder gate: at most 2 extra attempts unlock
    # ONLY on 3 signal-bearing REDs with zero flakes and est under cap;
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
    # LOOP FEEDBACK (resim-integrity): set after a behaviorally-null
    # resim (identical checksum + unchanged tohost); threaded into the
    # NEXT attempt's prompt as a separate context block and logged
    # verbatim in text_request.json. Cleared when a resim shows a
    # behavioral change. Never baked into a prompt template (no
    # prompt-file bump for this).
    loop_feedback = ""
    # Prior-attempt failure feedback (v3-21 lesson): one compact row per
    # earlier failed attempt (patch one-liner + resim outcome + checksum
    # delta), threaded into attempt N>1 prompts as a context block.
    prior_attempts: list = []
    # Flake-vs-signal accounting (wave-2): ONLY completed model responses
    # (parseable or salvaged) consume the MAX_ATTEMPTS diagnostic budget.
    # Transport flakes (EMPTY content) retry the SAME diagnostic attempt;
    # persistent transport death fails loud and bounded via
    # MAX_TRANSPORT_FAILURES. Proof: attempts_consumed vs
    # transport_retries / transport_failures in meta.
    attempts_consumed = 0
    transport_failures = 0
    transport_waits_all: list = []
    duplicate_reasks = 0
    # Ladder 3->5 state: decided once when the base budget is exhausted;
    # tracked_spend_usd accumulates real per-call est so the cost gate
    # judges accumulated spend, not just the latest snapshot.
    ladder_used = False
    ladder_reason = "not-evaluated (base budget not yet exhausted)"
    ladder_decided = False
    tracked_spend_usd = 0.0
    # Consensus pilot (opt-in --consensus, default OFF): when False every
    # line below behaves byte-for-byte as the proven single path.
    use_consensus = bool(getattr(args, "consensus", False))
    # Multimodal pilot (opt-in --multimodal, default OFF): same rule.
    use_multimodal = bool(getattr(args, "multimodal", False))
    meta["attempts_consumed"] = 0
    meta["transport_retries"] = []
    meta["transport_failures"] = 0

    def note_transport_failure(exc: Exception) -> bool:
        """Account one exhausted transport sequence; True => stop the run.

        Reads the current attempt_prompt/loop_feedback/attempt for the
        proof snapshot written to text_request.json.
        """
        nonlocal transport_failures
        nonlocal ladder_reason
        transport_failures += 1
        transport_waits_all.extend(
            list(getattr(exc, "transport_waits", None) or []))
        meta["transport_retries"] = list(transport_waits_all)
        meta["transport_failures"] = transport_failures
        meta["attempts_consumed"] = attempts_consumed
        print("[DIAGNOSE] transport failure %d of %d on attempt %d: %s; "
              "diagnostic budget preserved (%d/%d consumed)"
              % (transport_failures, MAX_TRANSPORT_FAILURES, attempt + 1,
                 exc, attempts_consumed, MAX_ATTEMPTS), flush=True)
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
                 "error": str(exc),
                 "transport_failure": transport_failures,
                 "attempts_consumed": attempts_consumed,
                 "loop_feedback": loop_feedback}, indent=2) + "\n")
        if transport_failures >= MAX_TRANSPORT_FAILURES:
            meta["error"] = (
                "live text API failed after %d transport failures "
                "(%d diagnostic attempts completed): %s "
                "(no heuristic fallback)"
                % (transport_failures, attempts_consumed, exc))
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            # The ladder never unlocks on flakes: record the reason so
            # every triage-on-flakes meta carries the ladder verdict too.
            ladder_reason = ("transport-failures-present "
                             "(%d transport failures; flakes never unlock "
                             "the ladder)" % transport_failures)
            meta["ladder_used"] = False
            meta["ladder_reason"] = ladder_reason
            # The shared bundle tail below writes the remaining
            # placeholders, then the live run DEGRADEs to triage.
            return True
        return False

    attempt = 0
    attempt_limit = MAX_ATTEMPTS
    while True:
        if attempts_consumed >= attempt_limit:
            if (attempts_consumed == MAX_ATTEMPTS
                    and attempt_limit == MAX_ATTEMPTS
                    and not ladder_decided):
                # Base budget exhausted: evaluate the ladder gate ONCE.
                # Attempts 1-3 already ran EXACTLY as before; this block
                # only decides whether attempts 4-5 unlock (at most two
                # extra diagnose->patch->resim rounds with strictly more
                # signal). A blocked ladder breaks here, preserving the
                # historic stop-at-3 behavior byte-for-byte.
                ladder_decided = True
                ok, why = ladder_eligible(prior_attempts,
                                          attempts_consumed,
                                          transport_failures)
                if ok:
                    cok, est, src = ladder_cost_ok(
                        run_dir, tracked_spend_usd, attempts_consumed)
                    if cok:
                        attempt_limit = LADDER_MAX_ATTEMPTS
                        ladder_used = True
                        ladder_reason = (
                            "unlocked-3-signal-RED "
                            "(est $%.4f via %s < cap $%.2f)"
                            % (est, src, LADDER_COST_CAP_USD))
                    else:
                        ladder_reason = (
                            "ladder_cost_cap (est $%.4f via %s "
                            ">= cap $%.2f)" % (est, src,
                                               LADDER_COST_CAP_USD))
                else:
                    ladder_reason = why
                meta["ladder_used"] = ladder_used
                meta["ladder_reason"] = ladder_reason
                print("[LADDER] %s (ladder_used=%s)"
                      % (ladder_reason, ladder_used), flush=True)
                if attempt_limit == MAX_ATTEMPTS:
                    break
            else:
                break
        elif (ladder_used and attempt_limit == LADDER_MAX_ATTEMPTS
                and attempts_consumed == LADDER_MAX_ATTEMPTS - 1):
            # Before the final ladder attempt: re-check the cost cap on
            # the accumulated spend; over cap stops with ladder_cost_cap.
            cok, est, src = ladder_cost_ok(
                run_dir, tracked_spend_usd, attempts_consumed)
            if not cok:
                ladder_reason = (
                    "ladder_cost_cap (est $%.4f via %s >= cap $%.2f; "
                    "stopping before attempt %d)"
                    % (est, src, LADDER_COST_CAP_USD,
                       attempts_consumed + 1))
                meta["ladder_reason"] = ladder_reason
                print("[LADDER] %s" % ladder_reason, flush=True)
                break
        # Transport retries never advance the display counter: attempt N
        # is retried as attempt N until a completed response consumes it.
        attempt = attempts_consumed
        meta["retries"] = attempt
        if attempt > 0:
            # Clean-slate attempts: reset to the recorded RED baseline
            # BEFORE diagnosing again (v3-21 lesson: attempt N must not
            # debug attempt N-1's patch debris). Mutant trees re-apply
            # the pinned fault onto the pristine copy; non-mutant trees
            # have no recorded baseline (logged false, honestly).
            if mutant_record is not None:
                try:
                    reset_info = reset_tree_for_attempt(
                        case, pristine_tree, tree,
                        meta.get("red_baseline_tree_sha256"))
                except LoopError as exc:
                    meta["error"] = ("clean-slate reset failed before "
                                     "attempt %d: %s" % (attempt + 1, exc))
                    print("FATAL: %s" % meta["error"], file=sys.stderr)
                    break
                reset_note = {
                    "tree_reset": True,
                    "tree_reset_verified":
                        reset_info["tree_reset_verified"]}
                print("[RESET] scratch tree restored to RED baseline %s"
                      % reset_info["tree_sha256"][:12], flush=True)
            else:
                reset_note = {
                    "tree_reset": False,
                    "tree_reset_reason": "non-mutant tree: no recorded "
                                         "pristine baseline"}
        else:
            reset_note = {
                "tree_reset": False,
                "tree_reset_reason": "first attempt runs on the RED "
                                     "tree (no reset needed)"}
        note = ""
        if attempt > 0:
            prev = last_attempt.get("summary", "previous attempt failed")
            note = ("\n\nRETRY NOTE (attempt %d of %d): the previous patch "
                    "failed: %s. Diagnose again from the SAME trace and "
                    "propose a corrected unified diff."
                    % (attempt + 1, attempt_limit, prev[:600]))
        attempt_prompt = full_prompt + note
        fb_block = format_prior_attempts_block(prior_attempts)
        if fb_block:
            # Structured failure history (patch one-liner + resim +
            # checksum delta per prior attempt): separate context block,
            # prompt templates untouched, logged verbatim in
            # text_request.json below.
            attempt_prompt += "\n" + fb_block
        if loop_feedback:
            # No-op/stale-build signal from the previous resim: separate
            # context block (prompt templates untouched, hashes stable).
            attempt_prompt += "\n\nLOOP FEEDBACK: " + loop_feedback
        if attempt + 1 > MAX_ATTEMPTS:
            # Ladder attempts carry strictly more signal than attempts
            # 1-3 ever had: the full prior-attempts block above plus an
            # explicit directive to change repair class (context block
            # only -- prompt templates untouched, hashes stable -- and
            # logged verbatim in text_request.json below).
            attempt_prompt += "\n\n" + STRATEGY_CHANGE_DIRECTIVE
        # CONSENSUS pilot (opt-in --consensus; default OFF skips this whole
        # block so the single path below runs byte-identical). When active
        # AND the ladder cost chain reports accumulated spend under the
        # $0.50 cap, collect up to CONSENSUS_VOTES independent diagnoses
        # (same prompt, fresh calls; flakes retried inside
        # call_text_with_backoff, never counted as votes). UNANIMOUS or
        # 2-of-3 MAJORITY proceeds with the majority diagnosis; a 1-1-1
        # SPLIT (or fewer than 2 usable votes) consumes the attempt with
        # no patch and no resim burned (mirroring the diversity re-ask
        # rule) and carries all opinions into the next prior-attempts
        # block. Over cap degrades to single-diagnosis with OFF-cost-cap.
        consensus_note = ""
        consensus_winner = False
        consensus_votes_log: list = []
        if use_consensus:
            cok, cest, csrc = ladder_cost_ok(
                run_dir, tracked_spend_usd, attempts_consumed)
            if not cok:
                consensus_note = (
                    "consensus: OFF-cost-cap (est $%.4f via %s >= cap "
                    "$%.2f; single-diagnosis fallback)"
                    % (cest, csrc, LADDER_COST_CAP_USD))
                print("[CONSENSUS] %s" % consensus_note, flush=True)
            else:
                opinions: list = []  # (call, model_obj|None, salvaged, err)
                for _vote in range(CONSENSUS_VOTES):
                    try:
                        vcall, vwaits = call_text_with_backoff(
                            attempt_prompt)
                    except LiveAPIError as vexc:
                        # Exhausted transport sequence for this vote:
                        # excluded from votes, counted toward the bound.
                        transport_failures += 1
                        transport_waits_all.extend(
                            list(getattr(vexc, "transport_waits",
                                         None) or []))
                        meta["transport_retries"] = list(
                            transport_waits_all)
                        meta["transport_failures"] = transport_failures
                        print("[CONSENSUS] vote %d transport failure "
                              "(excluded from votes; %d/%d): %s"
                              % (_vote + 1, transport_failures,
                                 MAX_TRANSPORT_FAILURES, vexc), flush=True)
                        opinions.append((None, None, False, str(vexc)))
                        continue
                    tracked_spend_usd += ladder_text_usd(
                        vcall.get("usage"), vcall.get("model"))
                    transport_waits_all.extend(list(vwaits or []))
                    try:
                        vobj, vsalv = parse_model_output(vcall["content"])
                    except LoopError as vexc:
                        # Completed but unusable: an opinion that agrees
                        # with nothing (never invents a diff).
                        opinions.append((vcall, None, False, str(vexc)))
                        continue
                    opinions.append((vcall, vobj, vsalv, ""))
                meta["transport_retries"] = list(transport_waits_all)
                usable = [(c, o, s) for (c, o, s, _e) in opinions
                          if o is not None]
                consensus_votes_log = []
                for i, (_c, vo, vs, ve) in enumerate(opinions):
                    if vo is not None:
                        consensus_votes_log.append(
                            {"vote": i + 1,
                             "diagnosis": str(vo.get("diagnosis",
                                                     ""))[:160],
                             "locator": consensus_summarize(
                                 str(vo["patch_diff"])),
                             "salvaged": vs})
                    else:
                        consensus_votes_log.append(
                            {"vote": i + 1,
                             "diagnosis": "(unusable: %s)"
                                          % str(ve)[:120],
                             "locator": "(empty)",
                             "salvaged": False})
                diffs = [str(o["patch_diff"]) for (_c, o, _s) in usable]
                status, winner, detail = consensus_majority(diffs)
                if status != "split" and len(usable) >= 2:
                    wcall, wobj, wsalv = usable[winner]
                    call, model_obj, salvaged = wcall, wobj, wsalv
                    transport_waits = []
                    label = ("UNANIMOUS 3-0" if status == "unanimous"
                             else "MAJORITY 2-1")
                    consensus_note = (
                        "consensus: %s %s" % (label, detail))
                    consensus_winner = True
                    print("[CONSENSUS] attempt %d %s; proceeding with "
                          "vote %d" % (attempt + 1, consensus_note,
                                       winner + 1), flush=True)
                elif (transport_failures >= MAX_TRANSPORT_FAILURES
                        and len(usable) < 2):
                    # Transport dead (persistent flakes, no quorum):
                    # loud bounded stop, same shape as the single path.
                    meta["error"] = (
                        "live text API failed after %d transport failures "
                        "(consensus quorum unreachable): %s "
                        "(no heuristic fallback)"
                        % (transport_failures,
                           "; ".join(e for (_c, _o, _s, e) in opinions
                                     if e)[:300]))
                    print("FATAL: %s" % meta["error"], file=sys.stderr)
                    ladder_reason = ("transport-failures-present "
                                     "(consensus quorum unreachable)")
                    meta["ladder_used"] = False
                    meta["ladder_reason"] = ladder_reason
                    break
                else:
                    detail = " | ".join(
                        consensus_summarize(d) for d in diffs) or "no votes"
                    consensus_note = "consensus: SPLIT %s" % detail
                    print("[CONSENSUS] attempt %d %s (no patch, no "
                          "resim burned)" % (attempt + 1, consensus_note),
                          flush=True)
                    attempts_consumed += 1
                    meta["attempts_consumed"] = attempts_consumed
                    with open(api("text_request.json"), "w",
                              encoding="utf-8") as fh:
                        fh.write(json.dumps(
                            {"model": TEXT_MODEL, "endpoint": text_url(),
                             "prompt": attempt_prompt,
                             "prompt_sha256": prompt_hash,
                             "prompt_file": args.prompt,
                             "prompt_tokens": len(attempt_prompt.split()),
                             "trace_lines": counts["trace_lines"],
                             "trace_tokens": counts["trace_tokens"],
                             "case": args.case, "seed": args.seed,
                             "max_tokens": TEXT_MAX_TOKENS,
                             "attempt": attempt + 1,
                             "loop_feedback": loop_feedback,
                             "consensus": consensus_note,
                             "votes": consensus_votes_log},
                            indent=2) + "\n")
                    with open(api("text_response.json"), "w",
                              encoding="utf-8") as fh:
                        fh.write(json.dumps(
                            {"error": consensus_note,
                             "status": "SPLIT",
                             "votes": consensus_votes_log},
                            indent=2) + "\n")
                    meta["attempts"].append(
                        {**reset_note,
                         **{"attempt": attempt + 1,
                            "summary": "%s; vote logged, no patch "
                                       "applied, no resim burned"
                                       % consensus_note,
                            "consensus": consensus_note,
                            "votes": consensus_votes_log}})
                    for (_c, o, _s) in usable:
                        odiff = str(o["patch_diff"])
                        prior_attempts.append({
                            "attempt": attempt + 1,
                            "patch": "[%s] %s"
                                     % (consensus_summarize(odiff),
                                        patch_one_liner(odiff)),
                            "direction": direction_key(odiff),
                            "resim": "not-run (consensus SPLIT)",
                            "checksum_changed": "n/a"})
                    continue
        # DIAGNOSE (live only; transport flakes retry with backoff)
        if not consensus_winner:
            try:
                call, transport_waits = call_text_with_backoff(
                    attempt_prompt)
            except LiveAPIError as exc:
                # Transport flake, NOT a diagnostic attempt: budget
                # preserved, same attempt retried (bounded by
                # MAX_TRANSPORT_FAILURES).
                if note_transport_failure(exc):
                    break
                continue
        if not consensus_winner:
            tracked_spend_usd += ladder_text_usd(call.get("usage"),
                                                 call.get("model"))
            try:
                # Truncation salvage (v3-21 lesson): a max_tokens cut that
                # still yields a complete diagnosis+diff is repaired WITHOUT
                # burning a retry (salvaged=True below). Only a failed
                # salvage records an unparseable attempt and continues.
                model_obj, salvaged = parse_model_output(call["content"])
            except LoopError as exc:
                # Completed response, failed salvage: consumes one diagnostic
                # attempt (only transport flakes are exempt).
                attempts_consumed += 1
                meta["attempts_consumed"] = attempts_consumed
                last_attempt = {"attempt": attempt + 1,
                                "summary": "unparseable model output: %s" % exc,
                                "text_latency_ms": call["latency_ms"],
                                "text_request_id": call["request_id"],
                                "salvaged": False}
                meta["attempts"].append({**reset_note, **last_attempt})
                prior_attempts.append({
                    "attempt": attempt + 1,
                    "patch": "(unparseable model output)",
                    "direction": "(unparseable)",
                    "resim": "not-run",
                    "checksum_changed": "n/a"})
                print("[DIAGNOSE] attempt %d unparseable: %s"
                      % (attempt + 1, exc), flush=True)
                continue
        diagnosis_text = str(model_obj["diagnosis"])
        expected_text = str(model_obj.get("expected", ""))
        model_diff = str(model_obj["patch_diff"])
        transport_waits_all.extend(list(transport_waits or []))
        meta["transport_retries"] = list(transport_waits_all)
        # Attempt diversity (wave-2): a proposal with the same direction
        # (file+lines+op) as a prior patch (the v3-21 attempts-1-and-3
        # lesson) gets ONE extra model ask with an explicit avoid note.
        # No resim is burned for the duplicate; at most one re-ask per
        # run (a repeated duplicate is tolerated). Prompt templates are
        # untouched (context block only, v3 hash stable); the augmented
        # prompt is logged verbatim in text_request.json below.
        duplicate_info: dict = {}
        if (not consensus_winner
                and duplicate_reasks < MAX_DUPLICATE_REASKS
                and is_duplicate_direction(model_diff, prior_attempts)):
            duplicate_reasks += 1
            dup_key = direction_key(model_diff)
            dup_one = patch_one_liner(model_diff)
            duplicate_info = {"duplicate_direction": True,
                              "duplicate_patch": dup_one,
                              "duplicate_key": dup_key,
                              "reasked": True}
            print("[DIAGNOSE] attempt %d repeats prior direction %s; "
                  "re-asking once with an explicit avoid note (no resim "
                  "burned)" % (attempt + 1, dup_key), flush=True)
            attempt_prompt += (
                "\n\nAVOID REPEAT: your proposed patch repeats an earlier "
                "FAILED direction (%s: `%s`). Propose a DIFFERENT direction "
                "-- different file, lines, or add/remove/replace operation "
                "-- diagnose again from the SAME trace." % (dup_key, dup_one))
            try:
                call, transport_waits = call_text_with_backoff(
                    attempt_prompt)
            except LiveAPIError as exc:
                if note_transport_failure(exc):
                    break
                continue
            tracked_spend_usd += ladder_text_usd(call.get("usage"),
                                                 call.get("model"))
            transport_waits_all.extend(list(transport_waits or []))
            meta["transport_retries"] = list(transport_waits_all)
            try:
                model_obj, salvaged = parse_model_output(call["content"])
            except LoopError as exc:
                attempts_consumed += 1
                meta["attempts_consumed"] = attempts_consumed
                last_attempt = {"attempt": attempt + 1,
                                "summary": ("unparseable model output "
                                            "(re-ask): %s" % exc),
                                "text_latency_ms": call["latency_ms"],
                                "text_request_id": call["request_id"],
                                "salvaged": False,
                                **duplicate_info}
                meta["attempts"].append({**reset_note, **last_attempt})
                prior_attempts.append({
                    "attempt": attempt + 1,
                    "patch": "(unparseable model output)",
                    "direction": "(unparseable)",
                    "resim": "not-run",
                    "checksum_changed": "n/a"})
                print("[DIAGNOSE] attempt %d re-ask unparseable: %s"
                      % (attempt + 1, exc), flush=True)
                continue
            model_diff = str(model_obj["patch_diff"])
        attempts_consumed += 1
        meta["attempts_consumed"] = attempts_consumed
        meta["diagnosed"] = True
        print("[DIAGNOSE] attempt %d%s latency=%dms req=%s diagnosis=%.120s"
              % (attempt + 1, " SALVAGED" if salvaged else "",
                 call["latency_ms"], call["request_id"],
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
            tracked_spend_usd += float(cost_ledger.PRICING.get(
                img_info.get("model", visual.IMAGE_MODEL), {}).get(
                "usd_per_image", 0.0) or 0.0)
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

        # MULTIMODAL feedback (opt-in --multimodal; default OFF skips
        # this whole block so the proven single path stays
        # byte-identical). Feeds explain.png BACK into a second
        # diagnosis call (same trace window + first diagnosis + the
        # diagram): CONFIRM proceeds with the identical patch, REVISE
        # supersedes for PATCH (both + delta logged). Image failure,
        # cost-cap, or second-call failure degrades to
        # single-diagnosis and never blocks.
        multimodal_note = ""
        multimodal_detail: dict = {}
        if use_multimodal:
            if meta.get("explain") != "ok":
                multimodal_note = (
                    "multimodal: OFF-image-failed (explain FAILED; "
                    "single-diagnosis fallback)")
                print("[MULTIMODAL] %s" % multimodal_note, flush=True)
            else:
                cok, cest, csrc = ladder_cost_ok(
                    run_dir, tracked_spend_usd, attempts_consumed)
                if not cok:
                    multimodal_note = (
                        "multimodal: OFF-cost-cap (est $%.4f via %s >= "
                        "cap $%.2f; single-diagnosis fallback)"
                        % (cest, csrc, LADDER_COST_CAP_USD))
                    print("[MULTIMODAL] %s" % multimodal_note, flush=True)
                else:
                    mm_prompt = build_multimodal_prompt(
                        args.case, bundle["trace_block"],
                        diagnosis_text, model_diff,
                        str(img_info.get("sha256", "")))
                    with open(api("multimodal_request.json"), "w",
                              encoding="utf-8") as fh:
                        fh.write(json.dumps(
                            {"model": TEXT_MODEL,
                             "endpoint": text_url(),
                             "prompt": mm_prompt,
                             "prompt_sha256": sha256_text(mm_prompt),
                             "case": args.case, "attempt": attempt + 1,
                             "first_diagnosis_sha256": sha256_text(
                                 diagnosis_text),
                             "explain_sha256": str(
                                 img_info.get("sha256", ""))},
                            indent=2) + "\n")
                    try:
                        mm_call, mm_waits = call_text_with_backoff(
                            mm_prompt)
                    except LiveAPIError as mm_exc:
                        transport_waits_all.extend(
                            list(getattr(mm_exc, "transport_waits",
                                         None) or []))
                        meta["transport_retries"] = list(
                            transport_waits_all)
                        multimodal_note = (
                            "multimodal: OFF-second-call-failed (%s; "
                            "single-diagnosis fallback)" % (mm_exc,))
                        with open(api("multimodal_response.json"), "w",
                                  encoding="utf-8") as fh:
                            fh.write(json.dumps(
                                {"error": str(mm_exc),
                                 "status": "OFF-second-call-failed"},
                                indent=2) + "\n")
                        print("[MULTIMODAL] %s" % multimodal_note,
                              flush=True)
                    else:
                        tracked_spend_usd += ladder_text_usd(
                            mm_call.get("usage"), mm_call.get("model"))
                        transport_waits_all.extend(list(mm_waits or []))
                        meta["transport_retries"] = list(
                            transport_waits_all)
                        mm_verdict = parse_multimodal_output(
                            mm_call.get("content", ""),
                            diagnosis_text, model_diff)
                        with open(api("multimodal_response.json"), "w",
                                  encoding="utf-8") as fh:
                            fh.write(json.dumps(
                                {"raw": mm_call["raw"],
                                 "content": mm_call["content"],
                                 "decision": mm_verdict["decision"],
                                 "diagnosis": mm_verdict["diagnosis"],
                                 "patch_diff": mm_verdict["patch_diff"],
                                 "reason": mm_verdict["reason"],
                                 "revised": mm_verdict["revised"],
                                 "latency_ms": mm_call["latency_ms"],
                                 "request_id": mm_call["request_id"],
                                 "model": mm_call["model"],
                                 "usage": mm_call["usage"]},
                                indent=2) + "\n")
                        if mm_verdict["revised"]:
                            first_diag = diagnosis_text
                            first_diff = model_diff
                            diagnosis_text = str(
                                mm_verdict["diagnosis"])
                            model_diff = str(mm_verdict["patch_diff"])
                            multimodal_detail = {
                                "decision": "REVISE",
                                "reason": mm_verdict["reason"],
                                "first_diagnosis": first_diag,
                                "final_diagnosis": diagnosis_text,
                                "first_patch": patch_one_liner(
                                    first_diff),
                                "final_patch": patch_one_liner(
                                    model_diff),
                                "patch_changed": first_diff != model_diff,
                                "direction_before": direction_key(
                                    first_diff),
                                "direction_after": direction_key(
                                    model_diff),
                                "second_request_id": mm_call[
                                    "request_id"],
                            }
                            multimodal_note = (
                                "multimodal: REVISED (%s; "
                                "first %s -> final %s)"
                                % (mm_verdict["reason"],
                                   multimodal_detail["first_patch"],
                                   multimodal_detail["final_patch"]))
                            print("[MULTIMODAL] attempt %d %s"
                                  % (attempt + 1, multimodal_note),
                                  flush=True)
                        else:
                            multimodal_detail = {
                                "decision": "CONFIRM",
                                "reason": mm_verdict["reason"],
                                "first_diagnosis": diagnosis_text,
                                "final_diagnosis": diagnosis_text,
                                "first_patch": patch_one_liner(
                                    model_diff),
                                "final_patch": patch_one_liner(
                                    model_diff),
                                "patch_changed": False,
                                "second_request_id": mm_call[
                                    "request_id"],
                            }
                            multimodal_note = (
                                "multimodal: CONFIRMED (%s; "
                                "proceeding with identical patch %s)"
                                % (mm_verdict["reason"],
                                   multimodal_detail["final_patch"]))
                            print("[MULTIMODAL] attempt %d %s"
                                  % (attempt + 1, multimodal_note),
                                  flush=True)

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
                     "attempt": attempt + 1,
                     "loop_feedback": loop_feedback, **({"consensus": consensus_note} if consensus_note else {}), **({"multimodal": multimodal_note, "multimodal_detail": multimodal_detail} if multimodal_note else {})}, indent=2) + "\n")
            with open(api("text_response.json"), "w",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"raw": call["raw"], "content": call["content"],
                     "diagnosis": diagnosis_text, "expected": expected_text,
                     "parsed_keys": sorted(model_obj.keys()),
                     "salvaged": salvaged,
                     "latency_ms": call["latency_ms"],
                     "request_id": call["request_id"],
                     "model": call["model"], "usage": call["usage"],
                      **({"consensus": consensus_note,
                         "votes": consensus_votes_log}
                        if consensus_note else {}),
                      **({"multimodal": multimodal_note,
                         "multimodal_detail": multimodal_detail}
                        if multimodal_note else {})},
                    indent=2) + "\n")
            meta["diagnosed"] = True
            meta["text_latency_ms"] = call["latency_ms"]
            meta["text_request_id"] = call["request_id"]
            meta["model_id"] = call["model"]
            meta["api_usage"] = call["usage"]
            meta["retries"] = attempt
            meta["attempts"].append(
                {**reset_note, **{"attempt": attempt + 1,
                 "text_latency_ms": call["latency_ms"],
                 "text_request_id": call["request_id"],
                 "diagnosis_chars": len(diagnosis_text),
                 "salvaged": salvaged,
                 "patch_source_try": "withheld-triage",
                 "summary": "triage stop: diagnosis saved, patch withheld",
                 **duplicate_info},
                 **({"multimodal": multimodal_note,
                    "multimodal_detail": multimodal_detail}
                   if multimodal_note else {})})
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
                        "salvaged": salvaged,
                        "guard_ok": outcome["guard_ok"],
                        "patch_source_try": "model-verbatim",
                        "summary": summary,
                        **duplicate_info}
        if consensus_note:
            last_attempt["consensus"] = consensus_note
        if consensus_votes_log:
            last_attempt["votes"] = consensus_votes_log
        if multimodal_note:
            last_attempt["multimodal"] = multimodal_note
        if multimodal_detail:
            last_attempt["multimodal_detail"] = multimodal_detail
        if attempt + 1 > MAX_ATTEMPTS:
            # Ladder-attempt marker (attempts 1-3 entries stay untouched).
            last_attempt["ladder"] = True
            last_attempt["strategy_change"] = True
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
                meta["attempts"].append({**reset_note, **last_attempt})
                prior_attempts.append({
                    "attempt": attempt + 1,
                    "patch": patch_one_liner(model_diff),
                    "direction": direction_key(model_diff),
                    "resim": "not-run (guard-rejected; bootstrap refused)",
                    "checksum_changed": "n/a"})
                continue
            try:
                canon, _hits = canonical_diff(tree, case)
            except LoopError as exc:
                last_attempt["summary"] += "; bootstrap impossible: %s" % exc
                meta["attempts"].append({**reset_note, **last_attempt})
                prior_attempts.append({
                    "attempt": attempt + 1,
                    "patch": patch_one_liner(model_diff),
                    "direction": direction_key(model_diff),
                    "resim": "not-run (bootstrap impossible)",
                    "checksum_changed": "n/a"})
                continue
            bout = try_apply_diff(
                canon, tree, os.path.join(run_dir, "bootstrap.diff"), spec)
            last_attempt["patch_source_try"] = "canonical-bootstrap-gated"
            last_attempt["gate_signal"] = hit
            if not bout["applied"]:
                last_attempt["summary"] += (
                    "; bootstrap REJECTED: %s" % bout["reasons"])
                meta["attempts"].append({**reset_note, **last_attempt})
                prior_attempts.append({
                    "attempt": attempt + 1,
                    "patch": patch_one_liner(model_diff),
                    "direction": direction_key(model_diff),
                    "resim": "not-run (guard-rejected; bootstrap rejected)",
                    "checksum_changed": "n/a"})
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
        meta["attempts"].append({**reset_note, **last_attempt})
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
                 "attempt": attempt + 1,
                 "loop_feedback": loop_feedback, **({"consensus": consensus_note} if consensus_note else {}), **({"multimodal": multimodal_note, "multimodal_detail": multimodal_detail} if multimodal_note else {})}, indent=2) + "\n")
        with open(api("text_response.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"raw": call["raw"], "content": call["content"],
                 "diagnosis": diagnosis_text, "expected": expected_text,
                 "parsed_keys": sorted(model_obj.keys()),
                 "salvaged": salvaged,
                 "latency_ms": call["latency_ms"],
                 "request_id": call["request_id"],
                 "model": call["model"], "usage": call["usage"],
                 **({"consensus": consensus_note, "votes": consensus_votes_log}
                    if consensus_note else {}),
                 **({"multimodal": multimodal_note,
                    "multimodal_detail": multimodal_detail}
                    if multimodal_note else {})},
                indent=2) + "\n")
        meta["text_latency_ms"] = call["latency_ms"]
        meta["text_request_id"] = call["request_id"]
        meta["model_id"] = call["model"]
        meta["api_usage"] = call["usage"]
        meta["retries"] = attempt

        # RESIM + VERIFY (integrity-guarded: snapshot the patched
        # sources into the bundle, rebuild, re-hash; a drift forces ONE
        # clean rebuild; an identical checksum/tohost flags a
        # behaviorally-null resim for the next attempt's feedback).
        print("[RESIM] rebuilding patched tree...", flush=True)
        try:
            verdict_after, resim_integrity = resim_with_integrity(
                tree, case, args.seed,
                art("boot_after.log"), art("verdict_after.json"),
                run_dir=run_dir, verdict_before=verdict_before)
        except Exception as exc:
            last_attempt["summary"] += "; RESIM crashed: %r" % (exc,)
            meta["error"] = "RESIM failed: %r" % (exc,)
            print("FATAL: %s" % meta["error"], file=sys.stderr)
            break
        last_attempt["resim_integrity"] = {
            "stale-tree-suspect":
                resim_integrity.get("stale-tree-suspect", False),
            "clean_rebuilds": resim_integrity.get("clean_rebuilds", 0),
            "no_behavioral_effect":
                resim_integrity.get("no_behavioral_effect", False),
            "integrity_detail":
                resim_integrity.get("integrity_detail", ""),
        }
        meta["attempts"][-1].update(last_attempt["resim_integrity"])
        if resim_integrity.get("stale-tree-suspect"):
            last_attempt["stale-tree-suspect"] = True
            meta["attempts"][-1]["stale-tree-suspect"] = True
            print("[RESIM] tree drifted under build -- "
                  "clean rebuild forced (x1): %s"
                  % resim_integrity.get("integrity_detail", ""),
                  flush=True)
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
            if not ladder_used:
                ladder_reason = "green-within-base (ladder not needed)"
                meta["ladder_reason"] = ladder_reason
            break
        last_attempt["summary"] += (
            "; RESIM still RED (tohost=%s)" % verdict_after.get("tohost"))
        meta["attempts"][-1]["summary"] = last_attempt["summary"]
        if resim_integrity.get("no_behavioral_effect"):
            # v3-21 signal: checksum_after == checksum_before and tohost
            # unchanged -- the patch (or the build of it) had no
            # observable effect. Flag it and arm the next attempt's
            # LOOP FEEDBACK block (prompt templates untouched).
            last_attempt["no_behavioral_effect"] = True
            meta["attempts"][-1]["no_behavioral_effect"] = True
            meta["no_behavioral_effect"] = True
            loop_feedback = build_loop_feedback(
                attempt + 1, verdict_before, verdict_after)
            print("[VERIFY] %s" % NO_BEHAVIORAL_EFFECT_MSG, flush=True)
        else:
            # Behavioral change observed: stale feedback must not leak
            # into the next attempt's diagnosis.
            loop_feedback = ""
        # FAILURE FEEDBACK (v3-21 lesson): the next attempt's prompt gets
        # the patch one-liner + resim outcome + checksum delta, verbatim
        # in text_request.json. An applied patch that changed nothing
        # (checksum unchanged) must be visible, not just "still RED".
        after_checksum = verdict_after.get("checksum")
        before_checksum = meta.get("checksum_before")
        if after_checksum is None or before_checksum is None:
            changed = "n/a"
        else:
            changed = ("no" if after_checksum == before_checksum
                       else "yes")
        prior_attempts.append({
            "attempt": attempt + 1,
            "patch": patch_one_liner(final_patch),
            "direction": direction_key(final_patch),
            "resim": "RED (tohost=%s)" % verdict_after.get("tohost"),
            "checksum_changed": changed})
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
