# Static-cone top-1 localization (wave-4)

Generated: 2026-11-04 15:41:33Z (UTC) by `eval/localization.py` via `tests/test_localization.py`. No API, no sim, no threshold -- measured, not gamed.

Method: RTL-aware seeding -- vocab = driven net/reg names via `slicer.parse_drivers` on the case files; trace tokens NOT in RTL drop out BEFORE slicing (dropped count logged per row); candidates rank in-RTL spec-anchor signals first (mutant-line context KNOWN via the spec anchor; board cases use the same anchor text plus the trace verdict's named signals), then in-RTL trace tokens, then raw fallback; first non-empty cone wins, else abstain; cone = `backward_slice(max_depth=2)`; blame = `top1_localization` (driver-frequency rank, trace-anchor proximity tie-break). Hit = EXACT `file:line` match.

top1_accuracy: 6/53 = 11.3%

| case | truth | seed (from) | cone | drivers | top1 | hit |
|---|---|---|---|---|---|---|
| mut-bigN-01 | picorv32.v:376 | mem_done (spec-anchor) | 200 | 57 | picorv32.v:381 | miss |
| mut-bigN-02 | picorv32.v:376 | mem_done (spec-anchor) | 200 | 57 | picorv32.v:381 | miss |
| mut-bigN-03 | picorv32.v:373 | mem_xfer (spec-anchor) | 155 | 29 | picorv32.v:373 | HIT |
| mut-bigN-04 | picorv32.v:568 | mem_state (spec-anchor) | 84 | 13 | picorv32.v:380 | miss |
| mut-bigN-05 | picorv32.v:877 | is_alu_reg_imm (spec-anchor) | 159 | 21 | picorv32.v:381 | miss |
| mut-bigN-06 | picorv32.v:582 | max_addr (raw-fallback) | 0 | 0 | abstain | miss |
| mut-bigN-07 | picorv32.v:877 | is_alu_reg_imm (spec-anchor) | 159 | 21 | picorv32.v:381 | miss |
| mut-bigN-08 | picosoc/simpleuart.v:39 | recv_state (spec-anchor) | 7 | 0 | picosoc/simpleuart.v:39 | HIT |
| mut-bigN-09 | picorv32.v:679 | instr_trap (spec-anchor) | 200 | 166 | picorv32.v:650 | miss |
| mut-bigN-10 | picorv32.v:1408 | set_mem_do_wdata (spec-anchor) | 4 | 0 | picorv32.v:1200 | miss |
| mut-bigN-11 | picorv32.v:1450 | do_waitirq (spec-anchor) | 5 | 0 | picorv32.v:1219 | miss |
| mut-bigN-12 | picosoc/simpleuart.v:114 | send_pattern (spec-anchor) | 7 | 1 | picosoc/simpleuart.v:126 | miss |
| mut-bigN-13 | picorv32.v:1953 | mem_do_wdata (spec-anchor) | 12 | 0 | picorv32.v:358 | miss |
| mut-bigN-14 | picosoc/simpleuart.v:126 | send_pattern (spec-anchor) | 7 | 1 | picosoc/simpleuart.v:126 | HIT |
| mut-bigN-15 | picosoc/simpleuart.v:92 | recv_buf_valid (spec-anchor) | 5 | 0 | picosoc/simpleuart.v:43 | miss |
| mut-bigN-16 | picorv32.v:1498 | max_addr (raw-fallback) | 0 | 0 | abstain | miss |
| mut-bigN-17 | picorv32.v:380 | mem_la_read (spec-anchor) | 200 | 70 | picorv32.v:381 | miss |
| mut-bigN-18 | picorv32.v:877 | is_alu_reg_imm (spec-anchor) | 159 | 21 | picorv32.v:381 | miss |
| mut-bigN-19 | picorv32.v:380 | mem_la_read (spec-anchor) | 200 | 70 | picorv32.v:381 | miss |
| mut-bigN-20 | picorv32.v:587 | mem_state (spec-anchor) | 84 | 13 | picorv32.v:380 | miss |
| mut-bigN-21 | picorv32.v:1446 | decoder_trigger (spec-anchor) | 200 | 38 | picorv32.v:377 | miss |
| mut-bigN-22 | picosoc/simpleuart.v:123 | send_dummy (spec-anchor) | 6 | 0 | picosoc/simpleuart.v:48 | miss |
| mut-bigN-23 | picorv32.v:1584 | asserted (raw-fallback) | 0 | 0 | abstain | miss |
| mut-bigN-24 | picorv32.v:376 | mem_done (spec-anchor) | 200 | 57 | picorv32.v:381 | miss |
| mut-bigN-25 | picorv32.v:1952 | mem_do_rdata (spec-anchor) | 16 | 0 | picorv32.v:357 | miss |
| mut-bigN-26 | picorv32.v:376 | mem_done (spec-anchor) | 200 | 57 | picorv32.v:381 | miss |
| mut-bigN-27 | picosoc/simpleuart.v:74 | recv_divcnt (spec-anchor) | 9 | 0 | picosoc/simpleuart.v:40 | miss |
| mut-bigN-28 | picorv32.v:877 | is_alu_reg_imm (spec-anchor) | 159 | 21 | picorv32.v:381 | miss |
| mut-bigN-29 | picosoc/simpleuart.v:83 | uart (raw-fallback) | 1 | 0 | picosoc/simpleuart.v:20 | miss |
| mut-bigN-30 | picorv32.v:384 | mem_rdata_latched_noshuffle (spec-anchor) | 200 | 30 | picorv32.v:432 | miss |
| mut-bigN-31 | picorv32.v:877 | is_alu_reg_imm (spec-anchor) | 159 | 21 | picorv32.v:381 | miss |
| mut-bigN-32 | picorv32.v:877 | is_alu_reg_imm (spec-anchor) | 159 | 21 | picorv32.v:381 | miss |
| mut-bigN-33 | picosoc/simpleuart.v:46 | send_bitcnt (spec-anchor) | 9 | 0 | picosoc/simpleuart.v:46 | HIT |
| mut-bigN-34 | picosoc/simpleuart.v:72 | recv_buf_valid (spec-anchor) | 5 | 0 | picosoc/simpleuart.v:43 | miss |
| mut-bigN-35 | picosoc/simpleuart.v:80 | recv_state (spec-anchor) | 7 | 0 | picosoc/simpleuart.v:39 | miss |
| mut-bigN-36 | picosoc/simpleuart.v:99 | recv_state (spec-anchor) | 7 | 0 | picosoc/simpleuart.v:39 | miss |
| mut-bigN-37 | picosoc/simpleuart.v:98 | recv_pattern (spec-anchor) | 6 | 1 | picosoc/simpleuart.v:98 | HIT |
| mut-bigN-38 | picosoc/simpleuart.v:131 | send_pattern (spec-anchor) | 7 | 1 | picosoc/simpleuart.v:126 | miss |
| mut-bigN-39 | picorv32.v:1057 | instr_addi (spec-anchor) | 200 | 14 | picorv32.v:432 | miss |
| mut-bigN-40 | picorv32.v:892 | compressed_instr (spec-anchor) | 6 | 0 | picorv32.v:662 | miss |
| mut-bigN-41 | picosoc/simpleuart.v:132 | send_bitcnt (spec-anchor) | 9 | 0 | picosoc/simpleuart.v:46 | miss |
| mut-bigN-42 | picorv32.v:583 | mem_do_prefetch (spec-anchor) | 180 | 11 | picorv32.v:871 | miss |
| mut-bigN-43 | picorv32.v:874 | is_beq_bne_blt_bge_bltu_bgeu (spec-anchor) | 157 | 21 | picorv32.v:381 | miss |
| mut-bigN-44 | picorv32.v:870 | instr_jalr (spec-anchor) | 150 | 21 | picorv32.v:381 | miss |
| mut-bigN-45 | picosoc/simpleuart.v:131 | send_pattern (spec-anchor) | 7 | 1 | picosoc/simpleuart.v:126 | miss |
| picosoc-uart-div | picosoc/simpleuart.v:90 | recv_divcnt (spec-anchor) | 9 | 0 | picosoc/simpleuart.v:40 | miss |
| picorv32-fetch-misalign | picorv32.v:382 | mem_la_addr (spec-anchor) | 200 | 84 | picorv32.v:382 | HIT |
| uart-tx-div | picosoc/simpleuart.v:130 | send_divcnt (spec-anchor) | 7 | 0 | picosoc/simpleuart.v:47 | miss |
| uart-start-half | picosoc/simpleuart.v:84 | recv_divcnt (spec-anchor) | 9 | 0 | picosoc/simpleuart.v:40 | miss |
| fetch-highword | picorv32.v:584 | mem_valid (spec-anchor) | 70 | 12 | picorv32.v:372 | miss |
| rdata-latch | picorv32.v:384 | mem_rdata_latched_noshuffle (spec-anchor) | 200 | 30 | picorv32.v:432 | miss |
| trap-stuck | picorv32.v:1403 | trap (spec-anchor) | 14 | 0 | picorv32.v:91 | miss |
| pc-incr | picorv32.v:1552 | reg_next_pc (spec-anchor) | 109 | 32 | picorv32.v:1500 | miss |

## Misses (47) -- wave-4 fuel

- mut-bigN-01: blamed picorv32.v:381, truth picorv32.v:376 (seed 'mem_done' [spec-anchor], cone 200 lines, dropped 4 non-RTL tokens).
- mut-bigN-02: blamed picorv32.v:381, truth picorv32.v:376 (seed 'mem_done' [spec-anchor], cone 200 lines, dropped 4 non-RTL tokens).
- mut-bigN-04: blamed picorv32.v:380, truth picorv32.v:568 (seed 'mem_state' [spec-anchor], cone 84 lines, dropped 4 non-RTL tokens).
- mut-bigN-05: blamed picorv32.v:381, truth picorv32.v:877 (seed 'is_alu_reg_imm' [spec-anchor], cone 159 lines, dropped 3 non-RTL tokens).
- mut-bigN-06: empty static cone (seed 'max_addr' [raw-fallback] absent from RTL, dropped 4 non-RTL trace tokens).
- mut-bigN-07: blamed picorv32.v:381, truth picorv32.v:877 (seed 'is_alu_reg_imm' [spec-anchor], cone 159 lines, dropped 3 non-RTL tokens).
- mut-bigN-09: blamed picorv32.v:650, truth picorv32.v:679 (seed 'instr_trap' [spec-anchor], cone 200 lines, dropped 3 non-RTL tokens).
- mut-bigN-10: blamed picorv32.v:1200, truth picorv32.v:1408 (seed 'set_mem_do_wdata' [spec-anchor], cone 4 lines, dropped 4 non-RTL tokens).
- mut-bigN-11: blamed picorv32.v:1219, truth picorv32.v:1450 (seed 'do_waitirq' [spec-anchor], cone 5 lines, dropped 4 non-RTL tokens).
- mut-bigN-12: blamed picosoc/simpleuart.v:126, truth picosoc/simpleuart.v:114 (seed 'send_pattern' [spec-anchor], cone 7 lines, dropped 4 non-RTL tokens).

