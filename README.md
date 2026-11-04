<p align="center">
  <img src="video/film/public/wordmark.png" alt="afterglow" width="420">
</p>

<p align="center">
  <em>Silicon doesn't lie. Neither do we.</em><br>
  I got tired of chatbots guessing at my RTL. So I built one that has to prove it: red sim in, green sim out, zero hand edits.
</p>

<p align="center">
  <a href="eval/results.md">locked 3/3 · full 8/8</a> ·
  <a href="eval/mutant_board_100.md">72-fault board</a> ·
  <a href="eval/ablation-waveform.md">text+waveform beats text alone</a> ·
  <a href="gallery/index.html">gallery</a>
</p>

---

## Try it in 30 seconds

```bash
git clone <this-repo> && cd afterglow
./install.sh        # deps, TUI build, .env stub, then doctor as proof
./afterglow demo    # triage-only run of the fastest locked case (~44s, patch withheld)
```

With a key in `.env`, the real thing:

```bash
./afterglow run sim/targets/picosoc-uart-div.yaml
# RED → COLLECT → DIAGNOSE → EXPLAIN → PATCH → RESIM → VERIFY
# GREEN: case picosoc-uart-div fixed (model-verbatim, 4 patch lines)
```

On a terminal you get the Rust TUI. Piped or on CI it's plain text. Same loop either way.

## How it actually works

- **RED first.** The sim fails. The agent gets a 200-line window around the failure, not the whole dump.
- **Text decides, image explains.** Muse Spark reads the trace and writes the diagnosis; Muse draws the failure so a human can sanity-check it. Bad patches get rejected by the guardrails (40-line cap, allowlisted RTL, lint must pass).
- **Re-sim is the whole game.** A fix only counts if the checksum flips to MATCH. Every run lands in `runs/<id>/`: the trace, the raw API requests, the diff, before/after checksums. If it's not in the bundle, it didn't happen.

## Receipts

| claim | where to look |
|---|---|
| locked 3/3, full 8/8 | `eval/results.md` (generated from bundles, never hand-edited) |
| 72 fresh faults, all RED-proven then fixed | `eval/mutant_board_100.md` (seed 777, quarantined from older families) |
| median 54s / ~$0.07 a fix | `eval/impact.md`, `eval/tournament.md` (costs logged per run) |
| text+waveform beats text alone | `eval/ablation-waveform.md` (preregistered, converted a real RED) |

Suite: `python3 -m unittest discover -s tests` → OK (2 honest skips: needs a key, needs a VCD flag). Gate: `bash scripts/ci.sh` → `CI GATE: ALL PASS`.

<details>
<summary>Repo map</summary>

- `afterglow` — CLI: `run` (TUI on a terminal, plain text when piped), `demo`, `explain`, `init`, `watch`, `doctor`
- `agent/` — the loop, guardrails, prompts, waveform renderer, open-weights client
- `tui-rs/` — the Rust terminal UI (built by `install.sh`; missing binary = plain-text fallback, nothing breaks)
- `sim/` — Verilator harness on pinned PicoRV32 + hand-written testbenches
- `eval/` — boards, preregs, tournament + impact writeups (generated, not hand-edited)
- `repro/` — one-command Docker repro + `EXPECTED.md` checksums
- `runs/` — immutable proof bundles
- `video/` — the 3-minute demo film + sources

</details>

## Foundation

PicoRV32 is someone else's (YosysHQ, pinned by hash in `targets.pin` — we don't take credit for it). Everything else here was written Oct 27–Nov 4 and committed in that window; nothing predates it.
