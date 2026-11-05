# SAM-lane vs fixed-grid vs text-only ablation (PREREGISTERED — results unfired)

Date preregistered: 2026-11-05. No SAM call has been fired yet
(run-ids below are reserved, bundles do not exist). This file is a
preregistration ONLY: writing it fires zero API calls (text or SAM)
and runs zero sims. The controller fires the live burns separately.
Contract: agent/waveform/samlanes.py
(call_sam(bearer, png_bytes) -> lane boxes; crop_lanes(png, boxes) ->
PNG crops; stdlib urllib only), pinned by
tests/test_waveform_samlanes.py (canned fixture, loopback HTTP only).

Hypothesis (H1): SAM lane crops beat fixed-grid slicing on the 4
genuine REDs. A waveform PNG sliced into per-signal lanes by sam-3.1
boxes carries lane-aligned timing evidence (one crop per signal row,
no split lanes, no merged lanes) that a naive equal-strip slicing
cannot guarantee; the diagnosis image-model call should therefore fix
REDs that fixed-grid crops miss. Null to beat: fixed-grid slicing is
all you need (grid arm == SAM arm), or neither beats text-only.

Design: 6 cases x 3 arms (OFF text-only, GRID fixed-grid crops, LANE
SAM-lane crops) x 1 seed (42) = 18 runs. Same prompt file, same
guard, same oracle, same seed all arms. Reserved run-ids:
sam-off-<case>-42, sam-grid-<case>-42, sam-lane-<case>-42.

Cases (from eval/verbatim-batch-ledger.md, genuine verbatim evidence):
RED arm (all model-verbatim applied, resim stayed RED — not flakes,
not guard blocks):
- mut-bigN-12 (uart TB, DUMP_WAVES-capable -> GRID+LANE attach)
- mut-pair-01 (uart TB, DUMP_WAVES-capable -> GRID+LANE attach)
- mut-pair-03 (fetch TB, NO dump block -> GRID+LANE degrade loudly)
- pc-incr (fetch TB, NO dump block -> GRID+LANE degrade loudly)
  ("pc-incr-batch" in earlier briefs = case id pc-incr.)
GREEN controls (verbatim greens, must not regress):
- picosoc-uart-div (verbatim GREEN, 4 lines)
- uart-tx-div (verbatim GREEN, 2 lines)

The two fetch-TB REDs are degrade-path controls: GRID and LANE arms
must log meta.waveform.status=degraded with a DUMP_WAVES reason,
continue text-only, and match the OFF outcome exactly. They do NOT
test the slicing question (no waveform.png exists for them by
construction).

Arm mechanics (binding):
- OFF: today's default path, text-only, no waveform.png, no SAM call.
- GRID: render waveform.png (same renderer as the waveform pilot),
  slice with samlanes.fixed_grid_lanes (N = rendered signal-row
  count, so strip count == lane count), attach all N crops to the
  diagnosis image-model call. No SAM call fired in this arm.
- LANE: render waveform.png, call samlanes.call_sam ONCE with the
  session key (Bearer, same envs as the Spark path), slice with
  samlanes.crop_lanes on the returned boxes, attach the crops. A SAM
  failure (transport, empty, degenerate boxes) degrades LOUDLY to
  GRID for that run (meta.sam.status=degraded, reason logged) --
  never silently to text-only, never a guessed slicing.

Primary metric: per-arm fix rate on the 4 REDs (GREEN = verbatim
model patch + clean resim, zero human touches).
Secondary: GREEN controls stay GREEN verbatim; attached vs degraded
split in GRID/LANE arms; mean wall-clock; mean est_usd (text + SAM
calls metered separately in cost.json).
Decision rule (written BEFORE firing, binding): SAM WINS iff the LANE
arm fixes >=1 of the 4 REDs that BOTH the OFF and GRID arms miss,
with a verbatim model patch + clean resim, AND neither GREEN control
regresses in LANE. If GRID also fixes the same RED, credit goes to
slicing -- not SAM (the cheaper baseline wins, SAM claim cut). If
LANE fixes only via bootstrap/non-verbatim, or any green regresses:
cut the claim, keep default OFF, record the negative result. Equality
is a result, not a failure.

Cost cap (binding): $2.50 all-in for the 18-run matrix. Estimate:
18 text runs x ~$0.07 ~= $1.26 + SAM segmentation calls (only
attach-capable runs fire SAM: 4 uart cases x 1 arm x 1 seed = 4
calls x ~$0.05 ~= $0.20) ~= $1.50 projected. The controller STOPS
firing new runs the moment metered spend projects past the cap, and
reports the partial matrix as-is. Firing is the CONTROLLER's call;
this prereg fires nothing.

## Results (UNFIRED -- controller fills after firing)

| case | OFF (sam-off-*-42) | GRID (sam-grid-*-42) | LANE (sam-lane-*-42) |
| mut-bigN-12 | — | — | — |
| mut-pair-01 | — | — | — |
| mut-pair-03 | — | — | — |
| pc-incr | — | — | — |
| picosoc-uart-div | — | — | — |
| uart-tx-div | — | — | — |
| cost | — | — | — |

## Decision (per prereg rule above — fill after firing)

**UNFIRED.** No SAM WINS / CUT call made; no runs exist.
