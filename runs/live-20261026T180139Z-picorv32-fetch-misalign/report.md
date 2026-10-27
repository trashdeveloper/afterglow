# Afterglow run report: live-20261026T180139Z-picorv32-fetch-misalign

case `picorv32-fetch-misalign` | status **RED** | mode `live` | seed `42`

wall start `2026-10-26T18:01:39Z` | wall end `2026-10-26T18:04:07Z` | time-to-green `148.6s`

retries `2` | human_touches `0` | diagnosed `True` | fixed_unaided `False` | patch_source `none`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=a29998cd8e81501b64b44a66e009c12b297662db5ef5369b12ef3feae608e5d5 cycles=2009 seed=42 pass=False
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

- prompt file `agent/prompts/v1.txt` sha256 `a33e9d03790a89ce89d4d12a7b53c72268fcccaed45861c132aa9fd413792056`
- input_trace.txt sha256 `e7cb2dbaab3806944b85bf0981c5570cdbd694a9ac1e1441ab96d81b26ebdf0f` (? lines, ? tokens)
- prompt tokens `?` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `?ms` request `?`
- image model `muse-image-1.0` latency `15979ms` request `db149831-5f3b-485a-aa85-d7229bf0a18c` status `ok`
- image prompt sha256 `9be727ec5e6a93f948e432eb0a111ff07016db0bf491bda3649a40a50b3bc575`, trace sha `e7cb2dbaab3806944b85bf0981c5570cdbd694a9ac1e1441ab96d81b26ebdf0f` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (11): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (1): api/text_request.json

