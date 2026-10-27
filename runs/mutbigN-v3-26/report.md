# Afterglow run report: mutbigN-v3-26

case `mut-bigN-26` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-27T11:48:54Z` | wall end `2026-10-27T11:50:20Z` | time-to-green `85.7s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Trace shows TRAP: no progress max_addr=0x00000000 with tohost FAIL 0xdead instead of progress through 256-word NOP memory. mem_done at picorv32.v:376 requires (mem_do_rinst && mem_do_rdata || mem_do_wdata) so a fetch-only cycle with mem_do_rinst alone never asserts mem_done via the mem_xfer && |mem_state path. This holds mem_busy without mem_done and stalls instruction fetch, explaining zero progress; restoring || fixes the handshake.
```

expected re-sim signal: tohost PASS replacing FAIL 0xdead, no TRAP: no progress, max_addr advances >0x00000000

## Patch (2 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -375,3 +375,3 @@
 	wire mem_busy = |{mem_do_prefetch, mem_do_rinst, mem_do_rdata, mem_do_wdata};
-	wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst && mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) &&
+	wire mem_done = resetn && ((mem_xfer && |mem_state && (mem_do_rinst || mem_do_rdata || mem_do_wdata)) || (&mem_state && mem_do_rinst)) &&
 			(!mem_la_firstword || (~&mem_rdata_latched[1:0] && mem_xfer));

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `efd974adc7b30209e1b11b2134437ff7117952785155e25df1ad598f8bc3f66c` (9 lines, 69 tokens)
- prompt tokens `792` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `43470ms` request `chatcmpl-01a17d31-cdfd-73f9-9780-b86e348eea59`
- image model `muse-image-1.0` latency `41536ms` request `7aa50280-b342-4618-af8c-3c09e7d8516d` status `ok`
- image prompt sha256 `e46eecf1bbf5bc6686a5b00c90d4d30604c362bab97414d652c40d484b807fde`, trace sha `efd974adc7b30209e1b11b2134437ff7117952785155e25df1ad598f8bc3f66c` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

