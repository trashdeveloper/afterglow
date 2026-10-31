# Waveform on/off ablation (PREREGISTERED — results filled after firing)

Date preregistered: 2026-10-31. No waveform-ON run has been fired yet
(run-ids below are reserved, bundles do not exist). This file is a
preregistration ONLY: writing it fires zero API calls and runs zero
sims. The controller fires the live burns separately.

Hypothesis (H1): attaching a rendered fail-window waveform PNG to the
diagnosis image-model call (--waveform) fixes genuine REDs that
text-only misses, because the image carries signal-timing evidence
(divisor vs baud, start-bit sampling) the 200-line text window cannot.
Null to beat: OFF arm holds the ledger (4 RED stay RED, 2 GREEN stay
GREEN verbatim).

Design: 6 cases x 2 arms (OFF default, ON --waveform) x 1 seed (42) =
12 runs. Same prompt file, same guard, same oracle, same seed both
arms. Reserved run-ids: wave-off-<case>-42, wave-on-<case>-42.

Cases (from eval/verbatim-batch-ledger.md, genuine verbatim evidence):
RED arm (all model-verbatim applied, resim stayed RED — not flakes,
not guard blocks):
- mut-bigN-12 (uart TB, DUMP_WAVES-capable -> ON attaches)
- mut-pair-01 (uart TB, DUMP_WAVES-capable -> ON attaches)
- mut-pair-03 (fetch TB, NO dump block -> ON degrades loudly)
- pc-incr (fetch TB, NO dump block -> ON degrades loudly)
  ("pc-incr-batch" in the task brief = case id pc-incr in batch.)
GREEN controls (verbatim greens, must not regress):
- picosoc-uart-div (verbatim GREEN, 4 lines)
- uart-tx-div (verbatim GREEN, 2 lines)

The two fetch-TB REDs are degrade-path controls: ON must log
meta.waveform.status=degraded with a DUMP_WAVES reason, continue
text-only, and match the OFF outcome exactly. They do NOT test the
attach path (no VCD exists for them by construction).

Primary metric: per-arm fix rate on the 4 REDs (GREEN = verbatim
model patch + clean resim, zero human touches).
Secondary: GREEN controls stay GREEN verbatim; mean wall-clock;
mean est_usd; attached vs degraded split in ON arm.
Decision rule (written BEFORE firing, binding): waveform WINS iff the
ON arm fixes >=1 of the 4 REDs the OFF arm misses, with a verbatim
model patch + clean resim, AND neither GREEN control regresses.
Otherwise (equal REDs, or any green regresses, or ON fixes only via
bootstrap/non-verbatim): cut the claim, keep default OFF, record the
negative result. Equality is a result, not a failure.

Cost estimate: 12 x ~$0.07 = ~$0.85. Firing is the CONTROLLER's call.

## Results (NOT FIRED — fill after the controller runs the burns)

| case | OFF (wave-off-*-42) | ON (wave-on-*-42) |
| mut-bigN-12 | _pending_ | _pending (attach expected)_ |
| mut-pair-01 | _pending_ | _pending (attach expected)_ |
| mut-pair-03 | _pending_ | _pending (degrade expected)_ |
| pc-incr | _pending_ | _pending (degrade expected)_ |
| picosoc-uart-div | _pending_ | _pending (attach expected)_ |
| uart-tx-div | _pending_ | _pending (attach expected)_ |

## Decision (per prereg rule above — fill after firing)

_pending_
