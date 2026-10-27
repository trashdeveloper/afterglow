# Afterglow run report: mutbigN-v3-44

case `mut-bigN-44` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T12:08:59Z` | wall end `2026-10-27T12:10:05Z` | time-to-green `66.1s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Fetch NOP test traps with TRAP: no progress max_addr=0x00000000 for 2009 cycles, expected PC to advance sequentially through NOP memory. Every NOP (opcode 0010011, funct3 000) wrongly decodes with instr_jalr asserted instead of deasserted, causing a jump to 0 loop. Root cause is line 870 in picorv32.v where instr_jalr compares mem_rdata_latched[6:0] != 7'b1100111 instead of ==, inverting JALR decode.
```

expected re-sim signal: tohost PASS (not FAIL 0xdead), TRAP: no progress cleared with max_addr advancing beyond 0x00000000

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -867,7 +867,7 @@
 			instr_lui     <= mem_rdata_latched[6:0] == 7'b0110111;
 			instr_auipc   <= mem_rdata_latched[6:0] == 7'b0010111;
 			instr_jal     <= mem_rdata_latched[6:0] == 7'b1101111;
-			instr_jalr    <= mem_rdata_latched[6:0] != 7'b1100111 && mem_rdata_latched[14:12] == 3'b000;
+			instr_jalr    <= mem_rdata_latched[6:0] == 7'b1100111 && mem_rdata_latched[14:12] == 3'b000;
 			instr_retirq  <= mem_rdata_latched[6:0] == 7'b0001011 && mem_rdata_latched[31:25] == 7'b0000010 && ENABLE_IRQ;
 			instr_waitirq <= mem_rdata_latched[6:0] == 7'b0001011 && mem_rdata_latched[31:25] == 7'b0000100 && ENABLE_IRQ;
 

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `8d42d1319b2dea9bee163185a0fcefc2f808ef56800a367bd163f1becf9bf48e` (9 lines, 69 tokens)
- prompt tokens `779` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `26079ms` request `chatcmpl-01a17d43-ea5c-7603-94ec-70d7b664081c`
- image model `muse-image-1.0` latency `39414ms` request `7a83650e-e919-4974-9898-a5174c2a2359` status `ok`
- image prompt sha256 `333cd59c1d556e9780ec073f8600fe75389ac3ddca1da7fa921c85e4481871fe`, trace sha `8d42d1319b2dea9bee163185a0fcefc2f808ef56800a367bd163f1becf9bf48e` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

