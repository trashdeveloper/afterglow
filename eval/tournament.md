# Tournament: 5 unseen mutants × {v2, v3} + ablation + opt pilot (live, 2026-10-27)

All runs full-auto, seed 42, model-verbatim patches, checksums MATCH 5c8f58d0.
RED = loud (exit 1, bundle kept). Costs = meta.json est_usd (ESTIMATES table).

## Round 1 — default v2

| run | status | attempts | time_to_green | est $ | notes |
|---|---|---|---|---|---|
| mut-live-01-v2 | green | 1 | 62.8s | 0.0708 | 2-line verbatim |
| mut-live-02-v2 | green | 1 | 53.7s | 0.0699 | 2-line verbatim |
| mut-live-03-v2 | green | 1 | 134.4s | 0.0584 | 1 transport flake auto-retried |
| mut-live-04-v2 | green | 1 | 53.8s | 0.0626 | diagnosis named exact fault line |
| mut-live-05-v2 | RED | 1+flakes | 382.4s to give up | 0.0791 | patch didn't take; 3 empty-response flakes exhausted retries → loud RED, no invention |

## Round 2 — harvest v3

| run | status | attempts | time_to_green | est $ | notes |
|---|---|---|---|---|---|
| mut-live-01-v3 | green | 1 | 54.1s | 0.0748 | fp 9e84de80 (matches aiming value) |
| mut-live-02-v3 | green | 1 | 46.7s | 0.0526 | cheapest run of the night |
| mut-live-03-v3 | green | 1 | 60.9s | 0.0605 | |
| mut-live-04-v3 | green | 1 | 46.2s | 0.0592 | |
| mut-live-05-v3 | green | 1 | 67.2s | 0.0851 | the one v2 couldn't take first |

## Deconfound — 05 under v2 again

| mut-live-05-v2-rerun | green | 2 | 353.2s | 0.0903 | first patch failed again, second took; 2 flakes retried |

## Ablation (locked picosoc-uart-div)

| ablate-v2-uartdiv | green | 1 | 53.4s | 0.0742 |
| ablate-v3-uartdiv | green | 1 | 49.3s | 0.0670 |

## Opt pilot (fix+opt)

| opt-pilot-uartdiv | green (fix) + opt REJECTED | 1 | 84.9s | 0.0654 | unsafe opt caught by equivalence gate, GREEN stood |

## Findings (recorded, not hyped)

1. No capability separation: both prompts fix all 5 unseen mutants. v3's edge is
   attempt-efficiency (05: v3 attempt-1 twice-implied vs v2 attempt-2 twice).
2. Cost is a tie: v2 round $0.3408 (4/5) / $0.4311 with rerun (5/5); v3 round
   $0.3322 (5/5 clean). Per-run mean ≈ $0.069 → ~1,400 fixes per $100 budget.
3. Speed: median time-to-green ≈ 54s (range 46–134s excluding flake storms).
4. Flakes don't invent: 4 empty-response storms auto-retried; 1 exhausted budget
   and went loud-RED with proof. Zero silent failures all night.
5. Total live spend for tournament + ablation + opt pilot: ≈ $0.97.
