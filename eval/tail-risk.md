# Tail-risk: wall-clock per case (measured, not estimated)

Source: `runs/*/meta.json` wall_start/end_utc across every bundle in the
repo (board + stranger + mutant + fresh-design + reruns). Method: the
one-liner in the Task-4 commit message. Attempt counts from meta
`attempts`; flake notes from bundle logs. No API was called to write this.

## Board (8 cases)

| case | runs | min | median | max | attempts range |
|---|---|---|---|---|---|
| picosoc-uart-div | 4 | 49.0s | 84.0s | 92.6s | 1 |
| picorv32-fetch-misalign | 8 | 33.1s | 50.0s | 148s (early RED live-20261026T180139Z; v5-01 RED was 47s) | 1 (greens) |
| uart-tx-div | 1 | 71.0s | 71.0s | 71.0s | 1 |
| uart-start-half | 2 | 52.0s | 69.0s | 69.0s | 1 |
| fetch-highword | 2 | 54.0s | 170.0s (early RED) | 170.0s | 1-3 |
| rdata-latch | 4 | 62.0s | 83.0s | 93.0s | 1 |
| trap-stuck | 1 | 75.0s | 75.0s | 75.0s | 1 |
| pc-incr | 1 | 171.0s | 171.0s | 171.0s | 2 |

Worst board green: pc-incr, 171s, 2 attempts. Every board case greens in
<=2 attempts; 7/8 in 1 attempt on the recorded green run.

## Mutant campaign (45 + 5 pilot)

Typical fix: 45-95s, 1 attempt. Tail cases (the honest part):

| run | wall | cause |
|---|---|---|
| mutbigN-v4-12 | 1345s (22min) | flake storm (3 transport failures) then green |
| mutbigN-v4b-06 | 1142s | flake storm, kept RED, rerun green in 51s |
| mutbigN-v4-06 | 1013s | 3 attempts incl. transport failures |
| mutbigN-v4-02 | 572s | 1 transport failure mid-run |
| mutbigN-v3-21 (orig) | 601s | genuine 3-attempt RED (kept visible) |
| mutbigN-v3-30 | 454s | 1 transport failure |
| mut-pilot-05 v2 | 383s | 1 attempt + flake, rerun 354s |

## What this means (and does not)

- Median fix is about a minute; the loop's cost ledger prices that at
  ~$0.065. The mean is worse than the median because API transport
  flakes (empty-content responses) stall runs, never because the loop
  invents work: stalled runs go RED and loud, then green on rerun.
- Worst observed wall: 1345s. Worst observed outcome that is NOT a
  flake: mutbigN-v3-21's 601s genuine RED, fixed only after the
  wave-1/2/3 machinery (separate commits).
- p90-style claim we will defend: across 100+ green runs, 9 in 10
  finish under 200s wall and under $0.15. The 10th is a flake storm or
  a genuine hard fault; both are logged, never averaged away.
