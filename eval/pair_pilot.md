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

## v7 05 with trailing-context grounding (2026-10-28, `mutpair-v7-05`)

Grounding strengthened (`end EVERY hunk with >=2 trailing context lines`).
Result: RED, but the failure moved again — attempt-1/3 hunk now HAS
trailing context and APPLIES clean (10-line hunk, `do_waitirq <= 1` -> `0`
at 1450, checksum 563b..626f -> 563b..f25d: real behavior change), yet
only ONE fault fixed per attempt; 1408 (`wdata`) untouched -> still RED.
v6-att3 had BOTH hunks but contextless (rejected); v7 has ONE hunk with
context (applied, insufficient). Attempt-2 output truncated mid-JSON
(unparseable, correctly rejected). Two EMPTY flakes burned calls 1-2.

Conclusion: the model can produce each half of the fix but never both in
one diff, and the loop RESETs behavior-changing-but-still-RED progress.
Compositional repair (stack verified partial diffs, continue from the
improved tree) is now evidence-backed, not speculation. 05 stays RED and
visible; no more live spend on it until the stacking wave lands.

## v8/v9 05 goes GREEN via stacking + honesty (2026-10-28)

v8 (`mutpair-v8-05`): 9-EMPTY flake storm, 0 diagnostics, correct triage.
v8b (`mutpair-v8b-05`): stacking fired live (depth 1, then depth 2 after
an unparseable attempt-2 reset) but stayed RED — and forensics proved
the v8b stack record DIVERGED from its tree (reset wiped attempt-1's
fix while the record kept appending) AND attempt-1's correct both-line
fix may have met a stale build (manual fully-fixed tree PASSES golden
5c282382 while the loop's resim said FAIL on identical content).
Built: resim honesty (per-attempt stash, build log, stale-build
suspect + ONE clean-rebuild retry as arbiter, stack truncation on
reset) + widened applied-clean stacking trigger (`9a3a796`, retrospective
APPROVE, suite 403 OK, CI ALL PASS).
v9 (`mutpair-v9-05`): attempt-1 both-line fix -> null resim ->
stale-build suspect FIRED -> clean rebuild -> still null (suspect
falsified as stale, confirmed as genuine partial) -> STACKED (depth 1)
-> attempt-2 from improved tree, no reset -> PASS 5c282382 ->
stack replay VERIFIED on fresh tree (depth 2), model-verbatim 6 lines,
0 touches, fixed_unaided=True, 585.9s.

Pilot: 5/5. The last RED converted on the machinery its failure built:
stacking accumulated the two halves, honesty arbitrated the ghost.
See `runs/mutpair-v9-05/`.
See `runs/mutpair-v7-05/`.
