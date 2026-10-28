# Afterglow run report: vis-off-rdata-latch-42

case `rdata-latch` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-28T00:44:00Z` | wall end `2026-10-28T00:45:10Z` | time-to-green `70.1s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Trace ends with `TRAP: cpu trap asserted` and `tohost: FAIL 0xdead` (fingerprint sha256:d52ca6bb4e44) after 2009 cycles on NOP fetch. `mem_rdata_latched_noshuffle` returned stale `mem_rdata_q` on the `mem_xfer` cycle instead of live `mem_rdata`, causing illegal-decode trap. Root cause is picorv32.v:384 where `(mem_xfer || LATCHED_MEM_RDATA) ? mem_rdata_q : mem_rdata` inverts latch/hold mux; live `mem_rdata` is valid at transfer per picosoc/picosoc.v:130.
```

expected re-sim signal: tohost PASS (was FAIL 0xdead), no TRAP: cpu trap asserted

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -384,1 +384,1 @@
-	assign mem_rdata_latched_noshuffle = (mem_xfer || LATCHED_MEM_RDATA) ? mem_rdata_q : mem_rdata;
+	assign mem_rdata_latched_noshuffle = (mem_xfer || LATCHED_MEM_RDATA) ? mem_rdata : mem_rdata_q;

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `3193d666f3861eea3f5164c1e9223a2fd72804ba2b9d6e76e24c069f39832dbb` (9 lines, 69 tokens)
- prompt tokens `1011` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `32780ms` request `chatcmpl-01a17ff7-431b-741d-8c5d-b651058018d5`
- image model `muse-image-1.0` latency `36594ms` request `d0802bbd-0054-4769-a827-3277ebae4ca2` status `ok`
- image prompt sha256 `f471fa43aad3daf1b9222ddff7628502febb066656157c78000ed2313a256f3b`, trace sha `3193d666f3861eea3f5164c1e9223a2fd72804ba2b9d6e76e24c069f39832dbb` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

