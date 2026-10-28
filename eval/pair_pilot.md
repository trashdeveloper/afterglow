# Pair pilot (multi-fault): mut-pair-01..05, seed 43, prompt v3

Honest board: **1/5 GREEN** (02, model-verbatim attempt 2). All five bundles
in `runs/mutpair-v5-0X/`. Re-fire of 04 (flake-drowned) pending as
`mutpair-v5-04b` (seed 44).

- 01: genuine miss. Truth = recv_state width (:39) + send_pattern ~1 (:114).
  Attempt 1 fixed fault A verbatim (checksum e825→9e1b, cycles 22023→2024:
  real behavioral effect) but never touched fault B; attempt 2 drowned in
  9 straight EMPTY-content flakes, all refused. See `runs/mutpair-v5-01/`.
- 02: GREEN attempt 2, model-verbatim, 4 lines.
- 03: genuine miss (wrong-direction + no-effect attempts, hunk failures).
- 04: TRIAGE, not RED-by-merit: 6 straight EMPTY responses burned the
  attempt-2 budget; patch correctly withheld (exit 2).
- 05: genuine miss (repeated hunk failures at 1402/1406, no-effect apply).

## Finding (pair-01 autopsy): no partial-progress mechanism

The loop RESETs to the RED baseline after every non-green attempt. That is
correct for single-fault honesty, but for multi-fault it discards real
progress: attempt 1 fixed 1 of 2 faults (verified behavior change), and
attempt 2 started over from the 2-fault state instead of continuing from
the improved tree. The machinery has no "behavior changed but still RED =
keep going from here" state. Compositional repair (stacked, each-verified
diffs with their own provenance) is the designed next wave — NOT bolted on
here, because it changes the clean-baseline honesty story and needs its
own proof rules first.

## v6 re-fire on per-fault-windows path (2026-10-28, fixed `build_excerpt_text`)

Same 4 missed pairs, same seeds, new run-ids `mutpair-v6-*`, fixed code:
- 01: GREEN attempt 2, canonical-bootstrap-gated (attempt-1 real behavior
  change 22023->2024 cycles, attempt-2 verbatim 4 lines, PASS 5c8f58d0).
- 03: GREEN attempt 2, model-verbatim, PASS 5c282382. Both windows present
  (`fault 1 ...679`, `fault 2 ...877` + pinned-lines grounding in prompt).
- 04: GREEN attempt 1, model-verbatim, PASS 5c282382.
- 05: RED (attempt 1 no-effect, attempts 2-3 hunk failures at 1446 /
  1405+1447 — new: model now targets the 1450 region, context still off).

Pilot: 4/5 (02 v5-verbatim + 01/03/04 v6). Only 05 remains, and its failure
mode MOVED from invisible-fault (a) to wrong-context (c) — the exact
progress the fix predicted. See `runs/mutpair-v6-*/`.
