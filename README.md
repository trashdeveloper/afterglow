# Afterglow — Muse Spark agent that debugs silicon

> **Foundation: pre-existing PicoRV32 (YosysHQ, link+hash in `targets.pin`/`foundation.pin`). Entry: agent + eval + repro + runs, all commits Oct 27–Nov 4.**

A Muse Spark agent watches a RISC-V sim, diagnoses failures from raw traces, patches the RTL, re-runs the sim, and verifies itself. Red to green, zero human RTL edits.

## One command

```bash
docker run -e MODEL_API_KEY=$MODEL_API_KEY ghcr.io/<you>/afterglow --case picosoc-uart-div
# RED exit 1 checksum MISMATCH → live agent (text + image calls, ids shown) → GREEN exit 0 checksum MATCH <sha>, <10 min CPU-only
```

Local (needs `MODEL_API_KEY` for live Text/Image calls):

```bash
./repro/run.sh --case picosoc-uart-div --seed 42
```

## Quickstart: zero to first GREEN in ~5 minutes

You need: Linux or macOS, Verilator, and a model key. Nothing else.

```bash
git clone <this-repo> && cd afterglow
./afterglow init
# ok: verilator found at .../verilator
# ok: MODEL_API_KEY is set in the environment (or found in .env)
# ok: foundation.pin readable
# ok: targets.pin readable
# example spec: sim/targets/my-first-case.yaml (from built-in template)
# If a line says FAIL, it names the exact fix (install Verilator,
# export MODEL_API_KEY=..., restore the pin file) -- fix it, re-run init.
# A machine without Verilator gets that FAIL line, never a traceback.
# init is checkout-anchored: run it from the repo root; the scaffold lands in
# this checkout's sim/targets/.
export MODEL_API_KEY=...   # skip if init already found it in env or .env
./afterglow run --spec sim/targets/picosoc-uart-div.yaml
# afterglow run: spec sim/targets/picosoc-uart-div.yaml (case picosoc-uart-div)
# ... RED -> COLLECT -> DIAGNOSE -> EXPLAIN -> PATCH -> RESIM -> VERIFY ...
# GREEN: case picosoc-uart-div fixed (model-verbatim, 4 patch lines)
./afterglow explain runs/<run-id-printed-by-the-run-above>
# 1. RED symptom (tohost: FAIL ...) / 2. Diagnosis (verbatim quote) /
# 3. PATCH (4 changed lines) / 4. VERIFY (checksums MATCH) / 5. COST
```

What success looks like: `run` exits 0 and prints GREEN, with the
BEFORE/AFTER checksums matching `repro/EXPECTED.md` (AFTER starts
`5c8f58d0` for this case); `explain` shows the RED symptom, the
verbatim diagnosis quote, the 4-line patch, checksums MATCH, and the
cost line. Other useful surfaces, all agreeing (`./afterglow --help`):
`./afterglow run --explain runs/<id>` (same story as `explain`),
`./afterglow run --spec ... --triage` (diagnose, withhold patch,
exit 2), `./afterglow --version` (`afterglow 0.2.0`).

Measured on unseen single-point faults (receipts, not hype):
median time-to-green 54s, mean $0.069/fix, 11/11 live greens + 1 loud
RED that refused to invent (see `eval/impact.md`, `eval/tournament.md`).

## Proof, not claims

Every green claim ships a `runs/<run-id>/` bundle: failing trace window, boot logs before/after, builder verdicts, raw API request/response with latency + request ids, generated `explain.png` with input hashes, `git diff` of the patch, checksums before/after. `eval/score.py` reads only those bundles to build the board.

## Layout

- `agent/` — `loop.py` (RED→VERIFY), `collect.py`, `guard.py`, `visual.py`, `report.py`, `explain.py` (bundle story), `prompts/v1.txt`, `allowlist.txt`
- `afterglow` CLI — `run --spec` (full-auto; `--triage`, `--goals`, `--explain`), `explain runs/<id>`, `init` (checks + example spec), `--version`
- `afterglow_core/` — `verilator.py`, `policy.py`, `cost.py`, `initenv.py` (init checks), `slicer.py`, `packer.py`, `fingerprint.py`, `memory.py`
- `sim/` — deterministic Verilator harness on pinned PicoRV32 + 2 testbenches, 8 targets
- `eval/` — `cases.yaml` (8 real cases), `score.py`, `results.md` (generated)
- `repro/` — `run.sh`, `Dockerfile`, `EXPECTED.md`, `TESTED.md`
- `runs/` — proof bundles (immutable once written)

## Submission draft

Chip bring-up takes weeks and a verification team. Only giants can afford it. Afterglow is a Muse Spark agent that reads RISC-V sim traces, diagnoses failures, patches RTL, re-runs the sim, and verifies itself. One Docker command goes red to green: locked 3/3 green, full 6/8, checksums match, zero human RTL edits. Text decides every patch under guardrails; Image draws the failure explanation from the actual trace for human check. All runs logged with prompts, diffs, and latencies. Built Oct 27–Nov 4 on frozen open-source PicoRV32 foundation. If a student can fix a UART loopback because an AI read the trace, open silicon stops being elite.
