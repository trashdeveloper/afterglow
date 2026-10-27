# Afterglow run report: afterglow-fresh-serv-alu-lut-auto

case `serv-alu-lut` | status **RED** | mode `live` | seed `42`

wall start `2026-10-26T22:49:35Z` | wall end `2026-10-26T22:50:37Z` | time-to-green `62.0s`

retries `0` | human_touches `0` | diagnosed `False` | fixed_unaided `False` | patch_source `none`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c434c3f745c57e3e4c9aa9d169cd7f5e42aaa94479044e7e81b5a950778db135 cycles=24 seed=42 pass=False
- AFTER: MISSING

## Diagnosis (verbatim from api/text_response.json)

```
MISSING (text call produced no diagnosis)
```

expected re-sim signal: -

## Patch (0 lines, guard-checked)

```diff
(no patch applied)
```

## Provenance (judge checks)

- prompt file `/home/tuncay/afterglow/agent/prompts/v1.txt` sha256 `a33e9d03790a89ce89d4d12a7b53c72268fcccaed45861c132aa9fd413792056`
- input_trace.txt sha256 `358b6dabde9397643b61ab48de7a72b5c11c820265f7aaa9e7d6b3ad6b2caa06` (19 lines, 137 tokens)
- prompt tokens `574` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `?ms` request `?`
- image model `muse-image-1.0` latency `?ms` request `?` status `MISSING`
- image prompt sha256 `?`, trace sha `?` (see api/image_request.json)

## Explanation diagram

(explain.png MISSING -- image call failed, see meta.explain=FAILED)

## Bundle completeness

- present (5): artifacts/input_trace.txt, artifacts/boot.log, artifacts/verdict_before.json, api/text_request.json, meta.json
- missing (7): artifacts/boot_after.log, artifacts/verdict_after.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff

