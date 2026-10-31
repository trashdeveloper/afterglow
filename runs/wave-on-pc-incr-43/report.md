# Afterglow run report: wave-on-pc-incr-43

case `pc-incr` | status **RED** | mode `live` | seed `42`

wall start `2026-10-31T17:00:35Z` | wall end `2026-10-31T17:15:35Z` | time-to-green `899.8s`

retries `1` | human_touches `0` | diagnosed `True` | fixed_unaided `False` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=FAIL 0xdead checksum=2380078a93590da06f8e9ac7ff4e6eb90974634703b56ca2bacd5dcc122b9516 cycles=2009 seed=42 pass=False

## Diagnosis (verbatim from api/text_response.json)

```
Fetch makes no progress: max_addr stays 0x00000000 vs expected sequential fetch through 256 NOP words, ending in FAIL 0xdead and 'no progress' TRAP after 2009 cycles. Both reg_pc and reg_next_pc hold current_pc so the fetch address never advances. Root cause is lines 1526-1527 where reg_next_pc <= current_pc lacks the +4 increment, stalling PC at reset.
```

expected re-sim signal: tohost PASS (not FAIL 0xdead) with fetch progress max_addr advancing beyond 0x0 and no 'no progress' TRAP

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1526,2 +1526,2 @@
 				reg_pc <= current_pc;
-				reg_next_pc <= current_pc;
+				reg_next_pc <= current_pc + 4;

```

## Provenance (judge checks)

- prompt file `/home/tuncay/afterglow/agent/prompts/v2.txt` sha256 `d31e1ef6b74ddf52ee59e790ebff2f3defa9b03be82e699f45ef620cf8e27a6e`
- input_trace.txt sha256 `6fb539cd49c1c88b5c80cb9ec7a88d5980609812c3802546efb9ac2560d404e4` (9 lines, 69 tokens)
- prompt tokens `698` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `46848ms` request `chatcmpl-01a192e8-aea3-732c-bd25-91e3854c06dc`
- image model `muse-image-1.0` latency `20373ms` request `5892bfcf-bc9d-4bee-9b50-4b6e8b258f25` status `ok`
- image prompt sha256 `c94be186e2af531e240f24aed6e95b321bf894f4d2a169270bea4ce2dfe88f8c`, trace sha `6fb539cd49c1c88b5c80cb9ec7a88d5980609812c3802546efb9ac2560d404e4` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

