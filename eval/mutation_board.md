# Mutation board -- PILOT (aiming, not firing)

Pilot of the big-N fix-rate campaign (Depth centerpiece): seeded
single-point mutants of the Verilog tree, each verified RED under the
existing sim backend WITHOUT the agent. Live firing (agent green +
cost) is a later task; this board tracks the template.

- Generator: `eval/mutate.py` (seeded; trees go to `/tmp`/scratch only)
- Specs: `eval/mutant_pilot/mut-pilot-0{1..5}.yaml`
- Manifest (seeds, skip list, discards, RED evidence):
  `eval/mutant_pilot/manifest.json`
- Scorer stub: `eval/mutation_score.py` (static artifacts only)

| mutant | op | file:line | red evidence (oracle) | green unaided | cost |
|---|---|---|---|---|---|
| mut-pilot-01 | num_bump (-1) | simpleuart.v:78 | `tohost: FAIL 0xdead`, TRAP uart timeout, CYCLES=22023 | TBD (no live run) | TBD |
| mut-pilot-02 | num_bump (+1) | simpleuart.v:68 | `tohost: FAIL 0xdead`, TRAP data mismatch got 0xff want 0xa5, CYCLES=2024 | TBD | TBD |
| mut-pilot-03 | num_bump (-1) | simpleuart.v:59 | `tohost: FAIL 0xdead`, TRAP uart timeout, CYCLES=22023 | TBD | TBD |
| mut-pilot-04 | num_bump (+1) | simpleuart.v:42 | `tohost: FAIL 0xdead`, TRAP data mismatch got 0x25 want 0xa5, CYCLES=2652 | TBD | TBD |
| mut-pilot-05 | num_bump (+1) | simpleuart.v:120 | `tohost: FAIL 0xdead`, TRAP data mismatch got 0xff want 0xa5, CYCLES=2024 | TBD | TBD |

Aggregate (from `eval/mutation_score.py`):
`mutants=5 red_confirmed=5 green_fixed_unaided=0 cost_usd=TBD (no live runs)`

Pilot yield note: 20 candidates drawn (seed 42, same TB `picosoc-uart-div`),
5 sim-RED kept; 7 equivalent (still PASS), 4 build-breakers, 4 untried.
All 5 RED are `num_bump`; `cmp_swap` on this TB produced expression
statements the compiler rejects -- the full campaign needs a
statement-aware swap operator (recorded, not fixed here).

Projected per-mutant live-run cost (once `cost.json` exists): one
diag+patch loop ≈ one text call at the `test_cost.py` fixture usage
(prompt 1457 / completion 5940) + one successful image call =
1457/1000×$0.002 + 5940/1000×$0.008 + $0.03 = **$0.080434 per mutant**
at current `afterglow_core/cost.py` PRICING (ESTIMATES ONLY per that
file; recompute from `cost.json` at fire time).
