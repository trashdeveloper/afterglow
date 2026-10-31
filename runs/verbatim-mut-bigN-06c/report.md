# Afterglow run report: verbatim-mut-bigN-06c

case `mut-bigN-06` | status **RED** | mode `live` | seed `42`

wall start `2026-10-31T03:28:38Z` | wall end `2026-10-31T03:28:38Z` | time-to-green `0.1s`

retries `0` | human_touches `0` | diagnosed `False` | fixed_unaided `False` | patch_source `none`

## Verdicts (from sim, not typed)

- BEFORE: tohost=PASS checksum=5c28238265cb94168311e9914f0df355642c557d04dba11845d557ac8a64aa4a cycles=2009 seed=42 pass=True
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

- prompt file `/home/tuncay/afterglow/agent/prompts/v2.txt` sha256 `?`
- input_trace.txt sha256 `MISSING` (? lines, ? tokens)
- prompt tokens `?` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `?ms` request `?`
- image model `muse-image-1.0` latency `?ms` request `?` status `MISSING`
- image prompt sha256 `?`, trace sha `?` (see api/image_request.json)

## Explanation diagram

(explain.png MISSING -- image call failed, see meta.explain=FAILED)

## Bundle completeness

- present (3): artifacts/boot.log, artifacts/verdict_before.json, meta.json
- missing (9): artifacts/input_trace.txt, artifacts/boot_after.log, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff

