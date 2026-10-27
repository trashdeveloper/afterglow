# Afterglow Adapter Guide — onboarding a fresh RTL design (config-only)

This guide is derived from the Task 6 acceptance run (`serv-alu-lut`,
`srv`/`olofk/serv`): every step below is something that run actually
required. Adapting the framework to a new design needs **no code
changes** — three config artifacts only.

## The plugin surface (what you touch, what you don't)

Create / edit (config):

- `targets.pin` — one line: `<project> <git-url> <40-hex-sha>`
- `sim/targets/<case>.yaml` — the CaseSpec (flat `key: value`, see below)
- `sim/tb/<case>_tb.sv` — your testbench speaking the oracle protocol

Never touch for a new design: `afterglow_core/`, `afterglow` CLI,
`agent/guard.py`, `agent/prompts/`. If onboarding seems to require a
change there, that change IS the finding — log it, make the minimal
fix, and note it in `eval/ACCEPTANCE.md`. The Task 6 run required none.

One boundary to know: the patch guard compares **basenames** against
`agent/allowlist.txt`. If your RTL file's basename is not listed there,
every model patch will be rejected. The Task 6 file (`serv_alu.v`) was
already listed, so no change was needed; if yours is not, adding the
one basename line is a config change — record it as a framework touch.

## Prerequisites (as used)

- Verilator (`verilator --version` — Task 6 used 5.052), `patch`,
  Python 3.12 stdlib only, network access for the initial clone.
- `MODEL_API_KEY` (or `SPARK_API_KEY` / `META_API_KEY`) in the
  environment for triage/`--auto`. Never write the key to disk.

## Step 1 — Pin the design

Append to `targets.pin`:

```
serv https://github.com/olofk/serv.git 727bb40a8784c50b081abdcfd784a5bc7c34e40e
```

Format is `<name> <url> <sha>`; the loop clones to
`~/.cache/afterglow/work/<name>-<sha12>/`, checks out the sha, and
resets dirty state before every phase, so experiments never leak
between runs. Pin the **post-fix** commit when you reproduce a real
upstream fix (anchor = fixed line, bug = pre-fix line).

## Step 2 — Write the testbench (oracle protocol)

`sim/sim.py --case <id>` builds `[files...] + sim/tb/<tb>` with
`verilator --binary --timing` and runs the binary with `+seed=<N>`.
Your TB must print, deterministically:

- `tohost: PASS` on success, or `tohost: FAIL 0xdead` plus one or more
  `TRAP: <reason>` lines on failure (the `oracle_pass` / `oracle_trap`
  markers — `oracle_pass` must be non-blank or the spec is refused).
- `CYCLES=<n>` (scored and logged; any monotonic counter works).
- Accept `+seed=<n>` via `$value$plusargs` (logged; may be unused).

Worked example (`sim/tb/serv-alu-lut_tb.sv`, top `tb_serv_alu`): drives
the SERV ALU BOOL-LUT rows directly. Bool-op `01` must read `0` for all
four input pairs (the historical fix); xor/or/and rows are sanity
checks that pass under both LUT values, proving the harness is not
vacuous. Keep the TB small: the failing window fed to the model is
capped at 200 lines / 2500 tokens.

## Step 3 — Write the CaseSpec YAML

`sim/targets/serv-alu-lut.yaml` (flat keys, same schema as the
eight eval cases):

```yaml
project: "serv"
files: ["rtl/serv_alu.v"]
top: "tb_serv_alu"
tb: "serv-alu-lut_tb.sv"
anchor: "localparam [15:0] BOOL_LUT = 16'h8E06;//And, Or, 0, xor"
bug: "localparam [15:0] BOOL_LUT = 16'h8E96;//And, Or, =, xor"
gate: "bool lut alu shift buf"
oracle_pass: "tohost: PASS"
oracle_trap: "TRAP:"
```

Rules learned from the run:

- `--spec` is in-tree-only: the file stem must equal
  `sim/targets/<stem>.yaml`, because RED always dispatches
  `sim/sim.py --case <stem>`. Keep the case id, YAML stem, and TB
  `top` in agreement.
- `anchor` must equal the fixed source line **after stripping** —
  internal spacing included. Copy it from the pinned tree (e.g. via
  `git show <sha>:<path>`), not by retyping. The run verified this
  with `is_fixed()` on the pristine tree and `inject x1` on the RED run.
- `files` are repo-relative RTL paths inside the pinned project; the
  model patch must touch only these basenames (guard-enforced).
- `gate` words are your safety net: if the verbatim model diff is
  rejected, the loop applies the line-anchored canonical restore only
  when the live diagnosis names a gate word. Pick words the true
  diagnosis will naturally contain (signal and block names).

## Step 4 — Prove RED manually, then prove the PASS direction

```bash
python3 sim/sim.py --case serv-alu-lut --seed 42 \
  --boot /tmp/fresh_boot.log --verdict /tmp/fresh_v.json
# expect: `serv-alu-lut RED ... (inject x1)`, exit 1
```

Then confirm the pristine tree passes (the loop's `ensure_tree`
resets + re-injects every run, so this check leaves no state behind):

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, "sim")
import vbackend
case = vbackend.load_case("serv-alu-lut")
pins = vbackend.load_pins()
url, sha = pins[case["project"]]
tree = vbackend.ensure_tree(case["project"], sha, url, case["files"])
assert vbackend.is_fixed(tree, case)          # anchor present, verbatim
binary = vbackend.build(tree, case)           # pristine build must succeed
rc, log = vbackend.sh([str(binary), "+seed=42"], cwd=str(tree), timeout=300)
assert "tohost: PASS" in log, log             # PASS direction proven
print("PASS direction OK")
EOF
```

The Task 6 run showed RED with exactly the two discriminating
mismatches (`bo=01` equal-input pairs read `1`, want `0`) and PASS with
all ten checks ok after restore. If BEFORE is already PASS, the inject
does not reproduce — fix the TB/bug pair, never the oracle.

## Step 5 — Triage, then `--auto`

```bash
./afterglow run --spec sim/targets/serv-alu-lut.yaml --run-id afterglow-fresh-serv-alu-lut-triage
# expect: triage stop, exit 2, diagnosis saved, patch withheld
./afterglow run --spec sim/targets/serv-alu-lut.yaml --auto --run-id afterglow-fresh-serv-alu-lut-auto
# expect: exit 0 GREEN with runs/<run-id>/ proof bundle, or honest exit 1 RED
```

Use the `afterglow-fresh-*` run-id prefix so acceptance bundles are
attributable. A RED `--auto` with a visible run-id is a valid result —
do not delete or edit bundles; fix by creating a new run-id.

## Step 6 — Log it and verify the gates

Record the design, pin, setup minutes, both run-ids, and the outcome in
`eval/ACCEPTANCE.md` (see `scripts/check_acceptance.sh` for the
machine-checked shape), then:

```bash
bash scripts/check_acceptance.sh   # PASS iff the record has shape + bundles
bash scripts/ci.sh                  # ALL PASS
python3 eval/score.py               # main board intact
python3 eval/stranger_score.py      # stranger board byte-identical
```

Extra cases in `runs/` whose ids are not in `eval/cases.yaml` are
reported by the scorer as UNKNOWN, never scored — the fresh run cannot
disturb the 8-case board by existing.

## Troubleshooting (observed)

- `FATAL: unknown --spec stem` — YAML stem has no
  `sim/targets/<stem>.yaml` twin. Rename to match.
- `FATAL: unknown project` — `targets.pin` lacks the project line.
- `BEFORE run already PASS` — inject line never matched (anchor
  drift) or the TB passes despite the bug. Check `inject xN` in the
  sim output (`x0` for this backend raises instead) and re-verify the
  anchor bytes against the pinned tree.
- `bootstrap refused (diagnosis names no gate signal)` — the model
  never said any gate word. Enrich `gate` with block/signal synonyms.
- `patch -p1 dry-run failed` — the model diff's paths did not resolve;
  the loop already retries via basename search, so a persistent
  failure means the hunk context does not match the excerpt: keep
  patches within the 40-line excerpt scope.
- Live text/image calls fail loudly (exit 1, partial bundle kept) and
  never fall back to heuristics. A run with `explain: FAILED` in
  `meta.json` still counts; a run that never reaches DIAGNOSE (no key)
  is not an acceptance run — record it as pending, never as RED.
