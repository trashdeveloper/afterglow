# Afterglow RTL Debug Framework — Design (proposal, needs approval)

**Goal:** Turn Afterglow from a one-loop demo (8 PicoRV32 cases, 8/8 green) into the framework verification engineers drop into their own Verilator flow to get diagnosed, patched, re-verified fixes with proof.

**Why:** Simulation + verification is ~70% of chip effort. Today's loop proves trace-reading compresses from days to minutes on our cases; a framework generalizes that to anyone's RTL.

## Goals
- G1: Engineer points framework at their Verilator sim + oracle, gets RED→GREEN with proof, zero framework changes.
- G2: Autonomy knob per run: triage-first default (diagnose + proof, ask before RTL edits), escalate to full auto-fix per project policy.
- G3: Proof-bundle law kept as the signature: trace window, prompt hash, raw model I/O with latency/ids, `explain.png` provenance, diff, checksums.
- G4: Numbers that win: unseen-fix rate + time-to-green vs manual baseline on real historical bugs.

## Non-goals / anti-goals
- NG1: No new simulators (Verilator-only depth), no TB/oracle generation (user brings both), no open-weights chase.
- AG1 (failure even if it works): only runs on our 8 cases — must prove on stranger bugs.
- AG2: needs an expert to set up; AG3: fix without verifiable re-sim.

## Constraints
- Solo build, ~8 days, CPU-only sims, live Spark Text+Image API; all implementation commits Oct 27–Nov 4 (window rule).

## Approaches
- **A. Core + plugin surface (recommended, picked by answers):** extract loop/guard/collector/report into stable core; contracts `SimBackend` (build→run→verdict), `CaseSpec` (files, oracle, budget), bring-your-own TB; reference Verilator backend; CLI as first thin client and unseen-bug prover. Cost: biggest build; payoff: kills AG1 head-on, embeds in-flow.
- **B. CLI wrapper (dropped):** fastest unseen-bug proof but a tool, not a framework; can't embed in UVM flows.
- **C. Triage-only (dropped):** surrenders the closed loop + `fixed unaided` numbers; against the autonomy pick.

## Decided (from conversation)
- User: verification engineer inside their flow; Verilator + hand TBs; user brings sim + oracle; triage-first default; proof kept; historical-bug proof; live unseen-fix demo; chat-assistant contrast; lean constraints; time-primary metrics.

## Left to the builder
- Exact module boundaries (`afterglow-core` layout), `SimBackend` method signatures, adapter-kit shape, autonomy policy format, historical-bug sourcing order. To be settled in `writing-plans` plan with TDD.

## Builder-check resolutions (reviewer round, 2026-10-26)

**Answered from project files:**
- Real case list = AGENTS.md §5 real ids (picosoc-uart-div, picorv32-fetch-misalign, uart-tx-div + 5 stress); PLAN.md's stub names are stale and lose on conflict per PLAN's own rule. Baseline: 8/8 green on real ids, 6/8 target exceeded.
- Backends: hand-written Verilator TBs only; UVM explicitly out of scope.
- Window rule: core is extracted by refactor commits dated Oct 27–Nov 4 into an `afterglow-core/` subdirectory; no pre-window code moves; foundation/entry split unchanged.

**My calls (correct me):**
- Verdict form: `Verdict{pass: bool, evidence: map}` — backend returns pass + free-form evidence; `tohost`/`checksum`/`cycles` stay as the Verilator reference-backend convention, optional generically.
- Guardrails stay mandatory in core but parameterized per project: `CaseSpec` declares allowlist paths + lint command; 40-line cap stays the default, overridable with logged justification. Forbidden-strings scan unchanged.
- Proof bundle: mandatory = failing window, prompt hash, raw model I/O + latency/ids, diff, `explain.png` provenance, verdict evidence. Reference-only fields (tohost/cycles/checksum/seed) required only where the backend produces them.

**Open — answered 2026-10-26:**
- Historical bugs: I (the workforce) mine open-project bug history; baselines are my estimates. Accepted schedule risk.
- Unseen = new PicoRV32 faults authored by someone else (user or friend), quarantined from builders until run day.
- Triage: default run stops before PATCH with diagnosis + proof; `--auto` continues to patch + re-verify. `fixed unaided` counts full-auto runs only.
- Judge run: new `afterglow run --spec <their-case>` on the stranger bug goes RED→GREEN with proof; old repro stays as regression backup.
- Acceptance: I prove it on a fresh design myself (weaker independence, fastest to arrange).

**Status:** PROPOSAL — no product code until approved.
