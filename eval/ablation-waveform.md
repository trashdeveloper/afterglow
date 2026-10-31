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

## Results (FIRED 2026-10-31, seed 42, controller-run)

| case | OFF (wave-off-*-42) | ON (wave-on-*-42) |
| mut-bigN-12 | RED (3 attempts, compositional miss) | RED (3 attempts, model-verbatim 2-line, wf attached) |
| mut-pair-01 | RED (3 attempts, compositional miss) | GREEN (model-verbatim 4-line, unaided, wf attached+sent) |
| mut-pair-03 | TRIAGE (transport flakes x2, patch withheld) | GREEN (model-verbatim 4-line, unaided, wf degraded/picorv32 TB) |
| pc-incr | GREEN (canonical-bootstrap-gated 4-line) | GREEN (canonical-bootstrap-gated 4-line, wf degraded) |
| picosoc-uart-div | GREEN (model-verbatim 4-line) | GREEN (model-verbatim 4-line, wf attached+sent) |
| uart-tx-div | GREEN (model-verbatim 2-line) | GREEN (model-verbatim 2-line, wf attached+sent) |
| cost | $0.4058 measured (6 runs, from bundle meta.json est_usd) | $0.4931 measured (6 runs, from bundle meta.json est_usd) |

## Decision (per prereg rule above — fill after firing)

**WAVEFORM WINS** (binding rule satisfied): ON converted mut-pair-01 (OFF RED after 3 full attempts → ON GREEN model-verbatim 4-line, unaided, 0 touches, waveform PNG attached and sent with sha 34acd526…, clean resim VERIFY PASS), and neither GREEN control regressed (uart-div + uart-tx-div both verbatim GREEN in both arms). mut-bigN-12 RED in both arms (compositional miss stands; wave attached but didn't convert). mut-pair-03 OFF TRIAGE → ON GREEN is a second recovery but wf-degraded (picorv32 TB has no VCD path), so it does NOT count as wave-caused — the verdict rests on 01 alone. Caveats recorded: (a) single seed (42), n=1 conversion; (b) ON-01 used the compositional stack (stacked=True) — fully unaided/model-authored, prereg didn't exclude it; (c) only uart-TB cases currently receive wave input (others degrade loudly per design). Per the rule as bound ("Equality is a result, not a failure" — and this wasn't equality): keep the claim, waveform stays default-OFF pending a confirm-or-revise follow-up, record stands.

## Costs (measured)
