# Machine Wave 2 Implementation Plan (items 51–90)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 40 more debugger capabilities (waves E–H) to the SDD bar.

**Architecture:** Standalone default-off modules under the existing
`agent/discover|evidence|repair|trust` seams, reusing `sim/vbackend.py`,
`agent/repair/equiv.py`, and `eval/` boards. No loop/guard rewrites;
any sacred-file touch (loop/guard/score/CI) needs per-item justification
approved at review.

**Tech Stack:** Python 3.12 stdlib + repo imports only; Verilator 5.052
sim fixtures; `python3 -m unittest` runner (never pytest — not installed).

**Spec:** `docs/superpowers/specs/2026-10-29-machine-wave-2-design.md`

## Global Constraints

- New files only per task; no edits to `agent/loop.py`, `agent/guard.py`,
  `eval/score.py`, `scripts/ci.sh`, `docs/` except the plan checkbox tick.
- Sacred-file edits allowed ONLY with per-item justification approved at
  re-review; default is standalone.
- $0 builds: fixtures/simulated models only; no live API unless the task
  names a budget (none do).
- Deterministic: no wall-clock/network dependence in tests; seeds pinned.
- Verilator 5.052 CPU-only; picorv32 pinned `ef203c2b`; window commits.
- Runner is `python3 -m unittest` (discover: `python3 -m unittest discover -s tests`).
- Each task ends with: new-file tests green + FULL suite green +
  `bash scripts/ci.sh` ALL PASS + proof pasted in the done-report.
  Controller (not builder) runs re-review and commits.

## Standard gates (every task's test file MUST include these)

- `test_no_loop_wiring`: `agent/loop.py` + `agent/guard.py` contain no
  reference to the new module (unless the task explicitly allows it —
  none do by default).
- `test_tmp_only` (any task that writes files): all writes go to
  injected paths defaulting to tmp in tests; no repo writes.
- `test_no_live_api`: the new module has no `MODEL_API_KEY`/`requests`/
  `urllib` usage (AST or grep gate).

## Review Focus

- Fixture sims depending on absolute paths or the author's tree state —
  every sim fixture must build in a temp/worktree path; a test pins this
  in the task that owns sim fixtures.
- Wiring creep: any import of `loop`/`guard` outside an explicitly
  allowed item — each task's tests assert zero `loop.py`/`guard.py` hits.
- Registry/JSON/prompt files written into the repo instead of tmp —
  persistence tasks must use injected paths defaulting to tmp in tests.
- Hidden live API calls in "$0" tasks — each task's tests assert no
  `MODEL_API_KEY`/`requests`/`urllib` usage in the new module.
- Quarantine violations: stranger faults/TBs authored by the task
  builder — H1/H2/H10 authorship is the controller's, asserted by
  out-of-band commit provenance, not by the builder.

---

### Task E1: Differential DUT (item 51) — DONE

**Files:**
- Create: `agent/discover/diffdut.py`
- Test: `tests/test_discover_diffdut.py`

**Interfaces:**
- Consumes: `sim/vbackend.py` (`ensure_tree`, `build`, `sh`-style run).
- Produces: `run_pair(tree_a, tree_b, stimulus) -> {match,tohost_a,tohost_b,trace_a,trace_b,divergence}`; `divergence_report(result) -> str` (pinned format).

- [ ] **Step 1: Write the failing test** — `test_same_design_matches` (two
  pristine picorv32 trees, identical NOP stimulus → `match True`) and
  `test_tampered_tree_diverges` (one tree with a fixture fault →
  `match False` + report names the differing line). Also `test_no_loop_wiring`.
- [ ] **Step 2: Run it to verify RED** — `python3 -m unittest tests.test_discover_diffdut` → ImportError.
- [ ] **Step 3: Implement** `run_pair` + `divergence_report` in
  `agent/discover/diffdut.py` (build both trees once, run same stimulus,
  byte-compare tohost + trace).
- [ ] **Step 4: Run green + gate** — new tests pass; `python3 -m unittest discover -s tests` green; `bash scripts/ci.sh` ALL PASS.
- [x] **Step 5: Report proof** — committed with E1 (see git log).
  both tails. No commit. (Controller also ticks the orphaned A5b box.)

### Task E2: Seed portfolio scheduler (item 52)

**Files:**
- Create: `agent/discover/seedportfolio.py`
- Test: `tests/test_discover_seedportfolio.py`

**Interfaces:**
- Consumes: kill-history dict `{seed: kills}` (in-memory; no I/O).
- Produces: `schedule(seeds, history) -> [seed...]` (unseen first, then
  by kills desc); `portfolio_kill(seeds, run_fn, is_kill) -> {killed, by_seed}`.

- [ ] **Step 1: Write the failing test** — fixture mutant killed by seed
  89 but not seed 42: `schedule` puts 89 first given history; `portfolio_kill`
  reports the kill attributed to 89.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/seedportfolio.py`.
- [ ] **Step 4: Run green + gate** (new + suite + CI).
- [ ] **Step 5: Report proof.** No commit.

### Task E3: Temporal assertion miner (item 53)

**Files:**
- Create: `agent/discover/ruleminer.py`
- Test: `tests/test_discover_ruleminer.py`

**Interfaces:**
- Produces: `mine_rules(passing_traces) -> [rule]` (rule = dict
  `{antecedent, consequent, support}` for ready→valid style orderings);
  `check_rule(rule, trace) -> True|{violations}`.

- [ ] **Step 1: Write the failing test** — mined rule from 3 honest UART
  traces holds on pristine; a doctored trace (valid without ready)
  falsifies it with a named violation.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/ruleminer.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task E4: Multi-hop cone slicer v2 (item 54)

**Files:**
- Create: `agent/discover/cone2.py`
- Test: `tests/test_discover_cone2.py`

**Interfaces:**
- Consumes: `agent/evidence/cone_plus.py` one-hop slicer.
- Produces: `slice_cone(signals, depth=2, cycle_guard=10000) -> {lines}`.

- [ ] **Step 1: Write the failing test** — 2-hop cone contains the fault
  line of a 2-deep fixture; 1-hop does not; cycle guard trips on a
  combinational-loop fixture instead of hanging.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/cone2.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task E5: Reset-domain explorer (item 55)

**Files:**
- Create: `agent/discover/resetexplorer.py`
- Test: `tests/test_discover_resetexplorer.py`

**Interfaces:**
- Produces: `enumerate_resets() -> [sequence]` (async/sync/multi-cycle);
  `explore(tree, case, sequences, run_fn) -> {findings}`.

- [ ] **Step 1: Write the failing test** — fixture with a reset-release
  bug: exactly one sequence in the enumeration exposes it (RED), others pass.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/resetexplorer.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task E6: CDC detector (item 56)

**Files:**
- Create: `agent/discover/cdcdetect.py`
- Test: `tests/test_discover_cdcdetect.py`

**Interfaces:**
- Produces: `classify_crossing(driver_clock, load_clock, has_sync) ->
  "sync"|"cdc-safe"|"cdc-risk"`.

- [ ] **Step 1: Write the failing test** — fixture pair: synchronized
  crossing → `cdc-safe`; direct cross-clock → `cdc-risk`; same clock → `sync`.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/cdcdetect.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task E7: Coverage-hole prioritizer (item 57)

**Files:**
- Create: `agent/discover/holerank.py`
- Test: `tests/test_discover_holerank.py`

**Interfaces:**
- Produces: `rank(uncovered_lines, toggles, fanout) -> [line...]`
  (score = fanout × (1 + toggle history)).

- [ ] **Step 1: Write the failing test** — top-ranked fixture line kills
  when mutated (simulated via injected run_fn), bottom-ranked does not.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/holerank.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task E8: Stimulus minimizer (item 58)

**Files:**
- Create: `agent/discover/stimmin.py`
- Test: `tests/test_discover_stimmin.py`

**Interfaces:**
- Produces: `minimize(stimulus, still_red_fn) -> minimal` (ddmin-style).

- [ ] **Step 1: Write the failing test** — 20-step failing stimulus
  minimizes to ≤5 steps still RED via the fixture predicate.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/stimmin.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task E9: Trace compressor (item 59)

**Files:**
- Create: `agent/discover/tracecompress.py`
- Test: `tests/test_discover_tracecompress.py`

**Interfaces:**
- Produces: `compress(trace, budget=2500) -> {text, tokens, checksum}`.

- [ ] **Step 1: Write the failing test** — compressed trace replays to
  the identical checksum (via `agent/repair/equiv.py::checksum_log`)
  and estimated tokens ≤ 2500.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/tracecompress.py`
  (dedup idle cycles, keep transitions).
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task E10: Finder portfolio board (item 60)

**Files:**
- Create: `agent/discover/finderboard.py`
- Test: `tests/test_discover_finderboard.py`

**Interfaces:**
- Produces: `attribute(fault_id, finder, board) -> board`;
  `leaders(board) -> [(finder, count)]` (dedup by fault_id).

- [ ] **Step 1: Write the failing test** — two finders report the same
  fault: credited once to the first; leaders() ranks correctly.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/finderboard.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F1: Multi-fault localizer (item 61)

**Files:**
- Create: `agent/evidence/multiloc.py`
- Test: `tests/test_evidence_multiloc.py`

**Interfaces:**
- Produces: `rank_sets(candidates, size=2, score_fn) -> [(set, score)]`.

- [ ] **Step 1: Write the failing test** — pair-fault fixture (two
  known lines): the true pair ranks top-1 above all singletons.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/multiloc.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F2: Cone-intersection diagnosis (item 62)

**Files:**
- Create: `agent/evidence/coneinter.py`
- Test: `tests/test_evidence_coneinter.py`

**Interfaces:**
- Consumes: `agent/discover/cone2.py::slice_cone`.
- Produces: `intersect(cone_lines, window_lines) -> [line...]`.

- [ ] **Step 1: Write the failing test** — a far-away same-signal line
  is excluded while the true line survives the intersection.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/coneinter.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F3: Counterexample-guided excerpt narrowing (item 63)

**Files:**
- Create: `agent/evidence/excerptnarrow.py`
- Test: `tests/test_evidence_excerptnarrow.py`

**Interfaces:**
- Produces: `narrow(window, failed_resim_first_diff) -> smaller_window`
  (still contains the fault line).

- [ ] **Step 1: Write the failing test** — after one failed attempt with
  a pinned first-diff line, the window shrinks ≥30% and still contains
  the fault.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/excerptnarrow.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F4: Evidence-overlap confidence (item 64)

**Files:**
- Create: `agent/evidence/overlapconf.py`
- Test: `tests/test_evidence_overlapconf.py`

**Interfaces:**
- Produces: `score(failing_signals, cone_lines, candidate_line) -> 0..1`
  (Jaccard × cone membership).

- [ ] **Step 1: Write the failing test** — true line scores strictly
  above a decoy line sharing one signal.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/overlapconf.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F5: Weighted past-fix retrieval (item 65)

**Files:**
- Create: `agent/evidence/pastfixw.py`
- Test: `tests/test_evidence_pastfixw.py`

**Interfaces:**
- Consumes: `agent/evidence/pastfix.py` index format.
- Produces: `retrieve(fault, index, design_modules) -> [fix]` weighted
  by module overlap.

- [ ] **Step 1: Write the failing test** — same-design prior fix
  outranks a higher-Jaccard cross-design fix.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/pastfixw.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F6: Churn-prioritized diagnosis (item 66)

**Files:**
- Create: `agent/evidence/churnprior.py`
- Test: `tests/test_evidence_churnprior.py`

**Interfaces:**
- Consumes: `agent/evidence/blame_ctx.py` porcelain output.
- Produces: `break_ties(scored_lines, churn) -> ordered` (equal scores
  ordered by churn recency).

- [ ] **Step 1: Write the failing test** — two lines with equal scores:
  the recently-churned one ranks first; different scores keep order.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/churnprior.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F7: Diagnosis memory (item 67)

**Files:**
- Create: `agent/evidence/diagmemory.py`
- Test: `tests/test_evidence_diagmemory.py`

**Interfaces:**
- Produces: `record(attempt_log, diagnosis)` / `avoid(attempt_log) ->
  [banned_lines]`.

- [ ] **Step 1: Write the failing test** — after recording a failed
  line-1446 diagnosis, the next proposal must differ from 1446.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/diagmemory.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F8: Spec-cited diagnosis (item 68)

**Files:**
- Create: `agent/evidence/diagcite.py`
- Test: `tests/test_evidence_diagcite.py`

**Interfaces:**
- Consumes: `agent/repair/speccite.py` (`extract_citations`, `check`).
- Produces: `check_diagnosis(text, valid_ids, strict=True)` (same
  pass/reject contract as patches).

- [ ] **Step 1: Write the failing test** — cited diagnosis passes both
  modes; uncited rejected in strict; invented id rejected in both.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/diagcite.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F9: Waveform-shape features (item 69)

**Files:**
- Create: `agent/evidence/vcdshape.py`
- Test: `tests/test_evidence_vcdshape.py`

**Interfaces:**
- Produces: `features(signal_trace) -> {toggle_density, first_change}`;
  `classify(feats) -> "stuck-at"|"wrong-constant"|"unknown"`.

- [ ] **Step 1: Write the failing test** — stuck-at fixture →
  `stuck-at`; wrong-constant fixture → `wrong-constant`; flatline with
  no driver → `unknown`.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/vcdshape.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task F10: Diagnosis tournament (item 70)

**Files:**
- Create: `agent/evidence/diagtourney.py`
- Test: `tests/test_evidence_diagtourney.py`

**Interfaces:**
- Produces: `compete(fault, strategy_a, strategy_b, verifier_fn) ->
  {winner, evidence}` (verifier resims both candidate neighborhoods).

- [ ] **Step 1: Write the failing test** — fixture where strategy B's
  line verifies and A's does not: B wins with resim evidence logged.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/evidence/diagtourney.py`
  (strategies are injectable callables; no live API).
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G1: Dependent multi-hunk planner (item 71)

**Files:**
- Create: `agent/repair/planhunkdep.py`
- Test: `tests/test_repair_planhunkdep.py`

**Interfaces:**
- Consumes: `agent/repair/planhunk.py` plan format.
- Produces: `order_hunks(plan) -> ordered | raises on unorderable`
  (second hunk referencing the first's signal must come after).

- [ ] **Step 1: Write the failing test** — out-of-order dependent plan
  rejected; correctly ordered passes; independent hunks any order.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/planhunkdep.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G2: Repair from counterexample (item 72)

**Files:**
- Create: `agent/repair/cexrepair.py`
- Test: `tests/test_repair_cexrepair.py`

**Interfaces:**
- Produces: `focus_from_first_diff(first_diff) -> {file, line, signals}`
  for the next prompt's focus block.

- [ ] **Step 1: Write the failing test** — a pinned first-diff record
  yields the diff line as focus with its signals extracted.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/cexrepair.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G3: Patch minimality enforcer (item 73)

**Files:**
- Create: `agent/repair/patchmin.py`
- Test: `tests/test_repair_patchmin.py`

**Interfaces:**
- Produces: `minimize(patch, still_green_fn) -> smaller_patch`.

- [ ] **Step 1: Write the failing test** — 6-line passing fixture patch
  minimizes to ≤3 lines still GREEN via the fixture predicate.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/patchmin.py` (ddmin).
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G4: Equivalent-mutant filter (item 74)

**Files:**
- Create: `agent/repair/equivfilter.py`
- Test: `tests/test_repair_equivfilter.py`

**Interfaces:**
- Consumes: `agent/repair/equiv.py::check_equivalence`.
- Produces: `screen(mutant_tree, pristine_tree, case) ->
  "REAL-FAULT"|"NOT-A-BUG"` (identical traces → NOT-A-BUG, never patched).

- [ ] **Step 1: Write the failing test** — behavior-identical fixture
  mutant → NOT-A-BUG; behavior-changed → REAL-FAULT.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/equivfilter.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G5: Auto-bisect on regression (item 75)

**Files:**
- Create: `agent/repair/bisectreg.py`
- Test: `tests/test_repair_bisectreg.py`

**Interfaces:**
- Consumes: `agent/trust/regguard.py` registry format (read-only);
  `agent/discover/bisect.py`.
- Produces: `bisect_regression(failing_entry, history) -> culprit_commit`
  (standalone reader; no regguard/loop wiring).

- [ ] **Step 1: Write the failing test** — fixture history with a
  planted breaking commit: bisect pinpoints it within log2 bounds.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/bisectreg.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G6: Repair portfolio race (item 76)

**Files:**
- Create: `agent/repair/repairrace.py`
- Test: `tests/test_repair_repairrace.py`

**Interfaces:**
- Produces: `race(fault, strategies, verify_fn) -> {winner, attempts}`
  (strategies = injectable callables: template/model/transfer shapes;
  all simulated, no live API).

- [ ] **Step 1: Write the failing test** — fixture where the template
  strategy greens: winner is template and all three attempts are logged.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/repairrace.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G7: Fixer self-repair (item 77)

**Files:**
- Create: `agent/repair/selfrepair.py`
- Test: `tests/test_repair_selfrepair.py`

**Interfaces:**
- Consumes: `agent/trust/blame.py::classify_red` failure classes.
- Produces: `fallback_for(attribution) -> strategy`; `retry_with(failure,
  fallback_fn)`.

- [ ] **Step 1: Write the failing test** — unparseable-output failure
  maps to the re-ask fallback and the retry greens on a fixture.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/selfrepair.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G8: Story-vs-diff verifier (item 78)

**Files:**
- Create: `agent/repair/storyverify.py`
- Test: `tests/test_repair_storyverify.py`

**Interfaces:**
- Consumes: `agent/trust/diffstory.py` sentence schema.
- Produces: `verify(story, patch) -> True|{lies}` (each named signal +
  direction checked against the actual hunk).

- [ ] **Step 1: Write the failing test** — a story claiming an inversion
  the diff does not contain is rejected naming the lie; a true story passes.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/storyverify.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G9: Safe auto-apply (item 79)

**Files:**
- Create: `agent/repair/safeapply.py`
- Test: `tests/test_repair_safeapply.py`

**Interfaces:**
- Produces: `apply_logged(tree, patch, resim_fn) -> {green, rolled_back}`
  (RED → byte-identical rollback; every step appended to a log list).
  Honors D10: real-tree apply requires a sign-off token argument;
  fixture-copy proof requires none.

- [ ] **Step 1: Write the failing test** — failing resim triggers
  rollback: tree bytes identical before/after, log shows apply→resim→rollback.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/safeapply.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task G10: Full-auto graduation (item 80)

**Files:**
- Create: `agent/repair/graduate.py`
- Test: `tests/test_repair_graduate.py`

**Interfaces:**
- Consumes: `agent/trust/abstain.py` policy shape.
- Produces: `recommend(history, threshold, window=10) ->
  "promote"|"hold"|"demote"` (standalone recommender; no loop wiring —
  wiring needs a later explicit approval).

- [ ] **Step 1: Write the failing test** — 8/10 above threshold →
  promote; 2 consecutive below → demote; else hold.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/graduate.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task H1: Third stranger design (item 81)

**Files:**
- Create: `sim/targets/stranger3.yaml` (+ pinned sources, controller picks)
- Test: `tests/test_stranger3_quarantine.py` (RED both directions, quarantine holds)

**Interfaces:**
- Consumes: `sim/vbackend.py`, `afterglow_core/types.py` CaseSpec.
- Produces: a quarantined third target (controller-authored faults/TB;
  no builder authorship — asserted by out-of-band provenance).

- [ ] **Step 1: Controller authors the stranger** (design + TB + oracle
  + real fault, quarantined dir; NOT delegated).
- [ ] **Step 2: Write the failing test** — RED reproduces both
  directions on the real fault; quarantine markers hold.
- [ ] **Step 3: Fire the loop live** (controller-run, key-contained)
  and record GREEN with verbatim + checksums.
- [ ] **Step 4: Suite + CI green.**
- [ ] **Step 5: Commit** the target + bundle + test (controller commit).

### Task H2: Non-CPU stranger (item 82)

**Files:**
- Create: `sim/targets/stranger-nocpu.yaml` (+ pinned sources)
- Test: `tests/test_stranger_nocpu.py`

**Interfaces:** Same as H1, for a bus fabric/peripheral block.

- [ ] **Step 1: Controller authors the stranger** (design + TB + oracle
  + real fault, quarantined; NOT delegated).
- [ ] **Step 2: Write the failing test** — real injected fault goes RED.
- [ ] **Step 3: Fire the loop live** unaided to GREEN with receipts.
- [ ] **Step 4: Suite + CI green.**
- [ ] **Step 5: Commit** target + bundle + test.

### Task H3: Design-agnostic case spec (item 83)

**Files:**
- Create: `agent/discover/genericase.py`
- Test: `tests/test_discover_genericase.py`

**Interfaces:**
- Consumes: `afterglow_core/types.py` CaseSpec.
- Produces: `from_any(verilog_files, tb, oracle_cmd) -> CaseSpec`
  validated (files exist, oracle executable).

- [ ] **Step 1: Write the failing test** — a toy 2-file design +
  shell-oracle TB runs end-to-end (fixture sim) through the spec.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/discover/genericase.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task H4: Cross-design transfer (item 84)

**Files:**
- Create: `agent/repair/xferdesign.py`
- Test: `tests/test_repair_xferdesign.py`

**Interfaces:**
- Consumes: `agent/repair/transfer.py` index.
- Produces: `transfer_fix(picorv32_fix, darkriscv_fault) -> patch|None`
  without any model call.

- [ ] **Step 1: Write the failing test** — same-class fixture fault on
  the second design greens with zero model calls (counter asserted).
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/xferdesign.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task H5: Prompt-strategy registry (item 85)

**Files:**
- Create: `agent/prompts/registry.py`
- Test: `tests/test_prompts_registry.py`

**Interfaces:**
- Produces: `register(version, stats)` / `pick(case_class) -> version`
  (historical winner per class; ties → newest).

- [ ] **Step 1: Write the failing test** — registry with two versions'
  A/B outcomes picks the historical winner for the case class.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/prompts/registry.py` (flat JSON
  file store, injected path defaulting to tmp in tests).
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task H6: Cost-aware model router (item 86)

**Files:**
- Create: `agent/repair/modelrouter.py`
- Test: `tests/test_repair_modelrouter.py`

**Interfaces:**
- Produces: `route(failure_class) -> "cheap"|"escalate"`;
  `run_with_escalation(task_fn, cheap_fn, strong_fn)`.

- [ ] **Step 1: Write the failing test** — fixture escalates exactly
  once on the defined failure class; cheap path used otherwise
  (call counters assert no live calls at all — all fns injected).
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/modelrouter.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task H7: Third-party replay certification (item 87)

**Files:**
- Create: `agent/trust/replaycert.py`
- Test: `tests/test_trust_replaycert.py`

**Interfaces:**
- Consumes: `agent/trust/replay.py` + `agent/trust/auditmode.py` chain.
- Produces: `certify(bundle_dir, clean_checkout) -> {certified, reasons}`
  + stamp file.

- [ ] **Step 1: Write the failing test** — a real GREEN bundle certifies
  from a scratch copy; a tampered copy fails naming the broken link.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/trust/replaycert.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task H8: Flake-adjusted scoring (item 88)

**Files:**
- Create: `agent/trust/scoreadjust.py`
- Test: `tests/test_trust_scoreadjust.py`

**Interfaces:**
- Consumes: `agent/evidence/flake_fp.py` flake classes (read-only).
- Produces: `adjusted_score(raw_rows, flake_evidence) -> {score, deductions}`
  as a SEPARATE report (no `eval/score.py` edit unless the builder
  justifies it and the reviewer approves).

- [ ] **Step 1: Write the failing test** — fixture score adjusts exactly
  by the proven-flake count with evidence links attached.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/trust/scoreadjust.py`.
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task H9: Persistent fix library (item 89)

**Files:**
- Create: `agent/repair/fixlib.py`
- Test: `tests/test_repair_fixlib.py`

**Interfaces:**
- Consumes: `agent/repair/transfer.py` index format.
- Produces: `save(index, path)` / `load(path) -> index` with version
  stamp; version mismatch raises.

- [ ] **Step 1: Write the failing test** — save then reload retrieves
  the same top fix; version-mismatched file raises.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** in `agent/repair/fixlib.py` (tests use tmp
  paths only; no repo writes).
- [ ] **Step 4: Run green + gate.**
- [ ] **Step 5: Report proof.** No commit.

### Task H10: 100-mutant nightly (item 90)

**Files:**
- Create: `eval/mutant_board_100.md` + `eval/mine_100.sh` (controller-authored seeds)
- Test: `tests/test_mutant_board_100.py` (all 100 RED-proven, quarantine markers)

**Interfaces:**
- Consumes: existing miner lineage (`eval/mutate.py`) with new seeds.
- Produces: 100 kept mutants, each RED-proven, quarantined from builders.

- [ ] **Step 1: Controller mines 100 mutants** (new seeds; same
  RED-proof + quarantine rules as the 45; NOT delegated).
- [ ] **Step 2: Write the failing test** — all 100 RED-prove on sim.
- [ ] **Step 3: Run the board** (controller-run sim only, no API).
- [ ] **Step 4: Suite + CI green.**
- [ ] **Step 5: Commit** board + seeds + test (controller commit).
