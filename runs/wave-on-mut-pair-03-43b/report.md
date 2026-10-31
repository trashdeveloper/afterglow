# Afterglow run report: wave-on-mut-pair-03-43b

case `mut-pair-03` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-31T17:18:12Z` | wall end `2026-10-31T17:31:00Z` | time-to-green `768.5s`

retries `2` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Fetch test fills memory with NOP (ADDI x0,x0,0, opcode 0010011) but cpu trap is asserted and tohost reports FAIL 0xdead instead of progress. Line 877 compares mem_rdata_latched[6:0] to 7'b0010010 so is_alu_reg_imm never asserts, leaving OP-IMM undecodable and instr_trap asserted via line 679. Root cause is wrong opcode constant; correct RV32I OP-IMM is 7'b0010011 matching is_alu_reg_reg 0110011 pattern.
```

expected re-sim signal: tohost PASS (not FAIL 0xdead), TRAP deasserted, checksum != c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659

## Patch (4 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -679,4 +679,4 @@
-	assign instr_trap = (CATCH_ILLINSN || WITH_PCPI) || !{instr_lui, instr_auipc, instr_jal, instr_jalr,
+	assign instr_trap = (CATCH_ILLINSN || WITH_PCPI) && !{instr_lui, instr_auipc, instr_jal, instr_jalr,
 			instr_beq, instr_bne, instr_blt, instr_bge, instr_bltu, instr_bgeu,
 			instr_lb, instr_lh, instr_lw, instr_lbu, instr_lhu, instr_sb, instr_sh, instr_sw,
 			instr_addi, instr_slti, instr_sltiu, instr_xori, instr_ori, instr_andi, instr_slli, instr_srli, instr_srai,
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -874,7 +874,7 @@
 			is_beq_bne_blt_bge_bltu_bgeu <= mem_rdata_latched[6:0] == 7'b1100011;
 			is_lb_lh_lw_lbu_lhu          <= mem_rdata_latched[6:0] == 7'b0000011;
 			is_sb_sh_sw                  <= mem_rdata_latched[6:0] == 7'b0100011;
-			is_alu_reg_imm               <= mem_rdata_latched[6:0] == 7'b0010010;
+			is_alu_reg_imm               <= mem_rdata_latched[6:0] == 7'b0010011;
 			is_alu_reg_reg               <= mem_rdata_latched[6:0] == 7'b0110011;
 
 			{ decoded_imm_j[31:20], decoded_imm_j[10:1], decoded_imm_j[11], decoded_imm_j[19:12], decoded_imm_j[0] } <= $signed({mem_rdata_latched[31:12], 1'b0});

```

## Provenance (judge checks)

- prompt file `/home/tuncay/afterglow/agent/prompts/v2.txt` sha256 `d31e1ef6b74ddf52ee59e790ebff2f3defa9b03be82e699f45ef620cf8e27a6e`
- input_trace.txt sha256 `d6fa908d28e8f5f9c95338369ad3a68d8cf3d8375695edeca2a35ad5eb183cee` (9 lines, 69 tokens)
- prompt tokens `1145` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `41666ms` request `chatcmpl-01a19303-4cc1-731b-a11b-751e44935f22`
- image model `muse-image-1.0` latency `30766ms` request `5cdc9299-7a01-423e-b78b-901dcff1dd27` status `ok`
- image prompt sha256 `838586899b43f8f9780bf3bd1cb0700c7574bbee58c3c0b920fc36362fcbc4c0`, trace sha `d6fa908d28e8f5f9c95338369ad3a68d8cf3d8375695edeca2a35ad5eb183cee` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

