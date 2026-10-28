# Vision on/off ablation (PREREGISTERED — results filled after firing)

Date preregistered: 2026-10-27. No vision-ON run has been fired yet
(run-ids below are reserved, bundles do not exist).

Hypothesis (H1): image feedback (--multimodal, literal-REVISE rule)
does not change first-attempt fix rate on these 5 cases, because the
text diagnosis already carries the failing line and the guard decides.
Null to beat: OFF arm fixes all 5 (it did historically).

Design: 5 cases x 2 arms (OFF default, ON --multimodal) x 2 seeds
(42, 43) = 20 runs. Same prompt v3, same guard, same oracle.
Reserved run-ids: vis-off-<case>-<seed>, vis-on-<case>-<seed>.

Cases: picosoc-uart-div, picorv32-fetch-misalign, uart-tx-div,
fetch-highword, rdata-latch.

Primary metric: first-attempt GREEN rate per arm.
Secondary: mean wall-clock, mean est_usd, verbatim-vs-bootstrap split.
Decision rule: if ON fixes >= OFF fixes with <=20% cost overhead,
claim "image is free insurance"; if ON fixes FEWER, cut the claim and
keep default OFF with evidence; if equal, report equality (a result,
not a failure).

Cost estimate: 20 x ~$0.07 = ~$1.40. Budget remaining ~$95+.

## Results (filled 2026-10-28, all 20 fired, job bash-fdf9bbbb)

OFF arm: 10/10 GREEN (all first-attempt, rc=0).
ON arm: 9/10 GREEN; vis-on-picosoc-uart-div-42 triaged after 3 attempts
(storm of API EMPTY-content flakes; diagnostic budget preserved, patch
withheld, exit 2). No case where ON fixed what OFF missed.

Costs (est_usd from bundle meta): OFF mean ~$0.069, ON mean ~$0.066 on
greens — the ON miss cost only $0.03 (withheld early, never burned a
bad patch). Vision feedback bought no fix-rate benefit on these cases.

First-attempt GREEN rate: OFF 10/10, ON 9/10.

## Decision (per prereg rule: ON fixes FEWER -> cut the claim)

Default stays text-only. --multimodal remains opt-in for human-in-the-
loop sessions. explain.png keeps its proven role: every bundle carries
one as the human-verification artifact (provenance-pinned), but the
loop does not pay for machine vision feedback until a case class shows
it helps. Negative result recorded, not hidden.
