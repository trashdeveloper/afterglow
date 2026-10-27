# Afterglow run report: stranger-fence-decode-20261026T220712Z

case `rdata-latch` | status **RED** | mode `live` | seed `42`

wall start `2026-10-26T22:08:53Z` | wall end `2026-10-26T22:09:55Z` | time-to-green `62.3s`

retries `0` | human_touches `0` | diagnosed `False` | fixed_unaided `False` | patch_source `none`

## Verdicts (from sim, not typed)

- BEFORE: tohost=FAIL 0xdead checksum=c2a7a4d6295484feeb9ec1ea9381732b8e39a7ccd1997c7d70f6454da2fb4659 cycles=2009 seed=42 pass=False
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
- input_trace.txt sha256 `dbd5e7a29d59ef7bc7f088142ac8ce42c2c0287820c89731341da0dbe96eb753` (9 lines, 69 tokens)
- prompt tokens `579` (cap 2500)
- text model `muse-spark-1.3-contributor` latency `?ms` request `?`
- image model `muse-image-1.0` latency `?ms` request `?` status `MISSING`
- image prompt sha256 `?`, trace sha `?` (see api/image_request.json)

## Explanation diagram

(explain.png MISSING -- image call failed, see meta.explain=FAILED)

## Bundle completeness

- present (5): artifacts/input_trace.txt, artifacts/boot.log, artifacts/verdict_before.json, api/text_request.json, meta.json
- missing (7): artifacts/boot_after.log, artifacts/verdict_after.json, api/text_response.json, api/image_request.json, api/image_response.json, explain.png, patch.diff

