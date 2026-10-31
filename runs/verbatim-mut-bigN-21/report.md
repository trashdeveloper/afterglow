# Afterglow run report: verbatim-mut-bigN-21

case `mut-bigN-21` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-30T16:06:45Z` | wall end `2026-10-30T16:14:57Z` | time-to-green `491.3s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Trace window is sparse showing only TRAP asserted with tohost FAIL 0xdead on 256-word NOP stream where no trap is expected and fetch progress is expected. CYCLES=2009 indicates the core ran to trap/timeout instead of clean progress. Root cause in excerpt line 1446 is decoder_trigger <= mem_do_rinst || mem_done, which retriggers the decoder on mem_done and can mis-decode a NOP as illegal, raising the observed trap.
```

expected re-sim signal: tohost PASS (not FAIL 0xdead), no TRAP asserted, fetch progress completes through 256 NOPs

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1443,7 +1443,7 @@
 			timer <= timer - 1;
 		end
 
-		decoder_trigger <= mem_do_rinst || mem_done;
+		decoder_trigger <= mem_do_rinst && mem_done;
 		decoder_trigger_q <= decoder_trigger;
 		decoder_pseudo_trigger <= 0;
 		decoder_pseudo_trigger_q <= decoder_pseudo_trigger;

```

## Provenance (judge checks)

- prompt file `/home/tuncay/afterglow/agent/prompts/v2.txt` sha256 `d31e1ef6b74ddf52ee59e790ebff2f3defa9b03be82e699f45ef620cf8e27a6e`
- input_trace.txt sha256 `f61137432f7f2d8e9fe3dea452995423352208859cf6519c0fea5fb182d769c7` (9 lines, 69 tokens)
- prompt tokens `628` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `80972ms` request `chatcmpl-01a18d97-8b5f-77a6-abbf-3e62fd5bf199`
- image model `muse-image-1.0` latency `15105ms` request `afa1842c-46d5-4c45-9f56-771c4004fa50` status `ok`
- image prompt sha256 `5b0bf69101505845316f5a8b13badf79774d76fbd556f29894e81f79a7eda6e8`, trace sha `f61137432f7f2d8e9fe3dea452995423352208859cf6519c0fea5fb182d769c7` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

