# Afterglow run report: mutbigN-v3-06

case `mut-bigN-06` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T11:13:12Z` | wall end `2026-10-27T11:14:26Z` | time-to-green `73.3s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Fetch progress max_addr is stuck at 0x00000000 vs expected advance, with `TRAP: no progress max_addr=0x00000000` and tohost `FAIL 0xdead`. Trace window has no per-cycle data and CONE_SLICE is empty, so exact linkage to excerpt is unclear. Within excerpt lines 401-427 the `case (mem_wordsize)` has no default/pre-assign, so X/3 poisons mem_rdata_word and stalls first fetch.
```

expected re-sim signal: tohost 0x1 PASS with no `TRAP: no progress` and max_addr advancing beyond 0x00000000

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
- input_trace.txt sha256 `26d56b5dbf4f2547a15d4067f78afd3f7c0faa23db84ec12929c6f7a44f6ccb2` (9 lines, 69 tokens)
- prompt tokens `703` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `44647ms` request `chatcmpl-01a17d11-2426-7335-87b7-d1ecf1c5a2a2`
- image model `muse-image-1.0` latency `27992ms` request `a32fc58a-605c-4cba-8caa-9d15f19d0f33` status `ok`
- image prompt sha256 `32a656e8b72f601ca7e388d58304a84c937b5a0957f02e9c193cb28f9212cd18`, trace sha `26d56b5dbf4f2547a15d4067f78afd3f7c0faa23db84ec12929c6f7a44f6ccb2` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

