# Afterglow run report: mutbigN-v4-30

case `mut-bigN-30` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T15:53:14Z` | wall end `2026-10-27T16:00:48Z` | time-to-green `453.3s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
With COMPRESSED_ISA enabled (picosoc/picosoc.v:139) the 256-word NOP fetch test traps with `TRAP: cpu trap asserted` / `FAIL 0xdead` instead of progressing. At picorv32.v:388 `mem_rdata_latched` for `mem_la_firstword` selects `mem_rdata_latched_noshuffle[31:16]` (0x0000 illegal) instead of `[15:0]` (0x0013 NOP low-half) per picorv32.v:386-387 shuffle context. This mis-shuffle injects illegal 0x0000 on aligned fetches and causes the trap.
```

expected re-sim signal: tohost: PASS (no TRAP: cpu trap asserted, fetch progress OK)

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
- input_trace.txt sha256 `135985a57e64dfcf170f3640e392fa1942f787e99fe1cb5de8a18144b59c9239` (9 lines, 69 tokens)
- prompt tokens `1011` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `36110ms` request `chatcmpl-01a17e17-2243-7357-b257-6ddb14953053`
- image model `muse-image-1.0` latency `39888ms` request `cd7c2f19-2931-424e-ad31-1edfef8d26cd` status `ok`
- image prompt sha256 `f9f4d1a22177ed5f9d1d188e30cd74ed3e5850439fcb2f419adb28d6c36441f6`, trace sha `135985a57e64dfcf170f3640e392fa1942f787e99fe1cb5de8a18144b59c9239` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

