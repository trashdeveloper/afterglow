# Afterglow RTL Debug Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract Afterglow's proven RED→VERIFY loop into a reusable core with a SimBackend/CaseSpec plugin surface, triage-first default with `--auto` escalation, and prove it on stranger bugs.

**Architecture:** Keep the working loop intact; extract in place via `git mv` into `afterglow_core/`, adding only `types.py` (Verdict/CaseSpec/SimBackend contract), a Verilator reference backend adapter over `sim/vbackend.py`, per-project guard policy, a triage stop, and a new `afterglow run --spec` entry point. No new simulators, no TB generation.

**Tech Stack:** Python 3.12 stdlib + pillow (existing), Verilator 5.x, live Muse Spark Text + Muse Image APIs (integration runs only, never unit tests).

**Spec:** `docs/superpowers/specs/2026-10-26-rtl-debug-framework-design.md` — the plan argues from the spec; executors read both.

## Global Constraints

- Verilator + hand-written testbenches only; no UVM, no cocotb, no TB/oracle generation in this plan.
- CPU-only sims; no new pip dependencies (stdlib + pillow only).
- Text API is the sole diagnosis decider; no mocks, no canned responses, no regex diagnosis — unit tests use static fixtures and real local functions, never model calls.
- Every green claim ships a full proof bundle (trace window, prompt hash, raw model I/O + latency/ids, `explain.png` provenance, diff, checksums); never edit a bundle after creation.
- Patch guardrails stay mandatory: allowlisted files only, ≤40 lines default (overridable with logged justification), lint clean, ≤2 retries, forbidden-strings scan clean.
- No implementation commits before 2026-10-27; all work commits dated Oct 27–Nov 4 with numbers (`eval 8/8, p50 71s, prompt v1`).
- Trace window ≤200 lines, total prompt ≤2500 tokens, hard asserts.

## Review Focus

- A user sim that exits 0 even when the design fails — Verdict must come from oracle output, never the exit code. Pinned in Task 1: `test_verdict_ignores_exit_code` asserts `pass=False` on a zero-exit RED log.
- An oracle whose pass/fail strings are inverted from ours (`ERROR` vs `tohost: PASS`) — CaseSpec must declare its own oracle markers. Pinned in Task 2: `test_custom_oracle_markers` with a foreign-style log fixture.
- A failing run with no TRAP line at all — collector must still center a valid ≤200-line window with verdict attached. Pinned in Task 3: `test_window_without_trap` on a TRAP-less fixture.
- A boot log whose failing window blows the 2500-token cap — collect must hard-fail loudly, never silently truncate. Pinned in Task 3: `test_oversize_prompt_fails_loud`.
- A resim binary that crashes (nonzero, no verdict file) — loop must record the crash in meta and stay RED, never claim green. Pinned in Task 4: `test_resim_crash_stays_red` with a crashing fake backend script.

---

### Task 1: Core contract (`Verdict`, `CaseSpec`, `SimBackend`)

**Files:**
- Create: `afterglow_core/__init__.py`, `afterglow_core/types.py`
- Test: `tests/test_verdict.py` (stdlib asserts, `python3 tests/test_verdict.py -v`)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Verdict(pass: bool, evidence: dict)` — `pass` always from oracle output, never exit code; `CaseSpec(case_id: str, project: str, files: list, oracle_pass: str, oracle_trap: str, budget_s: int, allowlist: list, lint_cmd: str)`; `SimBackend` protocol with `build(tree) -> None`, `run(seed: int) -> Verdict`. Later tasks import these names verbatim.

- [ ] **Step 1: Write the failing test** `tests/test_verdict.py` with `test_verdict_ignores_exit_code` (zero-exit RED log + `oracle_pass="tohost: PASS"` absent → `pass is False`) and `test_verdict_pass` (oracle marker present → `pass is True`).
- [ ] **Step 2: Run test to verify it fails** Run: `python3 tests/test_verdict.py -v` Expected: FAIL with "No module named 'afterglow_core'".
- [ ] **Step 3: Implement `Verdict`, `CaseSpec`, `SimBackend` in `afterglow_core/types.py`** with `decide_verdict(log_text: str, oracle_pass: str) -> Verdict` (substring match on oracle marker, exit code ignored).
- [ ] **Step 4: Run test to verify it passes** Run: `python3 tests/test_verdict.py -v` Expected: PASS, no other output.
- [ ] **Step 5: Commit** `git add afterglow_core tests/test_verdict.py && git commit -m "feat(core): Verdict/CaseSpec/SimBackend contract, exit-code-independent"` (in-window date).

### Task 2: Per-project guard policy

**Files:**
- Create: `afterglow_core/policy.py`
- Modify: none (loop.py integration comes in Task 3).
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: `CaseSpec` from Task 1 (`afterglow_core.types`).
- Produces: `check_diff(diff_text: str, spec: CaseSpec, rtl_root: str) -> (ok, reasons, info)` — same rules as `agent/guard.py:check` (allowlist subset, ≤40 lines default, whitespace/binary rejection, forbidden patterns, per-file lint via `spec.lint_cmd`), plus `test_custom_oracle_markers` fixture support.

- [ ] **Step 1: Write the failing test** `tests/test_policy.py` with `test_custom_oracle_markers` (foreign log with `RESULT: OK` marker → Verdict pass via spec-declared `oracle_pass="RESULT: OK"`) and `test_allowlist_enforced` (diff touching a non-allowlisted file → `ok is False`).
- [ ] **Step 2: Run test to verify it fails** Run: `python3 tests/test_policy.py -v` Expected: FAIL with "No module named 'afterglow_core.policy'".
- [ ] **Step 3: Implement `check_diff` in `afterglow_core/policy.py`** delegating line-count/diff-shape checks to the existing `agent/guard.py` helpers, allowlist/lint taken from `spec`.
- [ ] **Step 4: Run tests to verify they pass** Run: `python3 tests/test_policy.py -v && python3 tests/test_verdict.py -v` Expected: all PASS.
- [ ] **Step 5: Commit** `git add afterglow_core/policy.py tests/test_policy.py && git commit -m "feat(core): per-project guard policy from CaseSpec"` (in-window date).

### Task 3: Triage-first loop mode

**Files:**
- Modify: `agent/loop.py` (add `--triage` default-stop + `--auto` flag; default run stops after DIAGNOSE+EXPLAIN with diagnosis + proof saved, exit code 2 meaning "triage complete, patch withheld").
- Test: `tests/test_triage.py` (static fixtures: 9-line RED window + verdict JSON, no model calls).

**Interfaces:**
- Consumes: `Verdict`, `CaseSpec` (Task 1), `check_diff` (Task 2), existing `collect()` signature `collect(boot_log, verdict_json, prompt_text) -> dict`.
- Produces: loop exit codes `0` green / `1` red / `2` triage-withheld; `meta.json` gains `mode: "triage"` vs `"live"`; `fixed unaided` counts only full-auto (`--auto`) runs.

- [ ] **Step 1: Write the failing test** `tests/test_triage.py` with `test_window_without_trap` (TRAP-less fixture → window ≤200 lines, verdict attached) and `test_oversize_prompt_fails_loud` (bloated trace fixture → `collect()` raises `AssertionError`, never truncates).
- [ ] **Step 2: Run test to verify it fails** Run: `python3 tests/test_triage.py -v` Expected: FAIL (helpers not wired to fixture paths yet).
- [ ] **Step 3: Implement triage stop in `agent/loop.py`**: after EXPLAIN, if not `--auto`, write bundle with `patch.diff` empty + `meta.json` `mode: "triage"`, print diagnosis path, `return 2`.
- [ ] **Step 4: Run tests to verify they pass** Run: `python3 tests/test_triage.py -v` Expected: PASS; plus `python3 -m py_compile agent/loop.py`.
- [ ] **Step 5: Commit** `git add agent/loop.py tests/test_triage.py && git commit -m "feat(loop): triage-first default, --auto escalates to patch"` (in-window date).

### Task 4: `afterglow run --spec` on a stranger case

**Files:**
- Create: `afterglow_core/verilator.py`, `afterglow` (executable CLI)
- Test: `tests/test_spec_run.py` plus live integration on one existing case.

**Interfaces:**
- Consumes: `SimBackend` (Task 1), `check_diff` (Task 2), triage/`--auto` loop (Task 3).
- Produces: `afterglow run --spec sim/targets/<id>.yaml [--auto] [--seed N] [--run-id ID]` → same `runs/<id>/` bundle shape; `test_resim_crash_stays_red` pins crash handling.

- [ ] **Step 1: Write the failing test** `tests/test_spec_run.py` with `test_resim_crash_stays_red` (a `SimBackend` whose `run()` raises → bundle `meta.json` records `error`, exit `1`, never green).
- [ ] **Step 2: Run test to verify it fails** Run: `python3 tests/test_spec_run.py -v` Expected: FAIL with "No module named 'afterglow_core.verilator'".
- [ ] **Step 3: Implement `VerilatorBackend(SimBackend)` in `afterglow_core/verilator.py`** wrapping `sim/vbackend.py:build/run_case` (build via `verilator --binary --timing`, verdict via oracle marker, checksum over deterministic lines only) and the `afterglow` CLI dispatching to the loop with a `CaseSpec` loaded from the YAML path.
- [ ] **Step 4: Run tests to verify they pass** Run: `python3 tests/test_spec_run.py -v` Expected: PASS; then live: `MODEL_API_KEY=... afterglow run --spec sim/targets/uart-start-half.yaml --auto --seed 42` Expected: GREEN exit 0, bundle complete (proves wiring on a real case).
- [ ] **Step 5: Commit** `git add afterglow_core/verilator.py afterglow tests/test_spec_run.py && git commit -m "feat(cli): afterglow run --spec on any CaseSpec"` (in-window date).

### Task 5: Historical-bug set, quarantine, and metrics

**Files:**
- Create: `eval/stranger.yaml`, `eval/stranger_results.md` (generated), `scripts/mine_bugs.sh`
- Test: live loop runs only (no unit test; proof = bundles + board numbers).

**Interfaces:**
- Consumes: `afterglow run --spec` (Task 4).
- Produces: `eval/stranger.yaml` entries `{repo, commit, bug_desc, ground_truth_fix, manual_baseline}` mined by the workforce; quarantined author-written PicoRV32 faults kept out of the builders' context until run day; metrics = unseen-fix rate + time-to-green vs baseline.

- [ ] **Step 1: Write the failing check** `scripts/mine_bugs.sh` asserting `eval/stranger.yaml` lists ≥3 bugs with repo+commit+ground-truth+baseline fields; run it. Expected: FAIL ("stranger.yaml missing").
- [ ] **Step 2: Mine and record ≥3 historical bugs** with ground-truth fixes and manual-baseline hours into `eval/stranger.yaml`; re-run the script. Expected: PASS.
- [ ] **Step 3: Run `afterglow run --spec` per stranger bug** (`--auto`, fresh run-ids). Expected: bundles in `runs/`, board numbers in `eval/stranger_results.md` generated from bundles only.
- [ ] **Step 4: Verify metrics honestly** `unseen-fix rate` + `time-to-green vs baseline`; reds stay visible with run-ids. Expected: numbers read from bundles, never hand-edited.
- [ ] **Step 5: Commit** `git add eval/stranger.yaml eval/stranger_results.md scripts/mine_bugs.sh && git commit -m "eval: stranger-bug set + metrics (fix rate, time-to-green)"` (in-window date).

### Task 6: Fresh-design acceptance + framework docs

**Files:**
- Create: `docs/ADAPTER.md`, `eval/ACCEPTANCE.md`
- Test: acceptance run on a fresh design (prover = workforce, per design decision).

**Interfaces:**
- Consumes: Tasks 1–4 (core + CLI).
- Produces: adapter guide (config-file onboarding, worked example) and `eval/ACCEPTANCE.md` log (design, setup time, run-id, result).

- [ ] **Step 1: Write the failing check** a script asserting `eval/ACCEPTANCE.md` records setup minutes ≤30 and a run-id. Expected: FAIL (file missing).
- [ ] **Step 2: Perform the acceptance run** adapt a fresh Verilator design via config only, run triage then `--auto`, log minutes + run-id honestly.
- [ ] **Step 3: Write `docs/ADAPTER.md`** from what the run actually required (no invented steps).
- [ ] **Step 4: Verify gate set** `bash scripts/ci.sh` ALL PASS + `python3 eval/score.py` unchanged 8/8 + stranger board numbers present.
- [ ] **Step 5: Commit** `git add docs/ADAPTER.md eval/ACCEPTANCE.md && git commit -m "docs: adapter guide + fresh-design acceptance log"` (in-window date).

### Task 7: `afterglow watch` — no-YAML entry (plan addendum Oct 31)

**Files:**
- Create: `agent/watch.py`, `tests/test_watch.py`
- Modify: `afterglow` (watch subcommand, +43/-1)

**Interfaces:**
- Consumes: existing `loop.main(["--spec", path, "--triage", ...])` entry point only.
- Produces: CaseSpec draft from user's own sim argv + log output, confirmed before write.

- [x] **Step 1: Write the failing tests** (15 tests, red-first ModuleNotFoundError).
- [x] **Step 2: Implement** (run_cmd/exit capture, rtl extraction, marker pick, loud placeholders, confirm/--yes, write_spec, chain_to_triage; stdlib only, no model calls in plumbing).
- [x] **Step 3: Run green + gate** (15/15, 1933 suite OK, CI ALL PASS, independent re-review APPROVE).
- [x] **Step 4: Report proof.** Committed 3ac584d (15/15, 1933, CI, APPROVE).

### Task 8: CLI polish — errors, progress, simpler `run` (Nov 1)

**Files:**
- Modify: `afterglow` (positional SPEC, `--debug`/`--quiet`, error wrapper), `agent/loop.py` (flags, run-id, stderr progress)
- Create: `tests/test_cli_polish.py` (10 tests)

- [x] **Step 1: Write the failing tests** (10 tests, red-first missing attrs).
- [x] **Step 2: Implement** (single-line stderr errors, TTY-only progress, positional spec + auto run-id; stdlib only).
- [x] **Step 3: Run green + gate** (10/10, 1943 suite OK, CI ALL PASS, independent re-review APPROVE).
- [x] **Step 4: Report proof.** Committed d0b28e8 (10/10, 1943, CI, APPROVE).
