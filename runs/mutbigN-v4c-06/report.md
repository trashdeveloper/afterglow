# Afterglow run report: mutbigN-v4c-06

case `mut-bigN-06` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T16:36:49Z` | wall end `2026-10-27T16:37:40Z` | time-to-green `51.3s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Fetch made no progress with max_addr=0x00000000 vs expected advancing PC over 256 NOP words, yielding tohost FAIL 0xdead and TRAP: no progress. Root cause in excerpt at picorv32.v:392 is reset of mem_la_firstword_reg to 0 instead of 1, so the first post-reset fetch is treated as a continuation word and dropped, stalling the core. Trace wins over empty CONE_SLICE; VERDICT FAIL 0xdead confirms the stall.
```

expected re-sim signal: tohost PASS (not FAIL 0xdead), no TRAP: no progress, max_addr progress >0x00000000 over 256 NOPs

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -579,7 +579,7 @@
 				mem_wdata <= mem_la_wdata;
 			end
 			case (mem_state)
-				1: begin
+				0: begin
 					if (mem_do_prefetch || mem_do_rinst || mem_do_rdata) begin
 						mem_valid <= !mem_la_use_prefetched_high_word;
 						mem_instr <= mem_do_prefetch || mem_do_rinst;

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `db05dc742e47e8a62debd38e2206280596eab34686779dd7a76986ad944b2f4c` (9 lines, 69 tokens)
- prompt tokens `706` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `37101ms` request `chatcmpl-01a17e39-4bd5-70c1-9794-3f9e261e38eb`
- image model `muse-image-1.0` latency `13657ms` request `02c47955-3174-4203-b395-bf8c62f1d4f7` status `ok`
- image prompt sha256 `767ebbab91c6a809d14a7cb561d56417aa8648a737bfaff950c6d8a1c9765a66`, trace sha `db05dc742e47e8a62debd38e2206280596eab34686779dd7a76986ad944b2f4c` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

