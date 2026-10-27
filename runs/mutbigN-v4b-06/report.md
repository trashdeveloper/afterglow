# Afterglow run report: mutbigN-v4b-06

case `mut-bigN-06` | status **RED** | mode `live` | seed `42`

wall start `2026-10-27T16:14:46Z` | wall end `2026-10-27T16:33:48Z` | time-to-green `1141.7s`

retries `1` | human_touches `0` | diagnosed `True` | fixed_unaided `False` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False

## Diagnosis (verbatim from api/text_response.json)

```
Fetch makes no progress: max_addr=0x00000000 vs expected advance over 256 NOP words, with TRAP no progress and FAIL 0xdead after 2009 cycles. The excerpt resets mem_la_firstword_reg to 0 at line 392 instead of 1, so the first lookahead transaction after reset is marked not-first-word. With last_mem_valid handshake at line 397 this stalls mem_valid/mem_ready completion and locks fetch at PC 0.
```

expected re-sim signal: tohost PASS (not FAIL 0xdead), no TRAP no progress, max_addr advances beyond 0x00000000 over 256-word NOP sweep, checksum != 563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -390,7 +390,7 @@
 	always @(posedge clk) begin
 		if (!resetn) begin
-			mem_la_firstword_reg <= 0;
+			mem_la_firstword_reg <= 1;
 			last_mem_valid <= 0;
 		end else begin
 			if (!last_mem_valid)

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `4c9e2ecf8110620261f2169336700460f61f6e554d14b9cbe77b22c2f25b601e` (9 lines, 69 tokens)
- prompt tokens `817` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `54640ms` request `chatcmpl-01a17e29-4c70-7179-adb6-f2b82e8cd38c`
- image model `muse-image-1.0` latency `22800ms` request `4f18979f-2662-4bcf-8ebc-d8539728e17e` status `ok`
- image prompt sha256 `be2e956a001bb370a95aa3469c0534689d4b944319de859c3bd6a5abed57446d`, trace sha `4c9e2ecf8110620261f2169336700460f61f6e554d14b9cbe77b22c2f25b601e` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

