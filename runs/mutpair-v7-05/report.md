# Afterglow run report: mutpair-v7-05

case `mut-pair-05` | status **RED** | mode `live` | seed `42`

wall start `2026-10-28T03:45:57Z` | wall end `2026-10-28T03:53:09Z` | time-to-green `432.0s`

retries `2` | human_touches `0` | diagnosed `True` | fixed_unaided `False` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False

## Diagnosis (verbatim from api/text_response.json)

```
Fetch TB reports TRAP: no progress max_addr=0x00000000 and tohost FAIL 0xdead instead of advancing through 256-word NOP mem. Expected decoder_trigger from mem_do_rinst && mem_done at picorv32.v:1446 to advance PC past 0x00000000, but do_waitirq stuck at 1 at picorv32.v:1450 forces permanent WFI wait and blocks fetch. Root cause is default do_waitirq <= 1 should be 0; fixing picorv32.v:1450 restores progress.
```

expected re-sim signal: tohost PASS with max_addr advancing beyond 0x00000000 through 256-word NOP mem, no TRAP: no progress

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1446,10 +1446,10 @@
 		decoder_trigger <= mem_do_rinst && mem_done;
 		decoder_trigger_q <= decoder_trigger;
 		decoder_pseudo_trigger <= 0;
 		decoder_pseudo_trigger_q <= decoder_pseudo_trigger;
-		do_waitirq <= 1;
+		do_waitirq <= 0;
 
 		trace_valid <= 0;
 
 		if (!ENABLE_TRACE)
 			trace_data <= 'bx;

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `9096e6378fce79fcba08048e3269ec11ad07c82b5bc1ff1c5ed55b772743e896` (9 lines, 69 tokens)
- prompt tokens `1148` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `67409ms` request `chatcmpl-01a180a3-b249-762a-ab1c-c5933fa80055`
- image model `muse-image-1.0` latency `15248ms` request `dce03499-9a2f-428b-aa35-8081dbc9cc75` status `ok`
- image prompt sha256 `0ef96f481e5f1774bc4831ca58eb9a11644b90811289afc8f823767ba260ef95`, trace sha `9096e6378fce79fcba08048e3269ec11ad07c82b5bc1ff1c5ed55b772743e896` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

