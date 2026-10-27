# Afterglow run report: mutbigN-v3-16

case `mut-bigN-16` | status **TRIAGE** | mode `live` | seed `42`

wall start `2026-10-27T11:25:15Z` | wall end `2026-10-27T11:29:30Z` | time-to-green `255.3s`

retries `0` | human_touches `0` | diagnosed `False` | fixed_unaided `False` | patch_source `none`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=9bf5d2591144696f56ca014343e1655f97020c36292daceaf3f26e732301574d cycles=2009 seed=42 pass=False
- AFTER: tohost=None checksum=None cycles=None seed=42 pass=False

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

- prompt file `v3` sha256 `ad529368accaf171e6121a54b2d27547230920a900c7c4e5094d3fff13ca4233`
- input_trace.txt sha256 `326f64fb31b083fc118ff68ee8ad6416d8b1abaf34ac1e2acc43e95842827855` (9 lines, 69 tokens)
- prompt tokens `716` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `?ms` request `?`
- image model `muse-image-1.0` latency `?ms` request `?` status `MISSING`
- image prompt sha256 `?`, trace sha `?` (see api/image_request.json)

## Explanation diagram

(explain.png MISSING -- image call failed, see meta.explain=FAILED)

## Bundle completeness

- present (9): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, patch.diff, meta.json
- missing (3): api/image_request.json, api/image_response.json, explain.png

