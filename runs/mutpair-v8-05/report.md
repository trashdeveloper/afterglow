# Afterglow run report: mutpair-v8-05

case `mut-pair-05` | status **TRIAGE** | mode `live` | seed `42`

wall start `2026-10-28T13:17:24Z` | wall end `2026-10-28T13:31:49Z` | time-to-green `865.2s`

retries `0` | human_touches `0` | diagnosed `False` | fixed_unaided `False` | patch_source `none`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=563b7c10626ff25de2b3c430956c7c113e448c4efaea068f9b83a198d4322b15 cycles=2009 seed=42 pass=False
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
- input_trace.txt sha256 `bcff6d2092136818b80a6836210a7e8f9f0bde0ea4565a6a77bdd3b984f3d69c` (9 lines, 69 tokens)
- prompt tokens `989` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `?ms` request `?`
- image model `muse-image-1.0` latency `?ms` request `?` status `MISSING`
- image prompt sha256 `?`, trace sha `?` (see api/image_request.json)

## Explanation diagram

(explain.png MISSING -- image call failed, see meta.explain=FAILED)

## Bundle completeness

- present (9): artifacts/input_trace.txt, artifacts/boot.log, artifacts/boot_after.log, artifacts/verdict_before.json, artifacts/verdict_after.json, api/text_request.json, api/text_response.json, patch.diff, meta.json
- missing (3): api/image_request.json, api/image_response.json, explain.png

