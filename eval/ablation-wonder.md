# Wonder second-model ablation (PREREGISTERED — results filled after firing)

Date preregistered: 2026-11-04. No wonder-ON run has been fired yet
(run-ids below are reserved, bundles do not exist). This file is a
preregistration ONLY: writing it fires zero API calls and runs zero
sims. The controller fires the live burns separately.

Implementation landed (no live calls made by the implementer):
agent/models/wonder.py (muse-wonder-1.0 client, stdlib urllib, same
key envs MODEL_API_KEY/SPARK_API_KEY/META_API_KEY, same base env
SPARK_API_URL default https://api.meta.ai), loop.py --wonder routing
(attempt 1 = Spark, attempts 2+ = Wonder INSTEAD of the Spark retry;
same prompt, same guardrails, same resim gate), tests/
test_models_wonder.py (32 pass, live test skips with no key).

Endpoint (proven live on our key, per task brief): POST
https://api.meta.ai/v1/chat/completions, model muse-wonder-1.0,
standard OpenAI shape, response usage carries reasoning_tokens.

Hypothesis (H1): second-model diagnosis (--wonder) converts genuine
REDs that Spark retries miss, because an independent model's attempt-2+
diagnosis escapes the retry's direction-lock (Spark retries repeat
failed directions and burn the duplicate-reask; Wonder brings a fresh
diagnosis from the same prompt and trace).
Null to beat: OFF arm holds the ledger (2 RED stay RED, 2 GREEN stay
GREEN verbatim).

Design: 4 cases x 2 arms (OFF default, ON --wonder) x 1 seed (42) =
8 runs. Same prompt file, same guard, same oracle, same seed both
arms. Reserved run-ids: wonder-off-<case>-42, wonder-on-<case>-42.

Cases (genuine evidence from eval/verbatim-batch-ledger.md):
RED arm (model-verbatim applied, resim stayed RED — not flakes, not
guard blocks):
- mut-bigN-12 (uart TB mutant, num_bump picosoc/simpleuart.v:114,
  parent picosoc-uart-div; sim/targets/mut-bigN-12.yaml)
- pc-incr (fetch TB, ledger RED verbatim miss)
GREEN controls (verbatim greens, must not regress):
- picosoc-uart-div (locked case)
- uart-tx-div (locked case)

Note on pc-incr: the seed-43 waveform extension converted it in one
ON setting, so it is not an all-settings miss. That does not change
this design: the decision rests on the OFF-vs-ON same-seed contrast
fired by the controller, not on the historical ledger. If OFF also
converts pc-incr on seed 42, pc-incr drops out of the convertible set
and only mut-bigN-12 can carry the win.

Primary metric: per-arm fix rate on the 2 REDs (GREEN = verbatim
model patch + clean resim VERIFY checksum MATCH, zero human touches;
a Wonder patch counts ONLY on checksum MATCH, same gate as Spark).
Secondary: GREEN controls stay GREEN verbatim; mean wall-clock; mean
est_usd (cost ledger: Wonder usage is priced at Spark rates via the
PRICING fallback in afterglow_core/cost.py — logged, not free); model
split in the ON arm (which attempt's model served the deciding call,
from api/text_response.json model + meta model_id + api/
text_request.json model).
Decision rule (written BEFORE firing, binding): Wonder WINS iff the
ON arm converts >=1 RED unaided (verbatim second-model patch + clean
resim, 0 touches) that the OFF arm misses on the same seed, AND both
GREEN controls stay GREEN. Otherwise (equal REDs, or any green
regresses, or ON fixes only via bootstrap/non-verbatim): cut the
claim, keep default OFF, record the negative result. Equality is a
result, not a failure.

Cost estimate: 8 x ~$0.07 = ~$0.55. Firing is the CONTROLLER's call.

## Results (fired 2026-11-04, seed 42, controller-fired, ~$0.69 actual)

| case | OFF (wonder-off-*-42) | ON (wonder-on-*-42) |
| ---- | --------------------- | ------------------- |
| mut-bigN-12 | GREEN verbatim 2 lines, attempt 1 | TRIAGE (3x transport EMPTY, patch withheld) |
| pc-incr | GREEN verbatim 2 lines, attempt 2 (1 flake) | GREEN verbatim 2 lines, attempt 1 (Spark) |
| picosoc-uart-div | GREEN verbatim 4 lines, attempt 1 | TRIAGE (attempt-1 Spark EMPTY; attempt-2 Wonder invoked live muse-wonder-1.0, well-formed-but-wrong; no conversion) |
| uart-tx-div | GREEN verbatim 2 lines, attempt 3 | GREEN verbatim 2 lines, attempt 1 (Spark) |

ON-arm model split: both ON GREENs decided by Spark attempt-1 calls
(api/text_request.json model muse-spark; Spark never yielded to
Wonder). Wonder invoked exactly once live (uart-div attempt 2,
request id in api/text_request.json model muse-wonder-1.0) after a
Spark transport flake — router engages on Spark failure as designed.

## Decision (per prereg rule above — recorded from disk)

NO WIN — record the negative result. ON converted 0 REDs the OFF
arm missed (OFF went 4/4 GREEN); both GREEN controls stayed GREEN
(pc-incr, uart-tx-div verbatim both arms). Equality is a result, not
a failure: the router is proven live (Wonder invoked, well-formed
output, fail-loud preserved), but there is no evidence a second
model fixes what Spark cannot on this set. Default stays OFF.
Cut the Wonder-converts claim; keep the client as an opt-in path.
