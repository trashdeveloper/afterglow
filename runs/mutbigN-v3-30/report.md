# Afterglow run report: mutbigN-v3-30

case `mut-bigN-30` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T11:52:30Z` | wall end `2026-10-27T11:55:13Z` | time-to-green `163.4s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Fetch test with 256 NOPs asserts TRAP and reports tohost FAIL 0xdead instead of progressing. mem_rdata_latched in picorv32.v:388 selects mem_rdata_latched_noshuffle[31:16] (0x0000) for mem_la_firstword instead of [15:0] (0x0013), decoding as illegal compressed and trapping. Root cause is the wrong half-word select on the firstword path; fixing to [15:0] restores correct fetch alignment.
```

expected re-sim signal: tohost PASS (not FAIL 0xdead), no TRAP: cpu trap asserted, fetch progress

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -381,7 +381,7 @@
 			(COMPRESSED_ISA && mem_xfer && (!last_mem_valid ? mem_la_firstword : mem_la_firstword_reg) && !mem_la_secondword && &mem_rdata_latched[1:0]));
 	assign mem_la_addr = (mem_do_prefetch || mem_do_rinst) ? {next_pc[31:2] + mem_la_firstword_xfer, 2'b00} : {reg_op1[31:2], 2'b00};
 
-	assign mem_rdata_latched_noshuffle = (mem_xfer && LATCHED_MEM_RDATA) ? mem_rdata : mem_rdata_q;
+	assign mem_rdata_latched_noshuffle = (mem_xfer || LATCHED_MEM_RDATA) ? mem_rdata : mem_rdata_q;
 
 	assign mem_rdata_latched = COMPRESSED_ISA && mem_la_use_prefetched_high_word ? {16'bx, mem_16bit_buffer} :
 			COMPRESSED_ISA && mem_la_secondword ? {mem_rdata_latched_noshuffle[15:0], mem_16bit_buffer} :

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `e481473b05baf94d5760f5d49cef25eb6345c7a8360ae1ca6aa9ca8c7194f101` (9 lines, 69 tokens)
- prompt tokens `773` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `41530ms` request `chatcmpl-01a17d36-4e33-7389-a196-52c8a66f1783`
- image model `muse-image-1.0` latency `39878ms` request `c8c8333b-2999-4e70-844a-7122410ff192` status `ok`
- image prompt sha256 `ac190ebb71dd84cb4a4067cb2028e3632adad5bed426073f2b86c4657140e3b0`, trace sha `e481473b05baf94d5760f5d49cef25eb6345c7a8360ae1ca6aa9ca8c7194f101` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

