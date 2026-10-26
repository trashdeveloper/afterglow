# Afterglow — Plan (11/10, locked)

> Muse Spark agent that debugs silicon: watches a RISC-V sim, diagnoses
> failures from raw traces, patches the RTL, re-runs the sim, verifies
> the fix itself. Red to green, zero human RTL edits.

- Track: Indie Developer. Problem track: Push the Frontier.
- Team: solo. Submission: 3-min video + public repo + one-command repro.
- Status: planning. **All implementation commits Oct 27 – Nov 4.**
- **Nothing in this repo except PLAN.md + AGENTS.md predates the window.**
- Law: **AGENTS.md is the build standard.** If PLAN and AGENTS conflict, AGENTS wins. Devving happens in THIS repo at root (`agent/`, `eval/`, `repro/`, `runs/`).
- North star: a tired judge who assumes it's fake says "okay, that's real" by 1:50.

## Why this wins

1. **Moving:** chip bring-up takes weeks + a verification team. Students/hobbyists can't afford that. Afterglow compresses trace-reading to minutes. Claim ONLY time saved + access, backed by time-to-green numbers. No watt claims without joules (per AGENTS.md §9).
2. **Meta load-bearing x2, both proven:** Text API is the sole patch decider (no regex fallback). Image API generates the explanation diagram FROM the failing window + diagnosis hash, saved into the same `runs/<id>/` BEFORE re-sim. Provenance visible or we cut the 2-surface claim.
3. **Real:** real sim, real `git diff`, real re-boot, BEFORE/AFTER checksums. Raw request/response logged. Replay mode is CI-only, never presented as live.
4. **Re-runnable:** one Docker command, CPU-only, <10 min, checksums match `EXPECTED.md`.

Fallback is locked: same loop, smaller bug, same green. Never no green.

## Gaps the judge found → what we fixed (do these or lose)

| # | Judge's kill-shot | Fix locked in this plan + AGENTS.md |
|---|-------------------|-------------------------------------|
| 1 | "Image is decoration" | Provenance rule: image input = trace hash + diagnosis verbatim, output saved pre-resim, embedded in report. If not verifiable by Oct 29, cut to 1-surface honestly. |
| 2 | "Verify mode = replay = fake" | Live mode DEFAULT for judging. Replay labeled `mode: replay, not a claim`, CI-only. Video shows live API latency + request id. |
| 3 | "8/10 with 8 cases? Can't count" | Fixed: **8 total, target 6/8, locked 3/3**. `score.py` generates `results.md`, never hand-edited. |
| 4 | "4min Linux fix in 65s = cuts = human help" | **Uncut PATCH→GREEN minute required:** continuous capture + wall-clock overlay, fast-forward only with `>>8x` badge. Diagnosis shown verbatim from JSON. |
| 5 | "3-line fix is a parameter, not debugging" | Frame honestly: tiny divisor, hours of trace-reading. Show rejected bad patch + trace window that proves reasoning. Small + true > big + shaky. |
| 6 | "Weft = taking credit for pre-existing work" | Video 0:20-0:45 MUST show `git log --since Oct 27` (this repo) + `foundation.pin` (weft hash, pre-window). README header labels split. CI fails if weft files copied here. |
| 7 | "Solo 8 days for 8 cases + Docker + video? Fantasy" | Schedule below is files-per-day, no new features after Oct 30, Oct 31 repro must go cold-green by a non-author. `advisor.py` CUT permanently. |
| 8 | "Energy per chip = hype" | Removed. Impact = engineer-hours + respins avoided, backed by time-to-green. |

## The demo (180s, locked, uncut proof inside)

No talking head. Screen + voiceover + captions. Numbers on screen for every claim.

**0:00-0:20 Pain**
"Chip bring-up takes weeks. You stare at this [trace wall], you need a verification engineer, only giants can afford one."
Visual: raw fetch trace scroll, `tohost: FAIL`, red `checksum MISMATCH`.

**0:20-0:45 Setup + compliance in 25s**
"Afterglow is a Muse Spark agent. Frozen RISC-V foundation below — I don't touch it. Everything above is new, Oct 27-Nov 4. One command."
Visual: `git log --oneline --since Oct 27` + `cat foundation.pin` + `cat repro/run.sh`. Show root layout: `agent/ eval/ repro/`.

**0:45-1:50 The moment (65s)**
Split: left agent, right sim. Wall-clock overlay ON from here.

1. RED: `./repro/run.sh --case priv-tap-misclass` (default video case) → fail, exit 1, freeze on 200-line window.
2. COLLECT: `fetch.trace[200] + boot.log[trap] + verdict.json` stacking → "312 lines → 2.1k tokens, prompt v3 `a4f2...`".
3. DIAGNOSE: live Text API call, show latency + request id. Diagnosis card VERBATIM from `text_response.json`.
4. EXPLAIN: `explain.png` generated FROM that window+diagnosis (show hashes). "It draws what it thinks so you can check it."
5. PATCH: `git diff` — ≤40 lines, allowlisted file. Show one rejected diff first (trust booster).
6. GREEN, UNCUT: continuous capture, no cuts, `tohost: PASS`, `checksum MATCH <sha>`, "1 retry, 0 human RTL edits." Linux footage (if green) plays here fast-forwarded with `>>8x` + preserved timer; otherwise Linux sits on eval board as "in progress."

Main video case = `priv-tap-misclass` (~30s sim) UNLESS Linux `uart-divisor` is fully proven by Oct 30 23:59. Speed wins trust. Moonshot is bonus, not bet.

**1:50-2:30 Proof it's not one trick**
"Same loop, different bug. No new code."
Eval board flips: locked 3/3 green, then full `6/8` with 2 honest reds + log links. Voiceover: "Two it couldn't fix. Logs in repo."

**2:30-3:00 Meaning**
"If a student can boot Linux because an AI read the trace, open silicon stops being elite. Fewer weeks of bring-up, fewer respins."
End card: repo URL, one command, "All new Oct 27-Nov 4, all live API calls, all logs in `runs/`."

## Architecture (THIS repo, root)

```
./
  PLAN.md             # this file (planning only, pre-window OK)
  AGENTS.md           # law (pre-window OK) — all code follows it
  foundation.pin      # weft URL + frozen commit hash, created Oct 27
  agent/
    loop.py           # RED→COLLECT→DIAGNOSE→EXPLAIN→PATCH→RESIM→VERIFY
    collect.py        # windowing ≤200 lines, ≤2500 tokens, logs counts
    guard.py          # allowlist + ≤40 lines + lint + diff + 2 retries
    visual.py         # trace hash + diagnosis → image → explain.png pre-resim
    report.py         # builds report.md from bundle, embeds explain.png
    allowlist.txt     # RTL paths agent may touch
    prompts/v1.txt…   # versioned, hash logged, bump = rerun locked 3
  eval/
    cases.yaml        # 8 cases: inject method + oracle + sim budget
    score.py          # reads runs/ only → results.md
    results.md        # generated, never hand-edited
  repro/
    run.sh            # ONE command, live default, exit 1 RED / 0 GREEN
    Dockerfile        # cached toolchain + weft @ foundation.pin, CPU-only
    EXPECTED.md       # BEFORE/AFTER checksums per case
    TESTED.md         # non-author cold-run log
  runs/<id>/          # 13-file proof bundle per AGENTS.md §1
  scripts/ci.sh       # window check + no-weft-copy + forbidden-strings + score
```

Loop contract: Text API sole decider, fail loudly if down. Every run saves full bundle. Guard rejects or retries. Image provenance required. See AGENTS.md §1-4 for MUSTs.

Foundation (NOT this repo): Weft (Cell-V, region builder, CVA6 tap/port, Linux boot) pinned in `foundation.pin`, consumed as submodule/docker layer, never copied. CI fails on copy.

CUT forever: `advisor.py`, viral/community plays, watt claims.

## Eval (8 cases, defined like tests)

`eval/cases.yaml` format per case: `inject: <file:line patch>`, `oracle: <tohost + checksum + log grep>`, `sim_budget: <seconds>`, `tier: locked|stress`.

| Tier | Case | Budget | Inject → Oracle |
|------|------|--------|-----------------|
| LOCKED | `priv-tap-misclass` | 30s | tap privilege bit flip → `tohost PASS` + `mstatus.MPP` trap gone |
| LOCKED | `stuck-pending` | 60s | pending-bit clear dropped → boot progress past M-mode |
| LOCKED | `linux-uart-divisor` | 4min | divisor /16 → `Linux version` + console legible + checksum MATCH |
| STRESS | `uart-baud-2x` | 30s | baud x2 → console garbage → legible |
| STRESS | `csr-misa-mask` | 45s | MISA mask wrong → capability trap → clean |
| STRESS | `timer-cmp-stuck` | 60s | mtimecmp stuck → timer irq storm → idle |
| STRESS | `fetch-misalign` | 90s | fetch addr LSB → misaligned trap → sequential |
| STRESS | `plic-prio-invert` | 120s | PLIC prio invert → irq storm → ordered |

Score: `diagnosed? / fixed unaided? / reruns / time-to-green / patch lines / human touches`. Target **locked 3/3, full ≥6/8**. 2 reds stay red with logs.

## Repro (judge hungover test)

```bash
docker run ghcr.io/<you>/afterglow ./repro/run.sh --case priv-tap-misclass
# RED exit 1 MISMATCH → live agent (2 API calls, ids shown) → GREEN exit 0 MATCH <sha>, <10 min
```

- Prebuilt image, CPU-only, cold <10 min or broken.
- `EXPECTED.md` lists BEFORE/AFTER sha per case. Mismatch = fail.
- Non-author cold run required before Nov 1, logged in `TESTED.md`.

## Targets

| Cat (wt) | Target | Proof |
|---|---|---|
| Impact 30% | 9 | Time-to-green numbers + access story, no watt hype |
| Meta 25% | 9 | Text decides + image explains with provenance, both live on camera |
| Depth 20% | 9 | Closed loop, 3/3 + 6/8, checksums/cycles/latency logged, no mocks |
| Demo 25% | 9 | 180s, uncut PATCH→GREEN, captions, non-hardware viewer test |
| Compliance | pass | Window commits, split labeled, raw API logs |

Bonus in range: Best Agentic Use-Case. Not chased: viral, vote, open-weights.

## Schedule (solo, files-per-day, kill criteria bold)

- **Oct 27 — loop exists.** Create `foundation.pin`, `agent/loop.py`, `collect.py`, `prompts/v1.txt`, `scripts/ci.sh` skeleton. First live Text call on real log. Done = RED→RESIM runs end-to-end + 1 proof bundle (even if diagnosis dumb).
- **Oct 28 — first green.** `guard.py` + `allowlist.txt` + `report.py`. Lock `priv-tap-misclass` green unaided. Freeze `v1`. Done = 1/1 bundle complete, phone video OK.
- **Oct 29 — eval + visual.** All 8 in `cases.yaml`, `score.py` → `results.md` (even 2/8), `visual.py` live with provenance. First Linux RED capture. Done = board runs. **If image provenance unconvincing, cut 2-surface claim here.**
- **Oct 30 — prompts only, no features.** Iterate `v2/v3`, rerun locked 3 each bump. Linux must go green with bundle. **23:59 ABORT: Linux or small-bug main pair. No revisit.**
- **Oct 31 — repro + public.** Dockerfile cold <10min, `EXPECTED.md`, `TESTED.md` non-author run, repo public, README split header, submission draft. Done = friend green cold.
- **Nov 1 — buffer, no new code.** Repro fixes only. Record all raw video runs into `runs/`.
- **Nov 2-3 — cut + test.** 180s, uncut minute, captions, checksums readable. Non-hardware viewer: can they repeat one-liner? If bored/confused, recut same day.
- **Nov 4 — submit before noon.** Run `scripts/ci.sh` gate (AGENTS.md §11). Ship smaller truth if any box red. Breathe.

Daily: commits with numbers (`eval 4/8, p50 2m10s, prompt v3 a4f2`), prompt hash in logs, never edit old `runs/`.

## Risks

1. Sim slow → locked cases <60s except Linux; Linux `>>8x` with timer; Docker caches build.
2. Hallucinated patch → guard + 2 retries + rejected-patch footage.
3. Linux slips → Oct 30 abort, small-bug main.
4. Image decorative → provenance or cut.
5. Docker huge/slow → prebuilt layers, CPU-only, `<10min` hard gate.

## Submission assets (Nov 4)

- [ ] Public repo, README split header, `foundation.pin` visible
- [ ] 180s video (uncut PATCH→GREEN, wall-clock, verbatim diagnosis, checksums)
- [ ] Docker one-command green <10min + EXPECTED.md + TESTED.md
- [ ] `eval/results.md` (generated, 3/3 + 6/8, reds with logs)
- [ ] 3 locked `runs/` bundles, 13 files each, zero human RTL edits
- [ ] Submission text ≤150 words: problem → loop → numbers → CTA

Draft (142 words): "Chip bring-up takes weeks and a verification team. Only giants can afford it. Afterglow is a Muse Spark agent that reads RISC-V sim traces, diagnoses failures, patches RTL, re-runs the sim, and verifies itself. One Docker command goes red to green: `priv-tap-misclass` fixed unaided in 42s, locked 3/3 green, 6/8 overall, checksums match, zero human RTL edits. Text API decides every patch; Image API draws the failure explanation from the actual trace for human check. All runs logged with prompts, diffs, and latencies. Built Oct 27-Nov 4 on frozen open-source Weft foundation. If a student can boot Linux because an AI read the trace, open silicon stops being elite."

## Rules

AGENTS.md §0-11 enforce everything above. Read it before every coding session. Numbers over adjectives. Ship truth.
