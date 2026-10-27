# Mutant live-fire: design, firing, regeneration, proof

Big-N board firing is enabled alongside the pilot: 45 sanctioned
`sim/targets/mut-bigN-*.yaml` stems now exist (draw seed 1234), plus the
5 pilot stems. No live run was performed here (no API keys in this
session); the firing PATH is proven by fixtures, the frozen-path
self-check below, and real Verilator sim logs for every kept mutant.

Board record: `eval/mutant_pilot/board.json` (drawn = 2325 = 45 kept +
2210 equivalent + 70 build-break + 0 other, reconciled exactly; plus 5/5
pilot re-verified RED, kept_total = 50). Regeneration: draw seed 1234 +
pool_index per kept entry replays the draw (`eval/draw_board.py`); the
specs' anchor/bug + pinned line rebuild each fault byte-identically via
the unchanged firing path (self-check: 50/50).

## Firing compatibility check (new files / new ops) -- NO loop.py change

`agent/loop.py prepare_mutant_tree` is op-agnostic by construction: it
replaces the pinned line's stripped text anchor->bug and verifies the
single-line diff. New operators (cmp_swap, cond_grow, logic_swap,
constant_flip) and the new file (picorv32.v, mutant_file pin) flow
through the identical code path as the pilot num_bump faults -- the
other track owns loop.py and nothing in the board required touching it.

- Verified: `python3 eval/draw_board.py --self-check --workdir
  /tmp/mutboard` fires all 50 board specs through the frozen
  prepare_mutant_tree and byte-compares against the campaign trees:
  **50 passed, 0 failed** (recorded in board.json `self_check`).
- Verified in CI: `tests/test_board_fire.py` (4 tests) pins the
  contract with inline cases -- a picorv32.v cond_grow fault and a
  simpleuart logic_swap fault rebuild byte-identically, a wrong pristine
  anchor fails loudly (LoopError), and a generated spec round-trips
  through the loop's canonical loader into prepare_mutant_tree.
- Stem rule unchanged: a sanctioned file
  `sim/targets/<mutant_id>.yaml` on disk is all RED dispatch needs; the
  45 new stems are picked up with no code change. `sim/sim.py` still
  REFUSES mutant stems (line-anchored inject would double-fire), same
  footgun guard as the pilot.

## Incompatibilities found (documented, NOT forked around)

1. **Pilot mh site-numbering drift (mut-pilot-03).** The manifest's
   `(line 59, num_bump, site 1, sign -1)` no longer maps to its recorded
   bug under current `eval/mutate.py`: it now yields
   `cfg_divider[ 6: 0]` (equivalent), because `_num_occurrences` counts
   the identifier index in `reg_div_we[0]`. The TRUE fault is site 2
   (bare-0 force-+1 rule), verified RED. The firing path never noticed:
   it uses spec anchor/bug + pinned line, never (op, site). The board's
   pilot re-verify therefore fires SPECS, not mh tuples
   (`eval/draw_board.py reverify_pilot_via_spec`), and pool exclusion is
   matched by spec text, not site numbers. Lesson: site numbers are an
   implementation detail; anchor/bug + line is the fault identity.
2. **cond_grow: behavior changes, zero oracle kills.** All 8 pool sites
   (ALU SLT `<`, generate-loop bounds, uart line 84) were drawn or
   hand-tried; they shift timing (e.g. CYCLES 2652 -> 2619 on uart 84)
   but no oracle emits FAIL under the two parent TBs, so the board keeps
   0 cond_grow mutants. Honest zero, recorded in board.json discards.
3. **cmp_swap shift sites still build-break.** Statement-awareness
   covers `<=` non-blocking assigns (the pilot's `x > 0;` class is
   gone -- zero occurrences in this campaign), but `<<`/`>>`/`>>>`
   operands still match bare `<`/`>` and emit uncompilable text
   (e.g. `<<` -> `<>=`, `>>>` -> `><=>`). They are honest build-break discards (22 in
   picorv32.v), never hidden. Tightening `_CMP_RE` with shift
   lookarounds would shrink the pool and invalidate drawn indices, so
   it was deliberately deferred until the NEXT pool revision.
4. **Stale-binary hazard: analyzed, guarded.** Rebuilds are
   ccache-accelerated (~1 s incremental) and content-correct (proven by
   a known-RED fault flipping PASS -> FAIL after rebuild, and by
   mutants that move CYCLES). The driver still asserts the sim binary
   is newer than every RTL source before judging; a violation aborts
   loudly (INFRA-FAIL), never misclassifies.
5. **Timeout/crash policy.** A sim that produces no oracle verdict and
   no FAIL marker (timeout, crash) lands in `other`, never in kept-RED.
   This campaign: 0 `other`. Kept-RED always carries an explicit
   `tohost: FAIL` line in its boot log.

## Pilot record (unchanged, see above for the original pilot section)

## Decision (smallest safe option)

Chosen: **(c) line-targeted fault injection at run start, served
through (a) sanctioned in-tree specs.** The in-tree-only SPEC rule is
NOT weakened: five sanctioned `sim/targets/mut-pilot-*.yaml` stems now
exist, so the stem check passes unchanged, and no arbitrary path is
ever trusted.

- Firing path (`agent/loop.py`): for a case with `mutant_file` +
  `mutant_line`, the loop copies the PINNED pristine tree to
  `runs/scratch/<run-id>`, applies anchor->bug at the PINNED LINE ONLY,
  verifies the single-line diff, then REDs with the same
  build+run+verdict schema as `resim_after`. The pristine tree is never
  written; `/tmp` trees are never read (`mutant_tree` in the spec is
  provenance only).
- `sim/sim.py` REFUSES mutant stems (`vbackend.inject` raises): its
  inject is line-anchored and would silently mis-fire (below). This is
  the footgun guard for the campaign.
- `canonical_diff` (bootstrap restore) restores ONLY the pinned line
  for mutant cases; legacy anchored behavior is kept otherwise.

## Why not the alternatives

- **(a) alone (fire via existing inject): REJECTED on fidelity.**
  `vbackend.inject` replaces EVERY anchor match. Two of five pilot
  anchors occur twice in pristine `picosoc/simpleuart.v`:
  mut-pilot-02 `"recv_state <= 0;"` (lines 68 AND 93) and mut-pilot-05
  `"send_pattern <= ~0;"` (lines 114 AND 120). Firing via inject would
  build DOUBLE faults for those two -- the wrong fault distribution
  for big-N fix-rate stats. Verified by grep + the
  `test_prepare_*` / `test_sim_refuses_mutant_stem_loudly` tests.
- **(b) (trust `mutant_tree` with hash check): REJECTED on safety +
  robustness.** It trusts prebuilt `/tmp` trees (world-writable dir,
  TOCTOU between verify and build) and depends on ephemeral state: the
  seed-42 draw procedure (`sample.py`/`extend.py`) itself lives in
  `/tmp`. Spec-driven rebuild from anchor/bug+line is deterministic,
  needs no draw replay, and never opens an external tree.
- **Adjacent fix:** mut-pilot-01's bug `"1: begin"` already exists as
  pristine line 83, so an anchored bootstrap restore would corrupt a
  healthy line next to the fix. The pinned-line `canonical_diff` above
  covers this (tested both directions).

## Files

- Added: `sim/targets/mut-pilot-0{1..5}.yaml` (sanctioned; each pins
  mutant_file/mutant_line/seed/op + provenance `mutant_tree`).
- Added: `tests/test_mutant_fire.py` (14 tests).
- Changed: `agent/loop.py` (+`shutil` import, `is_mutant_case`,
  `prepare_mutant_tree`, mutant RED branch, pinned `canonical_diff`,
  docstring), `sim/vbackend.py` (`inject` refuses mutant stems).

## Firing (for the live session with API keys)

```bash
python3 agent/loop.py --spec sim/targets/mut-pilot-01.yaml --seed 42 \
    --run-id mut-pilot-01            # run-id feeds eval/mutation_score.py
# or: ./afterglow run --spec sim/targets/mut-pilot-01.yaml --seed 42
```

`--run-id mut-pilot-0N` is the `eval/mutation_score.py` convention
(`runs/<mutant_id>/meta.json`). Scratch trees land in
`runs/scratch/<run-id>`; delete to force a clean rebuild (the loop
refuses to reuse an existing scratch dir without a fresh run-id).

## Regeneration (/tmp ephemerality)

Firing never needs `/tmp/mutpilot/*`: the fault rebuilds from the spec
alone (`prepare_mutant_tree` asserts the pristine pinned line equals
the anchor, so a wrong pristine revision fails LOUDLY instead of
building a wrong fault). The historical trees are reproducible anyway:
seed-42 draw via `/tmp/mutpilot/sample.py` + `extend.py` (ephemeral --
copies of record; if lost, the spec pins are the source of truth, and
`test_prepare_rebuild_matches_historical_mutant_tree` proves today's
rebuilds are byte-identical to `cand-02/04/05/11/15`).

## Proof (this session, no API calls, no verilator build)

- `tests/test_mutant_fire.py`: 14/14 pass (spec load + pinning,
  line-targeted apply with duplicate-text control lines untouched,
  anchor-mismatch/existing-dest refusals, force-rebuild determinism,
  byte-identical rebuild of all 5 historical trees, pristine tree
  hash-unchanged, sim.py refusal, RED/GREEN verdict plumbing).
- Oracle on RECORDED real logs: all 5
  `eval/mutant_pilot/logs/mut-pilot-0N-boot.log` judge RED
  (`tohost: FAIL`, oracle absent); `/tmp/mutpilot/base-boot.log`
  judges GREEN (`tohost: PASS`) -- pre-patch RED + pristine control
  GREEN, no cross-contamination (pristine hash check).
- Full suite: 132/132 pass across 17 test files; `scripts/ci.sh`
  exits 0 (CI GATE: ALL PASS).

## Standing rules observed

No commits, no live API calls, no keys; TDD (tests failed first on
missing specs/helpers, then greened); fixtures/mocks only for the
firing path; the in-tree-only rule and all guardrails unchanged
(`test_unknown_stem_spec_rejected_fast` still passes: foreign stems
without mutant pins are still rejected RED).
