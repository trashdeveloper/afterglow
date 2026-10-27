# MEGA PLAN: Afterglow — the SWE-agent for RTL (11/10 edition)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the SWE-agent of RTL — an agentic repair loop so grounded, guarded, and proven that it maxes every hackathon rubric line (and contends for the Agentic + Community bonuses), built in 9 days without missing the submission.

**Architecture:** Three layers, built in order. (1) SHIP — the existing system lands in-window with Docker, video, submission. (2) THINK — the Signal Harvest plan (already written, included here as Phase B) gives the model senior-engineer context. (3) WOW — agentic depth (patch tournament, autonomy ladder), operator UX (TUI dashboard, `afterglow init`), and eval prestige (difficulty-graded board, regression mode). Every layer is demoable alone; no layer can sink the submission.

**Tech Stack:** Python stdlib only + pillow, Verilator 5.x, live Muse Spark Text + Image (integration only), Docker for repro.

**Spec:** `docs/superpowers/specs/2026-10-26-rtl-debug-framework-design.md`; detail plans: `docs/superpowers/plans/2026-10-26-rtl-debug-framework.md` (DONE), `docs/superpowers/plans/2026-10-26-signal-harvest.md` (Phase B here).

## Global Constraints

- All implementation commits 2026-10-27 – 2026-11-04; planning/doc-only tonight.
- Verilator-only; no UVM/cocotb/TB-gen/formal/cloud/multi-sim. Violations get cut, not debated.
- Full prompt <= 2500 tokens; pack budget 900; patch rules unchanged (allowlist, <=40 lines, no cheats, fail-loud).
- TDD + fresh-reviewer gate per task; eval reads bundles only; key lives in session env, never on disk.
- Default stays v1 until a challenger goes locked 3/3 live. No silent flips.

## Rubric Map (where the 11/10 comes from)

- Impact 30%: regression mode (nightly board over 8+6+3 cases), 2nd fresh design (generality proof), engineer-hours-saved metric from time-to-green, `afterglow init` (any RTL repo onboarded in one command).
- Meta Tooling 25%: Text decides + Image explains (proven load-bearing) + patch tournament (best-of-N agentic selection) + autonomy ladder (triage → auto → full) + open-weights story (all deterministic code runs anywhere; only the decider needs the API).
- Depth 20%: harvest algorithms (fingerprint, cone slice, module graph, scored packer), guard policy engine, proof bundles, per-class eval, CI gates.
- Demo 25%: TUI live dashboard (watch RED→GREEN with timer + checksums), 180s uncut video, one-liner Docker, gallery-ready README.
- Bonuses: Agentic $5K (ladder + tournament + memory); Community $10K (gallery + Discord build-log); Viral $20K (X thread with video + numbers).

## Review Focus

- Feature count is the enemy: any task that slips its day gets cut to its demoable core, never extended — the submission (Nov 4, 9PM EST) is immovable.
- Tournament cost explosion: best-of-N multiplies API calls; cap N=3 and count tokens per case in meta, or the demo budget dies.
- TUI must never touch loop logic: read-only over `runs/` + subprocess tail; a dashboard bug must be unable to RED a run.
- `afterglow init` must not phone home or copy foundations: reference-by-pin only (compliance rule survives).
- Second fresh design must be stranger-authored and quarantined like the first, or the generality claim is void.

---

## Phase A — SHIP (Oct 27, P0, win-protecting)

### Task A1: Land the 7 window commits

**Files:** all uncommitted work (loop, context, tests, prompts, docs, ledger)
- [ ] **Step 1:** `git status --short` review; stage in the 7-commit sequence (core → guard → loop → triage/cli → mining → context → prompts).
- [ ] **Step 2:** Commit one by one with numbers (`eval 8/8`, test counts) in messages, dates >= 2026-10-27.
- [ ] **Step 3:** `git log --oneline --since=2026-10-27` shows 7+ implementation commits; push to public repo.

### Task A2: Docker cold-run + non-author test

**Files:** Modify `Dockerfile`, `repro/run.sh` if the cold run exposes gaps; Create `repro/TESTED.md` entry.
- [ ] **Step 1:** Cold `docker build` + `docker run ... --case priv-tap-misclass`-equivalent (real locked id) on this machine; must go RED→GREEN < 10 min, checksums match EXPECTED.
- [ ] **Step 2:** A non-author human runs the same one-liner; log their run-id + wall time in TESTED.md.
- [ ] **Step 3:** Fix whatever breaks (toolchain cache, weft pin) and re-run until Step 1 passes twice in a row.

## Phase B — THINK (Oct 28–29, the harvest plan, included by reference)

### Task B1–B7: Signal Harvest, verbatim from `docs/superpowers/plans/2026-10-26-signal-harvest.md`

- [ ] **Step 1:** Execute harvest Tasks 1–6 (recon → fingerprint → slice → graph → packer → v3 wiring), each TDD + reviewer gate.
- [ ] **Step 2 (key-gated, with user):** Ablation v1/v2/v3 on locked 3 + stranger set; flip default only on locked 3/3.
- [ ] **Step 3:** If any harvest task slips its day, ship its demoable core (lexical fallback for slice, drop-order for packer) and move on.

## Phase C — AGENTIC DEPTH (Oct 29–30, the "better SWE-agent" features)

### Task C1: Patch tournament (best-of-3, smallest green diff wins)

**Files:** Create `agent/tournament.py` (`run_tournament(n=3)`); Modify `agent/loop.py` (one call site, `--tournament` flag, default OFF); Test `tests/test_tournament.py`.
**Interfaces:** Consumes `loop` single-attempt PATCH→RESIM as `attempt_once(prompt, seed_i) -> (patch, verdict)`; Produces `select_winner(results) -> patch` (green only, fewest diff lines, tie-break fastest); meta `tournament: {n, winner_seed, tokens}`.

- [ ] **Step 1: Write the failing test.** Fixture results (2 green 1 red, greens 8 vs 3 lines) → winner is the 3-line patch; all-red → returns None + stays RED.
- [ ] **Step 2:** Run it, watch it fail (import error).
- [ ] **Step 3:** Implement `agent/tournament.py` + flag wiring (v1 path untouched when flag off).
- [ ] **Step 4:** Full suite + CI green.
- [ ] **Step 5:** Reviewer gate (cap enforcement N≤3, token accounting, no default-path change).

### Task C2: Autonomy ladder (triage → auto → full) with per-rung proof

**Files:** Modify `agent/loop.py` (`--ladder full` climbs triage→auto→tournament automatically on success); Test: extend `tests/test_triage.py`.
**Interfaces:** Consumes rung exit codes (2 triage / 0 green / 1 red); Produces `meta.ladder: [rungs attempted]` + one bundle per rung (immutable history, no overwrites).

- [ ] **Step 1: Failing test** — ladder on a fixture that greens at auto: rungs == [triage, auto], attempts stop, bundle count == 2.
- [ ] **Step 2–4:** Fail → implement → pass + suite green.
- [ ] **Step 5:** Reviewer gate (a rung may never edit a previous rung's bundle).

### Task C3: Fix memory v2 (Jaccard retrieval over fingerprints)

**Files:** Modify `agent/harvest.py` (`recall_similar(fp, k)`); Test: extend `tests/test_harvest_fingerprint.py`.
**Interfaces:** Consumes `signature_index`; Produces ranked similar greens by token-set Jaccard on normalized traces (stdlib only).

- [ ] **Step 1: Failing test** — 3 indexed greens, query closest to #2 → ranked #2 first with score in counts.
- [ ] **Step 2–4:** Fail → implement → pass + suite green.
- [ ] **Step 5:** Reviewer gate (scores logged, never asserted-on for correctness — retrieval advises, re-sim decides).

## Phase D — OPERATOR WOW (Oct 30–31, demo fuel)

### Task D1: TUI live dashboard (read-only over runs/)

**Files:** Create `afterglow/tui.py` (stdlib `curses` or plain ANSI tail mode — recon picks; no new deps); Test: `tests/test_tui.py` (render functions on fixture bundles, no terminal needed).
**Interfaces:** Consumes `runs/*/meta.json` + `boot_after.log` tails; Produces a live board: case, rung, attempt, elapsed, last diagnosis line, checksum status. Zero imports from `agent.loop`.

- [ ] **Step 1: Failing test** — render fixture bundle → contains case id, status, elapsed format.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5:** Reviewer gate (prove zero loop imports: `grep -n "^import\|^from" afterglow/tui.py` shows no agent.loop).

### Task D2: `afterglow init` (one-command onboarding for any RTL repo)

**Files:** Create `afterglow/init_cmd.py`; Test `tests/test_init.py` (tmp dirs only).
**Interfaces:** Consumes a target repo path; Produces `afterglow.yaml` (sim command template, rtl globs, guard allowlist starter) + pin reference (never copies foundations).

- [ ] **Step 1: Failing test** — init on fixture repo writes valid yaml with pinned foundation ref, no copied RTL.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5:** Reviewer gate (compliance: foundation referenced, never vendored).

### Task D3: Second fresh design + regression mode

**Files:** Create `eval/regress.py` (nightly board runner over all case sets); new stranger design authored by someone else, quarantined like SERV.
**Interfaces:** Consumes `eval/cases.yaml` + stranger sets; Produces `eval/regression.md` (generated) with per-class rates.

- [ ] **Step 1:** Stranger authors fault on unseen design; quarantine it (no fixes leak into prompts).
- [ ] **Step 2:** `afterglow run --spec` live; log RED→GREEN or RED-with-logs.
- [ ] **Step 3:** `eval/regress.py` runs everything, writes the board; reviewer checks numbers match bundles.

## Phase E — SUBMIT (Nov 1–4, immovable)

### Task E1: Video + README + submission text
- [ ] 180s cut per video proof rules (uncut PATCH→GREEN minute, checksums readable, captions); test on one non-hardware viewer.
- [ ] README split header + numbers; submission text ≤150 words.
- [ ] Gallery post + Discord build-log thread (community groundwork).

### Task E2: Viral thread + office hours + submit
- [ ] X thread: problem → live RED→GREEN clip → numbers → repo link; submit link via platform.
- [ ] Last office-hours questions (Nov 3–4); submit before Nov 4 9PM EST with margin (target Nov 4 noon).

## Cut List (explicit non-goals — suggested, never snuck back)

Multi-simulator support · UVM/cocotb · TB generation · formal verification · cloud service / hosted UI · `Muse Voice` narration (gimmick risk) · anything needing a new dependency.

## Self-Review

1. **Spec coverage:** harvest spec → Phase B; SWE-agent parity (tournament, ladder, memory, graded eval) → Phase C; demo/impact/bonus → Phases D–E; ship → Phase A. Covered.
2. **Step scan:** every step names one action + checkable result; bodies only for scorer/slice/tournament-winner rules the signatures don't determine. Fixed inline.
3. **Type consistency:** `attempt_once -> (patch, verdict)`, `select_winner -> patch | None`, `recall_similar(fp, k) -> ranked list`, meta keys (`tournament`, `ladder`, `harvest_*`) distinct across phases. Consistent.
4. **Review Focus:** all five lines pinned (slip rule, tournament cap, TUI isolation with grep proof, init compliance, stranger quarantine).
5. **Proportion:** this mega plan delegates detail to the two sub-plans and specifies only new interfaces — it is shorter than the code it will produce. OK.
