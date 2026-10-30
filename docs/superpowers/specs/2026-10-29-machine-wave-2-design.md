# Design: Machine Wave 2 — 40 more debugger capabilities (items 51–90)

## Intent and why
Afterglow's loop (RED → diagnose → patch → resim → verify) is proven on
PicoRV32/SERV/darkriscv with 45/45 mutants green and 40 shipped features
(items 1–40, all SDD-bar: red-first, reviewed, committed). The human
partner asked for "another 40 things to build to make it better" and
picked **machine** (raw capability) over scoreboard-chasing, and approach
**A** (four waves of 10, same process as items 1–40). This doc specs
items 51–90: deeper discovery, deeper diagnosis, stronger repair +
autonomy, and generality + trust-2.

## Goals
- 40 new capabilities, each independently testable, sim-first, no API by
  default (live-API proof only where the item says so).
- Same SDD bar as items 1–40: TDD red-first, full suite green, CI ALL
  PASS, fresh re-review APPROVE, window commit per item.
- Reuse existing seams: `agent/discover/*`, `agent/evidence/*`,
  `agent/repair/*` (esp. `equiv.check_equivalence`), `agent/trust/*`,
  `sim/vbackend.py`, `eval/` boards. No forks of loop/guard logic.

## Non-goals
- Demo/video/submission assets (ruled out by the partner).
- Open-weights serving (ruled out earlier, stays out).
- Ship-It items 41–50 (separate list, untouched).

## Anti-goals
- 40 shallow wrappers that inflate counts without new verified behavior.
- Anything that weakens the existing gates (guard, CI, proof bundles).
- Wiring new modules into `loop.py`/`guard.py` by default — all default-off
  unless the item explicitly says otherwise.

## Constraints
- Verilator 5.052, CPU-only, picorv32 pinned `ef203c2b` (+ darkriscv/SERV
  strangers where named).
- Window commits Oct 27 – Nov 4; MODEL_API_KEY exists but wave builds
  must be $0 (fixtures/sim only) unless an item names a live budget.
- New files only per item; no edits to loop/guard/score/CI/docs except
  the plan checkbox tick at commit time.

## Approaches considered
- **A. Four waves of 10 (picked).** Same process that delivered 40/40
  with zero gate failures. Slowest, safest, every item shippable.
- **B. Spike the risky half first.** Rejected: at this depth the risk is
  half-building, not picking wrong; spikes would discard effort the
  partner wants kept as real tooling.
- **C. Judge-ranked backlog.** Rejected by the partner (would drift back
  to demo-adjacent work).

## The 40 items

### Wave E — Deeper discovery (items 51–60), `agent/discover/`
- **E1: Differential DUT (item 51).** Adopt plan's unticked A5b: run two
  CPU trees (picorv32 vs darkriscv stranger) on identical NOP stimulus,
  diff tohost/traces; test pins same-ISA-prefix behavior + divergence
  report format. Sim-only.
- **E2: Seed portfolio scheduler (52).** Run a seed set, not one seed;
  schedule by historical kill-rate; test: portfolio kills a mutant the
  single default seed misses. Fixture-level.
- **E3: Temporal assertion miner (53).** Mine SVA-like temporal rules
  from passing traces (e.g. ready→valid ordering); test: mined rule
  holds on pristine, falsified by a dishonest trace.
- **E4: Multi-hop cone slicer v2 (54).** Cone+ extended past one hop
  with depth cap + cycle guard; test: 2-hop cone contains the fault
  line, 1-hop does not.
- **E5: Reset-domain explorer (55).** Enumerate reset sequences
  (async/sync/multi-cycle); test: finds a reset-release bug fixture.
- **E6: CDC detector (56).** Flag unsynchronized cross-clock signals in
  multi-clock TBs; test: fixture pair classified sync vs CDC.
- **E7: Coverage-hole prioritizer (57).** Rank uncovered lines by
  estimated kill-potential (fan-out × toggle history); test: top-ranked
  line kills when mutated, bottom-ranked does not.
- **E8: Stimulus minimizer (58).** Shrink a failing stimulus to minimal
  length preserving RED; test: minimized stimulus still RED and shorter.
- **E9: Trace compressor (59).** Lossless trace reduction for prompt fit
  (dedup idle cycles, keep transitions); test: compressed trace
  replays to identical checksum + fits a token budget.
- **E10: Finder portfolio board (60).** Attribute each found fault to
  which finder found it first; test: board dedups and credits correctly.

### Wave F — Deeper diagnosis (items 61–70), `agent/evidence/`
- **F1: Multi-fault localizer (61).** Rank sets of lines (pairs) not
  single lines; test: pair-03 style fixture ranks the true pair top-1.
- **F2: Cone-intersection diagnosis (62).** Fault must lie in slicer-cone
  ∩ diff-window; test: intersection excludes a far-away same-signal line.
- **F3: Counterexample-guided excerpt narrowing (63).** A failed patch's
  resim trace narrows the next excerpt window; test: window shrinks and
  still contains the fault after one failed attempt (fixture).
- **F4: Evidence-overlap confidence (64).** Jaccard over failing signals
  × cone membership → 0..1 score; test: true line scores above a decoy.
- **F5: Weighted past-fix retrieval (65).** pastfix.py weighted by design
  similarity (module overlap), not plain Jaccard; test: same-design fix
  outranks cross-design fix.
- **F6: Churn-prioritized diagnosis (66).** Recent git-churn lines get a
  prior bump (blame_ctx); test: equally-scoring lines break ties by churn.
- **F7: Diagnosis memory (67).** Failed diagnoses are recorded and not
  repeated within a run; test: second attempt proposes a different line.
- **F8: Spec-cited diagnosis (68).** Diagnosis must cite a B2 spec chunk
  like patches do (speccite reuse); test: uncited diagnosis rejected.
- **F9: Waveform-shape features (69).** VCD shape features (toggle
  density, first-change cycle) map to fault classes; test: fixture
  stuck-at vs wrong-constant classify correctly.
- **F10: Diagnosis tournament (70).** Two prompt strategies compete on
  one fault; verifier (resim of both candidate lines' neighborhoods)
  picks; test: the true line wins on a fixture.

### Wave G — Stronger repair + autonomy (items 71–80), `agent/repair/`
- **G1: Dependent multi-hunk planner (71).** planhunk v2 orders hunks by
  dependency (second hunk references first's signal); test: out-of-order
  plan rejected, ordered passes.
- **G2: Repair from counterexample (72).** Failed resim's first-diff
  line feeds the next prompt's focus; test: second attempt targets the
  diff line on a fixture.
- **G3: Patch minimality enforcer (73).** ddmin on a passing patch keeps
  GREEN with fewer lines; test: minimized patch still GREEN and smaller.
- **G4: Equivalent-mutant filter (74).** C3 equiv screens the board:
  behavior-identical "mutants" are labeled NOT-A-BUG, never "fixed";
  test: identical-trace mutant flagged, never patched.
- **G5: Auto-bisect on regression (75).** regguard alarm triggers a
  bisect over recent commits to find the breaking change; test: fixture
  history pinpoints the planted commit.
- **G6: Repair portfolio race (76).** Three strategies (template,
  model, transfer-suggest) race; first GREEN wins, all logged; test:
  fixture where template wins records all three attempts.
- **G7: Fixer self-repair (77).** Repair-module failures (unparseable
  output, hunk fail) are classified by blame.py and retried with the
  matching fallback; test: fixture retries with fallback and greens.
- **G8: Story-vs-diff verifier (78).** diffstory sentences checked
  against actual diff semantics (named signal really changed that way);
  test: lying story rejected, true story passes.
- **G9: Safe auto-apply (79).** Apply + resim + auto-rollback on RED,
  no human in the loop, every step logged; test: RED auto-rollback
  restores baseline byte-identical.
- **G10: Full-auto graduation (80).** Loop promotes a case from triage
  to --auto when D2 calibration says confidence ≥ threshold over the
  trailing window; test: fixture promotes and demotes correctly.

### Wave H — Generality + trust-2 (items 81–90), `agent/trust/` + `sim/`
- **H1: Third stranger design (81).** New quarantined CPU/SoC target,
  RED-proven both directions like stranger-1/2; test: RED + GREEN both
  reproduced, quarantine holds.
- **H2: Non-CPU stranger (82).** Bus fabric / peripheral block with its
  own TB+oracle; test: a real injected fault goes RED→GREEN unaided.
- **H3: Design-agnostic case spec (83).** Any Verilog + TB + oracle
  check plugs into CaseSpec with zero loop changes; test: a toy design
  end-to-end (fixture sim).
- **H4: Cross-design transfer (84).** A fix learned on picorv32 applies
  to the same-class fault on darkriscv; test: fixture transfer greens
  without a model call.
- **H5: Prompt-strategy registry (85).** Versioned prompts with stored
  A/B outcomes; test: registry picks the historical winner for a case
  class.
- **H6: Cost-aware model router (86).** Cheap model first, escalate on
  defined failure classes only; test: fixture escalates exactly once.
- **H7: Third-party replay certification (87).** An independent script
  replays a bundle to the identical verdict from a clean checkout;
  test: certified stamp on a real bundle + tampered copy fails.
- **H8: Flake-adjusted scoring (88).** eval/score.py discounts
  proven-transport flakes with evidence links; test: fixture score
  adjusts exactly by the flake count.
- **H9: Persistent fix library (89).** transfer.py index persists to
  disk across sessions with version stamp; test: reload retrieves the
  same top fix.
- **H10: 100-mutant nightly (90).** Board scales 45 → 100 kept mutants
  with the same RED-proof + quarantine rules; test: all 100 RED-proven.

## Parts decided (partner calls)
- Machine over scoreboard; approach A over B/C.
- SDD bar unchanged (red-first, suite green, CI, re-review, commit).
- $0 build cost rule stays (fixtures/sim); live API only where an item
  names it (E2 none, F10 none, H4 none — all fixture/sim).

## Left to the builder
- Exact module/test filenames under the `agent/*/` dirs above.
- Fixture designs (must be tiny, deterministic, CPU-cheap like existing
  ones) and pinned numbers.
- Order within waves; E1 first (adopts the orphaned A5b plan box —
  tick A5b with the E1 commit).

## Builder-check decisions (controller calls, 2026-10-29)
- E9 trace budget: 2500 tokens (AGENTS.md §3 collector cap).
- E4: depth cap 2 hops + 10k-cycle guard, both pinned in tests.
- All 40 are $0: every model leg is fixture-simulated; no live budget.
- H10's extra ~55 mutants come from the existing miner
  (`eval/mutate.py` lineage) with new seeds + the same RED-proof and
  quarantine rules as the current 45.
- G9 respects D10: auto-apply needs the sign-off token like everything
  else; fixture-copy proof first, real tree only with token present.
- G5 is a standalone reader reusing `agent/discover/bisect.py`; no
  regguard wiring.
- AWAITING PARTNER: which items (if any) may touch sacred files
  (loop/guard/score/CI) — H8, G10 promotion wiring; H1/H2 stranger
  source and authorship.
- RESOLVED (partner, 2026-10-29): sacred files — builder decides per
  item, each justifying any loop/guard/score/CI edit in its review.
- RESOLVED (partner, 2026-10-29): strangers — controller picks the
  designs and scripts the faults/TBs itself (no builder authorship).
