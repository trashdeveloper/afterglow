# Afterglow run report: fetch-misalign-v5-01

case `picorv32-fetch-misalign` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T23:48:35Z` | wall end `2026-10-27T23:49:22Z` | time-to-green `46.8s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=a29998cd8e81501b64b44a66e009c12b297662db5ef5369b12ef3feae608e5d5 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
TRAP: fetch misalign addr low bits nonzero with tohost FAIL 0xdead because mem_la_addr low bits are 2'b10 instead of expected 2'b00 for word-aligned fetch. Root cause at picorv32.v:382 where mem_la_addr forces {next_pc[31:2]+mem_la_firstword_xfer,2'b10} for mem_do_prefetch/mem_do_rinst and {reg_op1[31:2],2'b10} for data. Every lookahead fetch therefore stays misaligned and never makes progress.
```

expected re-sim signal: tohost PASS (no FAIL 0xdead), no TRAP: fetch misalign, mem_la_addr[1:0]==2'b00

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
- input_trace.txt sha256 `6df5b825f5a24a57ceec12a4d19e45556e529b7ab1ff545b399f2c9a2355c360` (9 lines, 72 tokens)
- prompt tokens `780` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `18402ms` request `chatcmpl-01a17fc4-4eed-74a4-826a-6cc5f32eae53`
- image model `muse-image-1.0` latency `27539ms` request `0b2dee2d-6d4a-423b-8186-e582189d9e7d` status `ok`
- image prompt sha256 `a00f919839703dae7afc546311352cafa2415bb0b3544e0813ed57f15123fe7d`, trace sha `6df5b825f5a24a57ceec12a4d19e45556e529b7ab1ff545b399f2c9a2355c360` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

