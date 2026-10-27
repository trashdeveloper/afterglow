# Afterglow run report: mutbigN-v4-26

case `mut-bigN-26` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T15:48:41Z` | wall end `2026-10-27T15:50:18Z` | time-to-green `96.7s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Fetch TB shows TRAP: no progress max_addr=0x00000000 for 2009 cycles with tohost FAIL 0xdead, expected PC advancing through 256 NOPs. mem_done at picorv32.v:376 requires (mem_do_rinst && mem_do_rdata || mem_do_wdata) so instruction-only mem_xfer never asserts done and fetch stalls at 0. Root cause is && instead of ||; restoring OR restores mem_done for rinst/rdata/wdata.
```

expected re-sim signal: tohost PASS with progress, max_addr advancing beyond 0x00000000 and no TRAP: no progress

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -375,3 +375,3 @@
 	wire mem_busy = |{mem_do_prefetch, mem_do_rinst, mem_do_rdata, mem_do_wdata};
-	wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst && mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) &&
+	wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) &&
 			(!mem_la_firstword || (~&mem_rdata_latched[1:0] && mem_xfer));

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `8fbe95d7bfbaedcd50a018f042b25e4a2f866286921a25aa5d06fa72f0998fe7` (9 lines, 69 tokens)
- prompt tokens `795` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `48644ms` request `chatcmpl-01a17e0d-6805-73fc-84a2-db1e17b01df7`
- image model `muse-image-1.0` latency `47455ms` request `a1aab946-f9c8-438b-8f84-0252f463fa47` status `ok`
- image prompt sha256 `04e1dda88aa638f6feee66e42534c22fe99ceb205d5b3f8e94fab23377f7de63`, trace sha `8fbe95d7bfbaedcd50a018f042b25e4a2f866286921a25aa5d06fa72f0998fe7` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

