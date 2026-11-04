# Afterglow run report: wave-off-pc-incr-43

case `pc-incr` | status **GREEN** | mode `live` | seed `42`

wall start `2026-10-31T15:37:49Z` | wall end `2026-10-31T15:49:00Z` | time-to-green `671.5s`

retries `2` | human_touches `0` | diagnosed `True` | fixed_unaided `True` | patch_source `canonical-bootstrap-gated`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
- AFTER: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True

## Diagnosis (verbatim from api/text_response.json)

```
Fetch makes no progress: max_addr stays 0x00000000 for 2009 cycles leading to tohost FAIL 0xdead. Excerpt assigns both reg_pc and reg_next_pc to current_pc with no stride, so next fetch re-issues address 0x0. Root cause is missing PC increment at lines 1526-1527, which must add compressed-aware stride (compressed_instr ? 2 : 4) since zero-filled mem decodes as compressed.
```

expected re-sim signal: tohost PASS (no FAIL 0xdead) with fetch progress max_addr advancing beyond 0x0 over 256-word walk

## Patch (4 lines, guard-checked)

```diff
diff --git a/picorv32.v b/picorv32.v
--- a/picorv32.v
+++ b/picorv32.v
@@ -1550,7 +1550,7 @@
 					if (irq_pending) begin
 						latched_store <= 1;
 						reg_out <= irq_pending;
-						reg_next_pc <= current_pc;
+						reg_next_pc <= current_pc + (compressed_instr ? 2 : 4);
 						mem_do_rinst <= 1;
 					end else
 						do_waitirq <= 1;
@@ -1558,7 +1558,7 @@
 				if (decoder_trigger) begin
 					`debug($display("-- %-0t", $time);)
 					irq_delay <= irq_active;
-					reg_next_pc <= current_pc;
+					reg_next_pc <= current_pc + (compressed_instr ? 2 : 4);
 					if (ENABLE_TRACE)
 						latched_trace <= 1;
 					if (ENABLE_COUNTERS) begin

```

## Provenance (judge checks)

- prompt file `/home/tuncay/afterglow/agent/prompts/v2.txt` sha256 `d31e1ef6b74ddf52ee59e790ebff2f3defa9b03be82e699f45ef620cf8e27a6e`
- input_trace.txt sha256 `7b9c85f05cca4ab0b4ef3067d399dadd11721f8bec35ffcdb57fbac3b49b7906` (9 lines, 69 tokens)
- prompt tokens `721` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `76592ms` request `chatcmpl-01a192a6-2ab6-74c3-99fd-f53710d2483d`
- image model `muse-image-1.0` latency `12950ms` request `46b0b990-2997-4f8a-9f7c-52cb08027b1b` status `ok`
- image prompt sha256 `61f2862a57cac5eefc90a16906a06b52a494eed33028d0e6e68adf4761d0fcc8`, trace sha `7b9c85f05cca4ab0b4ef3067d399dadd11721f8bec35ffcdb57fbac3b49b7906` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

