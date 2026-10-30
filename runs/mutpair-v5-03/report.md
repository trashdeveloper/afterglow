# Afterglow run report: mutpair-v5-03

case `mut-pair-03` | status **RED** | mode `live` | seed `43`

wall start `2026-10-28T01:40:56Z` | wall end `2026-10-28T01:44:45Z` | time-to-green `228.8s`

retries `2` | human_touches `0` | diagnosed `True` | fixed_unaided `False` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=43 pass=False
- AFTER: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=43 pass=False

## Diagnosis (verbatim from api/text_response.json)

```
TRAP asserted on legal NOPs (ADDI) with tohost FAIL 0xdead and no fetch progress per picosoc/picosoc.v:146 cpu. instr_trap was 1 instead of 0 for recognized opcodes. Root cause at picorv32.v:679 the gate `assign instr_trap = (CATCH_ILLINSN || WITH_PCPI) || !{...}` uses outer OR so it forces trap high even when valid; it must be AND.
```

expected re-sim signal: tohost PASS, TRAP deasserted with fetch progress

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -679,1 +679,1 @@
-	assign instr_trap = (CATCH_ILLINSN || WITH_PCPI) || !{instr_lui, instr_auipc, instr_jal, instr_jalr,
+	assign instr_trap = (CATCH_ILLINSN || WITH_PCPI) && !{instr_lui, instr_auipc, instr_jal, instr_jalr,

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `5e490a8fe90a5fde1e51ed558a9b850bf4f6d01d70a6ff1749913257980e3704` (9 lines, 69 tokens)
- prompt tokens `1124` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `55217ms` request `chatcmpl-01a1802e-2308-71e0-bff8-ce1e5e0140ba`
- image model `muse-image-1.0` latency `15274ms` request `cfba4cc3-f6e9-4738-a1c1-4a5c921909fa` status `ok`
- image prompt sha256 `6108aeff68e70efbef9549d93ab34da4080cfdf98ae0bfb35a96385af81f188a`, trace sha `5e490a8fe90a5fde1e51ed558a9b850bf4f6d01d70a6ff1749913257980e3704` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

