# Afterglow run report: vis-on-rdata-latch-42

case `rdata-latch` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-28T00:45:10Z` | wall end `2026-10-28T00:46:48Z` | time-to-green `97.5s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Fetch fails with `TRAP: cpu trap asserted` and `FAIL 0xdead` instead of clean NOP progress. `mem_rdata_latched_noshuffle` at picorv32.v:384 selects `mem_rdata_q` when `mem_xfer` is true and live `mem_rdata` when idle, which is inverted. This feeds stale/previous-cycle data into `mem_rdata_latched` (picorv32.v:386-388) causing illegal decode and trap.
```

expected re-sim signal: tohost PASS with no `TRAP: cpu trap asserted`, checksum != c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659

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
- input_trace.txt sha256 `5724608dd3385155da50d1314cfea2c525a7f053e8258e80b8629b87b795a342` (9 lines, 69 tokens)
- prompt tokens `1011` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `25175ms` request `chatcmpl-01a17ff8-370e-74b6-a90c-3cddc4c7753a`
- image model `muse-image-1.0` latency `33931ms` request `748ccaf6-450b-4ff2-8cdc-9bbae410da1e` status `ok`
- image prompt sha256 `c064040464997eda2f39955ad3056816c756363cad2357331dd420bbeef9699c`, trace sha `5724608dd3385155da50d1314cfea2c525a7f053e8258e80b8629b87b795a342` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

