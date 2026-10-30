# Afterglow run report: mutpair-v5-04

case `mut-pair-04` | status **TRIAGE** | mode `live` | seed `43`

wall start `2026-10-28T01:44:45Z` | wall end `2026-10-28T01:55:58Z` | time-to-green `672.9s`

retries `2` | human_touches `0` | diagnosed `True` | fixed_unaided `False` | patch_source `none`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=43 pass=False
- AFTER: tohost=None checksum=None cycles=None seed=43 pass=False

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
- input_trace.txt sha256 `7c532834ac692803a2fa2d2a358d945e0e67b219b88886860f92efe045a1fddd` (9 lines, 69 tokens)
- prompt tokens `829` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `?ms` request `?`
- image model `muse-image-1.0` latency `35861ms` request `57949d18-9925-4e37-ae3a-ad11edd935c6` status `ok`
- image prompt sha256 `9f7f55c573cb646541fc75c9fb5eaf79feee97713f9c335e3430c9623caff8c5`, trace sha `7c532834ac692803a2fa2d2a358d945e0e67b219b88886860f92efe045a1fddd` (see api/image_request.json)

## Explanation diagram

![explain.png](explain.png)

## Bundle completeness

- present (12): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff, meta.json
- missing (0): none

