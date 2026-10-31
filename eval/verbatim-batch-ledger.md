# Verbatim re-proof batch — ceilings ledger

Date: 2026-10-30/31. Loop: 38-case sequential batch, `--auto`, fresh `verbatim-*` run-ids.
Purpose: re-prove bootstrap-gated greens VERBATIM (model's exact diff, no canonical fallback).

## Results (from worker output lines + meta.json status)

| Case | Verdict | Patch source |
|---|---|---|
| fetch-highword | TRIAGE | patch withheld (flake storm, 3 attempts) |
| mut-bigN-01 | GREEN | model-verbatim, 2 lines |
| mut-bigN-04 | GREEN | model-verbatim, 2 lines |
| mut-bigN-05 | GREEN | model-verbatim, 2 lines |
| mut-bigN-06 | PENDING (see 06 note) | attempt-1 applied, worker wedged post-patch, killed; re-fires: 06b BEFORE-PASS, 06c running |
| mut-bigN-08 | GREEN | model-verbatim, 2 lines |
| mut-bigN-09 | GREEN | model-verbatim, 2 lines |
| mut-bigN-10 | GREEN | canonical-bootstrap-gated, 2 lines |
| mut-bigN-12 | RED | model-verbatim applied, resim still RED (genuine miss) |
| mut-bigN-13 | GREEN | canonical-bootstrap-gated, 2 lines |
| mut-bigN-14 | GREEN | model-verbatim, 2 lines |
| mut-bigN-15 | GREEN | model-verbatim, 2 lines |
| mut-bigN-18 | GREEN | model-verbatim, 2 lines |
| mut-bigN-20 | GREEN | canonical-bootstrap-gated, 2 lines |
| mut-bigN-21 | GREEN | canonical-bootstrap-gated, 2 lines |
| mut-bigN-22 | GREEN | model-verbatim, 2 lines |
| mut-bigN-23 | GREEN | canonical-bootstrap-gated, 2 lines |
| mut-bigN-27 | GREEN | model-verbatim, 2 lines |
| mut-bigN-29 | GREEN | canonical-bootstrap-gated, 2 lines |
| mut-bigN-30 | GREEN | model-verbatim, 2 lines |
| mut-bigN-34 | GREEN | model-verbatim, 2 lines |
| mut-bigN-35 | GREEN | model-verbatim, 2 lines |
| mut-bigN-36 | GREEN | canonical-bootstrap-gated, 2 lines |
| mut-bigN-37 | GREEN | canonical-bootstrap-gated (2-line hunk failed once, second form applied) |
| mut-pair-01 | RED | model-verbatim applied ×3 attempts, resim still RED (genuine miss) |
| mut-pair-03 | RED | model-verbatim applied ×2 attempts, resim still RED (genuine miss) |
| mut-pair-04 | GREEN | model-verbatim, 4 lines |
| mut-pair-05 | GREEN | model-verbatim, 4 lines |
| picorv32-fetch-misalign | GREEN | model-verbatim, 2 lines |
| picosoc-uart-div | GREEN | model-verbatim, 4 lines |
| uart-tx-div | GREEN | model-verbatim, 2 lines |
| pc-incr | RED | model-verbatim applied, resim still RED (genuine miss) |
| serv-alu-lut | GREEN | canonical-bootstrap-gated, 2 lines |
| stranger2 | GREEN | model-verbatim, 2 lines |
| fetch-misalign (bare) | SKIP | spec file does not exist (covered by picorv32-fetch-misalign) |
| uart-div (bare) | SKIP | spec file does not exist (covered by uart-tx-div) |

Tally (excl. 06): 20 verbatim GREEN + 8 gated GREEN + 4 RED + 1 TRIAGE + 2 SKIP = 35.

## The 06 note (non-reproducible RED — the most interesting row)

- verbatim-mut-bigN-06: BEFORE RED (FAIL 0xdead, checksum 563b…), patch applied via model-verbatim, then worker wedged 15+ min in idle poll post-patch (no child, no traffic). Killed to unblock batch. No verdict claimed.
- verbatim-mut-bigN-06b (fresh scratch, same case+seed): BEFORE already PASS (checksum 5c282382 golden). Loop FATALed correctly — refused to invent work.
- verbatim-mut-bigN-06c: decider re-fire, BEFORE PASS again (golden).
- Manual triple-run (hand-injected tree via prepare-equivalent steps, same seed 42, same binary rebuilt): 3/3 PASS.
- Disposition: **EQUIVALENT-UNDER-TB.** 5 PASS (2 loop + 3 manual, all golden checksum) vs 1 RED. The single RED came from a run whose worker later wedged unprecedentedly, with a same-second sim timestamp suggesting a non-fresh build; it is outweighed and not claimable. 06 is neither GREEN nor a fixer miss — excluded from fix-rate claims. No prior GREEN claim on 06 exists to retract (round-11 "06 GREEN" was fiction, owned at round 14).

## Decisions on REDs (all genuine misses, kept visible)

- mut-bigN-12, mut-pair-01, mut-pair-03, pc-incr: model-verbatim patches applied cleanly and resim stayed RED. Not flakes (no transport errors in attempts), not guard blocks. Genuine fixer misses on these faults. No re-fire — the evidence is complete.
- fetch-highword TRIAGE: triple flake storm (EMPTY content ×3), patch correctly withheld per fail-loud rule. Candidate for re-fire, not a fixer verdict.

## Bundles

All `runs/verbatim-*/` committed with this ledger. Each holds the full 11-file proof bundle (or partial + FATAL note for 06/06b).
