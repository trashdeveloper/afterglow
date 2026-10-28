# Afterglow run report: vis-on-picorv32-fetch-misalign-43

case `picorv32-fetch-misalign` | status **GREEN** | mode `live` | seed `43`

wall start `2026-10-28T00:33:40Z` | wall end `2026-10-28T00:34:39Z` | time-to-green `58.6s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=a29998cd8e81501b64b44a66e009c12b297662db5ef5369b12ef3feae608e5d5 cycles=2009 seed=43 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=43 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Failing signal is mem_la_addr whose low bits are 2'b10 (nonzero), triggering `TRAP: fetch misalign addr low bits nonzero` instead of word-aligned 2'b00. Expected fetch/data address low bits 2'b00 for 32-bit word bus. Root cause in picorv32.v:382 which forces `{...,2'b10}` in both fetch and data arms of mem_la_addr.
```

expected re-sim signal: tohost PASS 0x1 with no TRAP: fetch misalign addr low bits nonzero

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -379,7 +379,7 @@
 	assign mem_la_write = resetn && !mem_state && mem_do_wdata;
 	assign mem_la_read = resetn && ((!mem_la_use_prefetched_high_word && !mem_state && (mem_do_rinst || mem_do_prefetch || mem_do_rdata)) ||
 			(COMPRESSED_ISA && mem_xfer && (!last_mem_valid ? mem_la_firstword : mem_la_firstword_reg) && !mem_la_secondword && &mem_rdata_latched[1:0]));
-	assign mem_la_addr = (mem_do_prefetch || mem_do_rinst) ? {next_pc[31:2] + mem_la_firstword_xfer, 2'b10} : {reg_op1[31:2], 2'b10};
+	assign mem_la_addr = (mem_do_prefetch || mem_do_rinst) ? {next_pc[31:2] + mem_la_firstword_xfer, 2'b00} : {reg_op1[31:2], 2'b00};
 
 	assign mem_rdata_latched_noshuffle = (mem_xfer || LATCHED_MEM_RDATA) ? mem_rdata : mem_rdata_q;
 

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `29521f74ddd1a3b73ee2a6504bc750a8e86757ac347d904b43bf605678f4deed` (9 lines, 72 tokens)
- prompt tokens `767` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `14390ms` request `chatcmpl-01a17fed-8719-75f4-81a6-1b19dfaf76c0`
- image model `muse-image-1.0` latency `24274ms` request `e94c9f05-3b6b-4219-9852-8f783fd22ef2` status `ok`
- image prompt sha256 `7c0d0e1459490a9bb2c757ea04f7e80a29ddb74ea7189d8c2541841b534daeff`, trace sha `29521f74ddd1a3b73ee2a6504bc750a8e86757ac347d904b43bf605678f4deed` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

