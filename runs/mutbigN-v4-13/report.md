# Afterglow run report: mutbigN-v4-13

case `mut-bigN-13` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T15:23:18Z` | wall end `2026-10-27T15:24:03Z` | time-to-green `45.5s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Failing signal max_addr stays 0x00000000 with TRAP: no progress vs expected advancing fetch over 256-word NOP mem. mem_do_wdata remains asserted after clear, blocking instruction fetch and preventing progress. Root cause at picorv32.v:1953 where the clear on !resetn||mem_done sets mem_do_wdata <= 1 instead of 0 (excerpt picorv32.v:1949-1954).
```

expected re-sim signal: tohost PASS (not FAIL 0xdead), no TRAP: no progress, max_addr>0x00000000 with forward progress

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1950,7 +1950,7 @@
 			mem_do_prefetch <= 0;
 			mem_do_rinst <= 0;
 			mem_do_rdata <= 0;
-			mem_do_wdata <= 1;
+			mem_do_wdata <= 0;
 		end
 
 		if (set_mem_do_rinst)

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `bed75d8b59e863d5c5419cae08477f6900c5b3418da962853f8fbf0f1695a7e5` (9 lines, 69 tokens)
- prompt tokens `708` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `31289ms` request `chatcmpl-01a17df5-e673-77b5-b0b4-7fe39e514bd8`
- image model `muse-image-1.0` latency `13623ms` request `bfb3654d-cbce-4e45-bf26-ad3b2bf890ce` status `ok`
- image prompt sha256 `a1b402ed42dc8342825a0a179e9a6ddd156a9cbeedf2bfe64e9d0611f7e80ce4`, trace sha `bed75d8b59e863d5c5419cae08477f6900c5b3418da962853f8fbf0f1695a7e5` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

