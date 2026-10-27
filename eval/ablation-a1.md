# Ablation A1: v2 (default) vs v3 (harvest) on picosoc-uart-div

Date: 2026-10-27 (live API, seeds 42). Question: does the v3 harvest pack
change fix rate or cost on a known single-point fault?

| run | prompt | status | patch | checksum | $ | text tok | harvest |
|---|---|---|---|---|---|---|---|
| ablate-v2-uartdiv | v2 | green (attempt 1) | verbatim 4 lines | 5c8f58d0 MATCH | 0.074154 | 7020 | n/a |
| ablate-v3-uartdiv | v3 | green (attempt 1) | verbatim 4 lines | 5c8f58d0 MATCH | 0.067004 | 6142 | ok: fp 9e84de80, cone 18 lines, pack 36x177tok |

v3 live fingerprint (9e84de80) matches the pre-registered aiming value exactly.
Finding: on single-point faults v3 matches v2 (both first-attempt greens) at
~10% lower token cost. Separation on harder multi-line faults TBD (mutation
campaign). v2-default regression: PASS (v2 greens what v1 greened).

## OPT pilot live (opt-pilot-uartdiv, --goals fix+opt, same session)

Fix: GREEN attempt 1, verbatim 4 lines, checksum 5c8f58d0 MATCH.
Opt pass: model proposed a patch, resim checksum 4d9e6fef != GREEN 5c8f58d0
-> REJECTED (behavior-change), GREEN fix stands, exit 0.
Finding: the equivalence gate works live — an unsafe opt was caught and the
fix was never ungreened. First CYCLES-saving opt still to be demonstrated.
