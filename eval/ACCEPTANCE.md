# Afterglow fresh-design acceptance log (Task 6)

_Machine-checked by `scripts/check_acceptance.sh`. Field lines below
are the record; prose sections carry the evidence. Live loop runs
landed 2026-10-26 (controller-run with session key, never on disk)._

design: serv (olofk/serv SERV bit-serial RISC-V CPU, pinned post-fix)
repo: https://github.com/olofk/serv.git
commit: 727bb40a8784c50b081abdcfd784a5bc7c34e40e
setup_minutes: 4
triage_run: afterglow-fresh-serv-alu-lut-triage
auto_run: afterglow-fresh-serv-alu-lut-auto2
outcome: GREEN

## Run notes (controller live runs, 2026-10-26)

- Triage `afterglow-fresh-serv-alu-lut-triage`: exit 2, diagnosis
  saved (correctly names the bo=01 LUT fault), patch withheld.
- First `--auto` attempt (`afterglow-fresh-serv-alu-lut-auto`):
  RED on an API empty-response flake (61.6s latency, no content);
  bundle kept, nothing hidden.
- Retry `--auto` (`afterglow-fresh-serv-alu-lut-auto2`): exit 0
  GREEN, 79s wall (22:52:16Z-22:53:35Z), model diagnosis verbatim,
  verbatim hunk failed dry-run so the canonical bootstrap-gated
  applier applied a 2-line patch, re-sim PASS, `fixed_unaided`,
  `human_touches: 0`, `explain: ok`.

## Why this design

- A different subsystem from everything the framework has seen: a
  small CPU (SERV) rather than a second UART, per the brief's
  preference. Not picorv32, not simpleuart/picosoc.
- Real upstream fix, preference (a): `727bb40` ("Simplify control
  logic for bool ops", 2021-03-14, Olof Kindgren) corrected
  `BOOL_LUT 16'h8E96 -> 16'h8E06` in `rtl/serv_alu.v` (bool_op `01`
  gave `rs1==op_b` instead of `0`, corrupting shift passthrough).
  History alternatives reviewed and rejected with reasons in the
  Task 6 report (refactors/whitespace-only, or fixed lines gone at
  HEAD); no workforce-authored fault was needed.
- Small enough for CPU-only Verilator: 57-line single-file unit, no
  submodules, no includes; RED proven ~4 min after first fetch.

## Setup window (wall-clock, honest)

- Start 2026-10-26T22:24:25Z (first `git clone` of olofk/serv).
- RED proven 2026-10-26T22:28:11Z (`sim/sim.py` verdict timestamp).
- Total 4 minutes, inside the 30-minute target. Covers clone, full
  history fetch, fix-commit review, pin + YAML + TB authorship, and
  the manual RED run. No framework code was touched at any point.

## Manual proof (no key required, both directions)

- RED: `python3 sim/sim.py --case serv-alu-lut --seed 42` printed
  `serv-alu-lut RED tohost=FAIL 0xdead ... (inject x1)`, exit 1. The
  boot log shows exactly the two discriminating mismatches
  (`bo=01` equal-input pairs read `1`, want `0`); all six sanity rows
  pass, so the TB is not vacuous. Checksum
  `c434c3f745c5...`, `CYCLES=24`.
- PASS direction: pristine tree (`is_fixed()` true, anchor verbatim)
  builds and prints `tohost: PASS` with all ten checks ok. The loop's
  `ensure_tree` resets + re-injects every phase, so these manual runs
  leave no state behind.

## Framework changes forced by the run

- None. Zero changes to `afterglow_core/`, the CLI, guard, or prompts.
  Config-only: one `targets.pin` line, one new CaseSpec YAML under
  `sim/targets/`, one TB under `sim/tb/`. The RTL basename
  (`serv_alu.v`) was already allowlisted.

## Handoff: the two live runs (controller, needs key)

- Prerequisites: key present in the environment under `MODEL_API_KEY`
  (or `SPARK_API_KEY` / `META_API_KEY`); nothing else is missing.
- First command (triage, expect exit 2):
  `./afterglow run --spec sim/targets/serv-alu-lut.yaml --run-id afterglow-fresh-serv-alu-lut-triage`
- Second command (auto, GREEN with bundle or honest RED):
  `./afterglow run --spec sim/targets/serv-alu-lut.yaml --auto --run-id afterglow-fresh-serv-alu-lut-auto`
- Record is final: the run-ids and outcome above are the real bundles;
  re-verify with `bash scripts/check_acceptance.sh` (expect PASS), and
  rerun the gate set. Never invent run-ids: the check requires a
  `runs/<id>/meta.json` bundle for each.
