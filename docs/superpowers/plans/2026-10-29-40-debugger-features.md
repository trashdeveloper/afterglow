# 40-Feature Debugger Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build list items 1–40 (discovery, diagnosis, repair, trust) into Afterglow, each to SDD bar.

**Architecture:** New code lives in `agent/` (loop-adjacent helpers), `sim/` (backends), `eval/` (measurers); each feature is an independent module with red-first tests, wired into the loop only through documented seams. No changes to guard policy, proof-bundle schema, or scoring without a plan amendment.

**Tech Stack:** Python 3.12 stdlib + unittest (repo has no pytest), Verilator 5.x, existing `agent/loop.py` / `sim/vbackend.py` seams.

**Spec:** The 50-item list from chat (items 1–40 in scope; 41–50 Ship-It excluded).

## Global Constraints

- TDD: red-first test, run to confirm FAIL, implement, run to confirm PASS.
- After every task: full suite `python3 -m unittest discover -s tests` OK + `bash scripts/ci.sh` ALL PASS.
- Every task gets a fresh re-reviewer; APPROVE required before the next task starts.
- No API keys on disk (`.env` only, gitignored); no commits of `runs/scratch/`.
- Mock the API in tests (`mock.patch.object(loop, ...)`); live validation fires use `.env` key, capped per wave.
- New modules go under `agent/discover/`, `agent/evidence/`, `agent/repair/`, `agent/trust/` by wave.

## Review Focus

- Fuzzer that never finds anything must say so loudly, not fake a RED (wave A owner tests empty-corpus behavior).
- Any feature that touches verdict/green paths must prove singles behavior byte-identical (regression test per task).
- Cost: every live-firing feature logs est. spend; wave budgets in tasks.
- Pair/single paths must not regress: compositional-stack + resim-honesty tests must stay green.

---

### Wave A — FIND BUGS (agent/discover/)

**Files per task:** Create `agent/discover/<name>.py` + `tests/test_discover_<name>.py`; Modify wiring seam only as listed.

**Interfaces (shared):** `StimulusCorpus(seed) -> list[bytes]`; `run_corpus(tree, case, corpus) -> list[Verdict]`; discovery writes `eval/discovery_<name>.md` reports, never verdicts.

- [x] **Task A1: Input fuzzer (item 1)** — DONE c676866^ (d1d8aee): fuzzer.py + 8/8 tests, suite 411, CI PASS, honest-zero validation.
- [x] **Task A2: TB blind-spot hunter (item 2)** — DONE c676866: blindspot.py + 10/10 tests, pilot 5/5 killed, suite 421, CI ALL PASS.
- [x] **Task A3: Coverage-driven explorer (item 3)** — DONE: coverage.py + 14 tests, 120 uncovered lines, top-5 verified, suite 435, CI ALL PASS.
- [x] **Task A4: Cross-seed differ (item 4)** — DONE: seeddiff.py + 14 tests, deterministic validation, suite 449, CI ALL PASS.
- [x] **Task A5: Protocol-assertion checker (item 5)** — DONE: protocol.py (VCD parser, UART frame + ready/valid rules) + 19 tests, ALL CLEAR on simpleuart 0xA5 frame, suite 468, CI ALL PASS.
- [ ] **Task A5b: Differential DUT (item 5 orig)** — runs two CPU trees (picorv32 vs darkriscv stranger) on identical NOP stimulus, diffs tohost/traces; test: same ISA prefix behavior documented, divergence report format pinned. Sim-only.
- [x] **Task A6: Assertion synthesizer (item 6)** — DONE: assertion_synth.py + 12 tests, 1 prop holds-clean/falsified-by-mutant, live $0.085 (4 attempts disclosed), suite 480, CI ALL PASS.
- [ ] **Task A7: Spec-vs-RTL differ (item 7)** — checks each documented behavior in a small spec excerpt has a TB path; test: fixture spec with 3 behaviors, 1 uncovered → reported. No API ( retrieval only).
- [ ] **Task A8: Reset/X-prop hunter (item 8)** — forces X on chosen regs at reset release in sim, reports X reaching committed state; test: clean design → no leak; injected X-leak mutant → caught. Sim-only.
- [ ] **Task A9: Timing stressor (item 9)** — varies `+seed`/clock-edge sampling params across runs, flags order-dependent PASS/FAIL flips; test: deterministic case → stable; injected race → flagged. Sim-only.
- [ ] **Task A10: Regression bisect (item 10)** — given a failing stimulus, delta-minimizes to shortest trigger; test: synthetic 100-input trigger minimized to known 3-input core. No API.

### Wave B — SEE MORE (agent/evidence/)

**Interfaces:** `EvidencePack(case, tree) -> dict` consumed by prompt builders; every addition is additive + default-off unless stated.

- [ ] **Task B1: VCD-as-evidence (item 11)** — enables `--trace`, slices VCD window around first FAIL cycle, summarizes top toggling signals; test: fixture VCD slice contains the injected signal transition. Sim-only.
- [ ] **Task B2: Spec RAG (item 12)** — chunks `docs/` spec excerpts, Jaccard retrieval per fault, cites chunk ids in prompt; test: query returns the chunk containing the anchor line. No API.
- [ ] **Task B3: Git-blame context (item 13)** — for fault lines, reports last-commit hash/message touching them (repo-local git); test: fixture repo returns pinned commit. No API.
- [ ] **Task B4: Past-fix memory (item 14)** — retrieves similar past diagnoses from `runs/*/meta.json` by signal-name overlap; test: query returns the known similar run. No API.
- [ ] **Task B5: Cone-plus (item 15)** — extends wave-3 slicer across one module boundary following fan-in; test: cross-module fault's driver appears in cone. No API.
- [ ] **Task B6: Multi-signal TBs (item 16)** — TB generator emitting data+control+interrupt checks in one TB; test: generated TB kills a control-only mutant that a data-only TB misses. Sim-only.
- [ ] **Task B7: Fault interaction graph (item 17)** — for pair specs, records which fault's fix changes the other's symptom (masking order); test: fixture pair yields pinned order. Uses existing pair runs, no new API.
- [ ] **Task B8: Flake fingerprinting (item 18)** — classifies transport failures by signature (EMPTY vs timeout vs truncation), routes around known-bad patterns; test: fixture error strings classify pinned. No API.
- [ ] **Task B9: Confidence calibration (item 19)** — model self-reports fix confidence 1-5; loop budgets attempts proportionally (low confidence → more attempts, never fewer than 1); test: budget mapping table pinned. Live validation on 2 cases (~$0.20).
- [ ] **Task B10: Counterexample minimizer (item 20)** — shrinks failing trace window to minimal lines preserving the FAIL signature; test: 200-line trace with injected fault minimizes to window containing the fault line. No API.

### Wave C — FIX BETTER (agent/repair/)

**Interfaces:** repair helpers consume `(tree, case, diagnosis)` and produce diffs through the existing `try_apply_diff` + guard seam. Singles behavior must stay byte-identical (regression test each task).

- [ ] **Task C1: Repair templates (item 21)** — guarded sketch-fill for reset-missing/off-by-one/width-mismatch shapes; template applies only if guard passes; test: each template fires on its fixture, refuses on others. No API.
- [ ] **Task C2: Multi-hunk planner (item 22)** — model emits plan-then-diff; loop validates every planned hunk landed; test: 2-hunk plan with 1 landed → flagged incomplete. Live validation 1 pair case (~$0.10).
- [ ] **Task C3: Equivalence check (item 23)** — after GREEN, runs fixed vs pristine tree on random stimulus, demands identical traces; test: equivalent pair passes, behavior-changed pair flagged. Sim-only.
- [ ] **Task C4: Fix ranking (item 24)** — collects up to 3 candidate patches across attempts, resims each, ships smallest passing; test: fixture picks smallest of 3. Uses existing attempt machinery, minimal API.
- [ ] **Task C5: Interactive repair (item 25)** — `--interact` pauses after PATCH, dev accepts/edits/rejects hunks, loop continues; test: reject path restores baseline. No API for the mechanism test.
- [ ] **Task C6: Spec-grounded fix (item 26)** — patch must cite a spec chunk id (from B2); mechanical check rejects uncited patches in strict mode; test: cited passes, uncited rejected. Builds on B2.
- [ ] **Task C7: Cross-fault transfer (item 27)** — indexes fixed diffs by signal names; tries top match first on new same-signal faults; test: fixture retrieves the known prior fix. No API.
- [ ] **Task C8: Negative learning (item 28)** — records failed fix shapes per signal into `eval/fix_blacklist.md`; prompt includes blacklist; test: blacklisted shape is absent from next prompt. No API.
- [ ] **Task C9: Lint-before-resim (item 29)** — runs Verilator `--lint-only` on patched tree before full rebuild; lint fail skips resim (saves time, not counted as attempt); test: broken syntax skips resim. Sim-only.
- [ ] **Task C10: Incremental rebuild cache (item 30)** — preserves obj dir across attempts when only one file changed (vs current drop-on-suspect); suspect path still drops; test: cache hit rebuilds faster than clean (timed), suspect still forces clean. Sim-only.

### Wave D — TRUST IT (agent/trust/)

- [ ] **Task D1: Proof bundle v2 (item 31)** — `runs/<id>/REPLAY.sh` regenerating the verdict from bundle contents alone; test: fresh copy of a bundle replays to identical checksum. Sim-only.
- [ ] **Task D2: False-fix alarm (item 32)** — after GREEN, runs C3 equivalence; divergence → alarm flag in meta, verdict stays but marked `needs-review`; test: fixture divergence alarms. Sim-only.
- [ ] **Task D3: TB strength score (item 33)** — kill-rate of standard mutant sample by the case TB, written to meta; test: weak TB scores lower than strong TB on fixture. Sim-only.
- [ ] **Task D4: Fix provenance chain (item 34)** — one JSON trail fault→diagnosis→patch→green with request ids; test: v9 bundle regenerates a complete trail. No API.
- [ ] **Task D5: Cost ledger per bug (item 35)** — extends existing cost.py with per-bug rollup across attempts; test: rollup arithmetic pinned. No API.
- [ ] **Task D6: Regression guard (item 36)** — fixed bugs become permanent specs re-fired by CI; test: re-fire of v9 spec stays green. Sim-only (CI integration).
- [ ] **Task D7: Human-readable diff story (item 37)** — renders patch explanation in the design's own signal names; test: fixture renders pinned sentence. Uses existing report data, no API.
- [ ] **Task D8: Audit mode (item 38)** — `--audit` dumps every prompt/response hash chain; test: audit log verifies against bundle. No API.
- [ ] **Task D9: Blameless REDs (item 39)** — failure attribution classifier (tool/TB/model/infra) in meta; test: fixture failures classify pinned. No API.
- [ ] **Task D10: Sign-off gate (item 40)** — `--signoff` requires an approval token file before GREEN is finalized; test: missing token blocks, present token passes. No API.

## Execution

Waves in order A→D (B2 before C6; C3 before D2). Within a wave, tasks sequential, each: implement → tests → suite+CI → fresh re-review APPROVE → commit → next. Live validation spend tracked in cost ledger; wave budget ~$3 each, stop-and-report on overrun.
