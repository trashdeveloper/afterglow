# Afterglow run report: stranger2-auto1

case `stranger2` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T18:46:15Z` | wall end `2026-10-27T18:47:31Z` | time-to-green `75.1s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=f4c01ef9454981bd93e52388b13a48719e5eea9443874bce9897c0eb347af824 cycles=13 seed=42 pass=False
- AFTER: tohost=PASS checksum=f143d378eb0f6e75985e23015eb95484fffe99510f7d1415362b98ff6ccf7471 cycles=13 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
SLTIU with U1REG=5000 and imm=-1 wrote datao=00000000 instead of expected datao=00000001 (x3 must be 1). Unsigned compare U1REG < U2REGX used zero-extended UIMM=4095, so 5000<4095 is false. Root cause is line 531 where U2REGX selects UIMM when XMCC is set; it must select sign-extended SIMM (0xFFFFFFFF) so 5000<4294967295 is true unsigned.
```

expected re-sim signal: tohost: PASS 0x1 with datao=00000001 (x3=1, 5000 < 0xFFFFFFFF unsigned true)

## Patch (2 lines, guard-checked)

```diff
diff --git a/rtl/darkriscv.v b/rtl/darkriscv.v
--- a/rtl/darkriscv.v
+++ b/rtl/darkriscv.v
@@ -530,3 +530,3 @@
     wire signed [31:0] S2REGX = XMCC ? SIMM : S2REG;
-    wire        [31:0] U2REGX = XMCC ? UIMM : U2REG;
+    wire        [31:0] U2REGX = XMCC ? SIMM : U2REG;
 

```

## Provenance (judge checks)

- prompt file `/home/tuncay/afterglow/agent/prompts/v2.txt` sha256 `d31e1ef6b74ddf52ee59e790ebff2f3defa9b03be82e699f45ef620cf8e27a6e`
- input_trace.txt sha256 `6f8fca95d603df712f458b814a1911a52ebb33ac6c264e73997d8e542650df4d` (10 lines, 78 tokens)
- prompt tokens `658` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `45031ms` request `chatcmpl-01a17eaf-edea-74f2-abbe-45f4f620714d`
- image model `muse-image-1.0` latency `29234ms` request `206777cb-0a0e-4e97-b70f-05868f0b4214` status `ok`
- image prompt sha256 `5f12612fa61f12731a9c223afe43d385c9c33526ef2eea92fdb90ec98488c029`, trace sha `6f8fca95d603df712f458b814a1911a52ebb33ac6c264e73997d8e542650df4d` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

