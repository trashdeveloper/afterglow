# Static-cone top-1 localization (wave-3)

Generated: 2026-10-27 13:51:52Z (UTC) by `eval/localization.py` via `tests/test_localization.py`. No API, no sim, no threshold -- measured, not gamed.

Method: seed = `fingerprint.extract_signals(trace)[0]` (production seed path; `anchor-fallback` only when the trace yields no signals); cone = `backward_slice(max_depth=2)`; blame = `top1_localization` (driver-frequency rank, trace-anchor proximity tie-break). Hit = EXACT `file:line` match.

top1_accuracy: 1/53 = 1.9%

| case | truth | seed (from) | cone | drivers | top1 | hit |
|---|---|---|---|---|---|---|
| mut-bigN-01 | picorv32.v:376 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-02 | picorv32.v:376 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-03 | picorv32.v:373 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-04 | picorv32.v:568 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-05 | picorv32.v:877 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-06 | picorv32.v:582 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-07 | picorv32.v:877 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-08 | picosoc/simpleuart.v:39 | byte (trace) | 0 | 0 | abstain | miss |
| mut-bigN-09 | picorv32.v:679 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-10 | picorv32.v:1408 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-11 | picorv32.v:1450 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-12 | picosoc/simpleuart.v:114 | uart (trace) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| mut-bigN-13 | picorv32.v:1953 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-14 | picosoc/simpleuart.v:126 | uart (trace) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| mut-bigN-15 | picosoc/simpleuart.v:92 | byte (trace) | 0 | 0 | abstain | miss |
| mut-bigN-16 | picorv32.v:1498 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-17 | picorv32.v:380 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-18 | picorv32.v:877 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-19 | picorv32.v:380 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-20 | picorv32.v:587 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-21 | picorv32.v:1446 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-22 | picosoc/simpleuart.v:123 | byte (trace) | 0 | 0 | abstain | miss |
| mut-bigN-23 | picorv32.v:1584 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-24 | picorv32.v:376 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-25 | picorv32.v:1952 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-26 | picorv32.v:376 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-27 | picosoc/simpleuart.v:74 | byte (trace) | 0 | 0 | abstain | miss |
| mut-bigN-28 | picorv32.v:877 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-29 | picosoc/simpleuart.v:83 | uart (trace) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| mut-bigN-30 | picorv32.v:384 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-31 | picorv32.v:877 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-32 | picorv32.v:877 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-33 | picosoc/simpleuart.v:46 | uart (trace) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| mut-bigN-34 | picosoc/simpleuart.v:72 | uart (trace) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| mut-bigN-35 | picosoc/simpleuart.v:80 | byte (trace) | 0 | 0 | abstain | miss |
| mut-bigN-36 | picosoc/simpleuart.v:99 | byte (trace) | 0 | 0 | abstain | miss |
| mut-bigN-37 | picosoc/simpleuart.v:98 | uart (trace) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| mut-bigN-38 | picosoc/simpleuart.v:131 | uart (trace) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| mut-bigN-39 | picorv32.v:1057 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-40 | picorv32.v:892 | asserted (trace) | 0 | 0 | abstain | miss |
| mut-bigN-41 | picosoc/simpleuart.v:132 | byte (trace) | 0 | 0 | abstain | miss |
| mut-bigN-42 | picorv32.v:583 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-43 | picorv32.v:874 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-44 | picorv32.v:870 | max_addr (trace) | 0 | 0 | abstain | miss |
| mut-bigN-45 | picosoc/simpleuart.v:131 | uart (trace) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| picosoc-uart-div | picosoc/simpleuart.v:90 | cfg_divider (trace) | 12 | 1 | picosoc/simpleuart.v:57 | miss |
| picorv32-fetch-misalign | picorv32.v:382 | b00 (trace) | 15 | 0 | picorv32.v:382 | HIT |
| uart-tx-div | picosoc/simpleuart.v:130 | cfg_divider (trace) | 12 | 1 | picosoc/simpleuart.v:57 | miss |
| uart-start-half | picosoc/simpleuart.v:84 | recv_divcnt (trace) | 9 | 0 | picosoc/simpleuart.v:40 | miss |
| fetch-highword | picorv32.v:584 | prefetched (trace) | 20 | 0 | picorv32.v:365 | miss |
| rdata-latch | picorv32.v:384 | latch (trace) | 200 | 0 | picorv32.v:67 | miss |
| trap-stuck | picorv32.v:1403 | high (trace) | 1 | 0 | picorv32.v:2111 | miss |
| pc-incr | picorv32.v:1552 | freeze (trace) | 0 | 0 | abstain | miss |

## Misses (52) -- wave-4 fuel

- mut-bigN-01: empty static cone (seed 'max_addr' absent from RTL).
- mut-bigN-02: empty static cone (seed 'max_addr' absent from RTL).
- mut-bigN-03: empty static cone (seed 'max_addr' absent from RTL).
- mut-bigN-04: empty static cone (seed 'max_addr' absent from RTL).
- mut-bigN-05: empty static cone (seed 'asserted' absent from RTL).
- mut-bigN-06: empty static cone (seed 'max_addr' absent from RTL).
- mut-bigN-07: empty static cone (seed 'asserted' absent from RTL).
- mut-bigN-08: empty static cone (seed 'byte' absent from RTL).
- mut-bigN-09: empty static cone (seed 'asserted' absent from RTL).
- mut-bigN-10: empty static cone (seed 'max_addr' absent from RTL).

