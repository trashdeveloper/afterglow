# Per-TB kill matrix, measured live (wave-5, part 2)

Measured by `eval/kill_rate_v2.py` (live Verilator builds+runs on scratch copies of the pinned pristine trees; CPU-only, no API, no network, no commits). Deterministic sim seed 42; report carries no timestamps or timings, so re-rendering from the same `eval/kill_rate_v2.json` is byte-identical.

Method: every kept mutant (45 big-N + 5 pilot = 50) fired under every TB harness (2 parent + 2 wave-5 native = 4), 200 cells. Cell taxonomy: killed = oracle marker absent (RED); survived = marker present (GREEN); timeout = run exceeded 300s; error = no verdict possible (cross-project fault, build/fire failure) -- error != killed, timeout != killed. Faults outside a TB's build files reuse the live-measured pristine baseline verdict of that TB (identical sources, counted as equiv_reuse). kill_rate = killed / (killed + survived + timeout); errors leave the denominator (same convention as the static report). Every harness pristine baseline measured GREEN before firing (checksums in JSON).

## Per-TB rows (live)

| TB | harness stem | fired-live | killed | survived (of which reuse) | timeout | error | kill_rate |
|---|---|---|---|---|---|---|---|
| picorv32-fetch_tb.sv | picorv32-fetch-misalign | 30 | 30 | 20 (20) | 0 | 0 | 60.00% |
| picosoc-uart-div_tb.sv | picosoc-uart-div | 20 | 20 | 30 (30) | 0 | 0 | 40.00% |
| picorv32-fetch-decode_tb.sv | picorv32-fetch-decode | 30 | 30 | 20 (20) | 0 | 0 | 60.00% |
| serv-alu-lut_tb.sv | serv-alu-lut | 0 | 0 | 0 (0) | 0 | 50 | n/a |
| TOTAL | 4 harnesses x 50 mutants = 200 cells | 80 | 80 | 70 | 0 | 50 | 53.33% |

## Delta vs the published static kill-rates

Published (`eval/tb_kill_rate.md`, wave-4 board, STATIC over drawn candidates): fetch 30/2206 = 1.36%, uart 15/49 = 30.61%, total 45/2255 = 2.00%.

Measured here (LIVE over the 50 kept mutants): fetch 60.00%, uart 40.00%, decode 60.00%, serv n/a, total 53.33%.

One-sentence delta: the fetch-oracle live kill-rate over kept mutants is 60.00% vs the published 1.36% seeding kill-rate over drawn candidates -- denominators differ by construction (50 kept faults here vs 2325 drawn candidates there, of which 2210 were honest equivalents the oracle correctly passes), so the live number measures oracle strength on real faults while 1.36% measures fault-draw yield; the genuinely new live signal is the decode row, which kills 30 of the 50 kept mutants with the wave-5 decoder oracle (per-cell dispositions below).

## Classification changes vs the old matrix

NONE: all 50 kept mutants are still killed (RED) under their parent TB on the current tree -- no kept-RED classification changed.

## Full 50x4 matrix (K=killed S=survived T=timeout E=error; *=pristine-equivalence reuse)

| mutant | parent TB | fetch | uart | decode | serv |
|---|---|---|---|---|---|
| mut-bigN-01 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-02 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-03 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-04 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-05 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-06 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-07 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-08 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-09 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-10 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-11 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-12 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-13 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-14 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-15 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-16 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-17 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-18 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-19 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-20 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-21 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-22 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-23 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-24 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-25 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-26 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-27 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-28 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-29 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-30 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-31 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-32 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-33 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-34 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-35 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-36 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-37 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-38 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-39 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-40 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-41 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-bigN-42 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-43 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-44 | picorv32-fetch_tb.sv | K | S* | K | E |
| mut-bigN-45 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-pilot-01 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-pilot-02 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-pilot-03 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-pilot-04 | picosoc-uart-div_tb.sv | S* | K | S* | E |
| mut-pilot-05 | picosoc-uart-div_tb.sv | S* | K | S* | E |

## Reproduce (exact command, CPU-only, no API)

```
python3 eval/kill_rate_v2.py --seed 42
```

Writes `eval/kill_rate_v2.json` (per-cell proof incl. checksums, cycles, runtimes) and this report. Rerun is verdict-deterministic (fixed seed, pinned trees, pinned TBs); only `runtime_s`/`wall_s` in the JSON vary.

