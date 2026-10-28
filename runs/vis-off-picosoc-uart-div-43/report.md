# Afterglow run report: vis-off-picosoc-uart-div-43

case `picosoc-uart-div` | status **GREEN** | mode `live` | seed `43`

wall start `2026-10-28T00:15:05Z` | wall end `2026-10-28T00:16:07Z` | time-to-green `61.1s`

retries `0` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `model-verbatim`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=27ee482b473c74db5dce8b5ab1a233084cce3055c317a1f2045faa6dbc13a1c6 cycles=2112 seed=43 pass=False
- AFTER: tohost=PASS checksum=5c8f58d0f0818c97d014d2f851a6140882bd1efc96a2537047806252c0c2caf9 cycles=2652 seed=43 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Loopback uart with divider 64 sent 0xa5 but recv returned 0xe0 causing `TRAP: uart data mismatch got 0xe0 want 0xa5` and tohost FAIL 0xdead. Root cause in excerpt is recv bit-period compare uses `(cfg_divider >> 4)` at lines 90 and 97 for data/stop states, sampling ~16x too fast instead of full `cfg_divider`. Via cone picosoc/picosoc.v:190 instance to picosoc/simpleuart.v:20, half-bit align `2*recv_divcnt > cfg_divider` at line 84 is correct, so data bits must use full divider.
```

expected re-sim signal: tohost PASS (was FAIL 0xdead), no TRAP, uart loopback 0xa5 matched, checksum != 27ee482b473c74db5dce8b5ab1a233084cce3055c317a1f2045faa6dbc13a1c6

## Patch (4 lines, guard-checked)

```diff
diff --git a/picosoc/simpleuart.v b/picosoc/simpleuart.v
--- a/picosoc/simpleuart.v
+++ b/picosoc/simpleuart.v
@@ -89,14 +89,14 @@
 				10: begin
-					if (recv_divcnt > (cfg_divider >> 4)) begin
+					if (recv_divcnt > cfg_divider) begin
 						recv_buf_data <= recv_pattern;
 						recv_buf_valid <= 1;
 						recv_state <= 0;
 					end
 				end
 				default: begin
-					if (recv_divcnt > (cfg_divider >> 4)) begin
+					if (recv_divcnt > cfg_divider) begin
 						recv_pattern <= {ser_rx, recv_pattern[7:1]};
 						recv_state <= recv_state + 1;
 						recv_divcnt <= 0;
 					end
 				end

```

## Provenance (judge checks)

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `a7cc571a0a0b3df9c44ee4a72129906b0315d42ae65bcbdd6d7626aacc88df15` (9 lines, 68 tokens)
- prompt tokens `809` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `44210ms` request `chatcmpl-01a17fdc-f78b-711e-870d-5dd817644331`
- image model `muse-image-1.0` latency `16429ms` request `260b3104-8f38-49a1-aa03-f082ff1c6297` status `ok`
- image prompt sha256 `f242f64ed64b8cdd5eec8b899c73369131f8b25b997846733dd77e560300418f`, trace sha `a7cc571a0a0b3df9c44ee4a72129906b0315d42ae65bcbdd6d7626aacc88df15` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

