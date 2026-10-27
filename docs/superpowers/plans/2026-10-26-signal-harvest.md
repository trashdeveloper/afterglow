# Signal Harvest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Squeeze every cheap, deterministic signal out of the failing sim and the RTL tree with real algorithms (slicing, fingerprinting, graph ranking, scored packing) before the model ever sees a token, so a mid-tier model diagnoses like a senior engineer.

**Architecture:** New `agent/harvest.py` owns all pre-model computation (pure functions, static fixtures, no key); `agent/context.py` keeps gathering and `build_pack` becomes a scored merge over harvest sections; `agent/prompts/v3.txt` adds `{{HARVEST}}`; loop wires it behind the same v1-default rule (flip only on locked 3/3 re-run, key-gated).

**Tech Stack:** Python stdlib only (re, json, subprocess git, os.walk), Verilator trees already pinned, existing `collect.approx_tokens` ruler.

**Spec:** `docs/superpowers/specs/2026-10-26-rtl-debug-framework-design.md` (context-pack v2 section) + this plan's algorithm choices below.

## Global Constraints

- Verilator-only; no UVM/cocotb/TB generation; no new dependencies (stdlib only, CPU-only).
- Full prompt stays <= 2500 tokens (`collect.MAX_PROMPT_TOKENS`); harvest sections share the 900-token pack budget (`context.PACK_BUDGET_TOKENS`).
- Trace window keeps its 200-line / first-priority budget: packer may never starve trace+verdict below their current ~600-token typical shape.
- All implementation commits dated 2026-10-27 – 2026-11-04; TDD (failing test first); every task ends with a reviewer gate.
- Key-gated steps (live ablation) are marked and run with the user, never with a stored key.

## Review Focus

- Generate blocks / `ifdef / macros hide real drivers from the regex slicer -> slice must report coverage (drivers found vs signals sought), and an empty slice must degrade to today's lexical ranking, never to an empty section.
- Over-normalized fingerprints collide two different bugs into one signature -> fingerprint keeps the anchor line verbatim alongside the normalized hash, and memory retrieval shows both.
- `git blame` on a huge tree can stall -> 30s timeout already set; packer treats timeout as "" and logs `blame: timeout`, never blocks the run.
- Scored packer must not eat the trace budget -> packer asserts trace+verdict tokens unchanged from the v1 baseline within 5% before merging harvest sections.
- A TB without waveform support must not RED the run -> Task 1 is a recon spike: if the C++ TBs lack `--trace`, VCD becomes a logged `vcd: unsupported` line and the task ships the other three signals.

---

### Task 1: Recon spike — what signals are actually harvestable?

**Files:**
- Create: `docs/superpowers/specs/2026-10-26-signal-harvest-recon.md`
- Test: none (spike; time-boxed reads only, no code changes)

**Interfaces:**
- Consumes: `sim/sim.py`, `agent/vbackend.py`, `targets/*.yaml`, C++ TB sources, `eval/cases.yaml`
- Produces: recon doc with a YES/NO table + token-cost estimates that Tasks 2–6 take as ground truth

- [ ] **Step 1: Read the sim path end to end.** Trace `sim/sim.py` → `vbackend.py` → TB invocation; record exactly what text lands in `boot.log` and `verdict.json` per case.
- [ ] **Step 2: Check waveform support.** Do the C++ TBs accept a trace flag? Is there any existing `.vcd`/FST output? Record YES/NO per TB.
- [ ] **Step 3: Sample the RTL.** On `targets/picorv32` + `targets/serv`: count `assign`/`always` blocks, generate usage, macro usage, instantiation depth — the constructs the Task 2 slicer must survive.
- [ ] **Step 4: Write the recon doc.** Table: signal → source → extraction cost → token shape → verdict (harvest / skip). Tasks 2–6 may only harvest rows marked harvest.

### Task 2: Failure fingerprint + signature memory

**Files:**
- Create: `agent/harvest.py` (with `normalize_trace`, `fingerprint`, `signature_index`)
- Test: `tests/test_harvest_fingerprint.py`

**Interfaces:**
- Consumes: recon doc Table (which log lines are stable vs volatile)
- Produces: `normalize_trace(trace_block: str) -> str`; `fingerprint(normalized: str) -> str` (sha256 of normalized + verbatim anchor line); `signature_index(runs_dir: str) -> dict[str, list[str]]` (fingerprint → green run-ids with patch summaries)

- [ ] **Step 1: Write the failing test.** Two traces differing only in cycle numbers/hex case produce the same fingerprint; two different anchor lines do not; `signature_index` on a fixture `runs/` returns only green runs. As code with exact expected hashes computed by hand from the fixture.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_harvest_fingerprint.py -v`. Expected: FAIL with "No module named harvest" (or `agent.harvest` import error).
- [ ] **Step 3: Implement the three functions in `agent/harvest.py`.** Normalize: strip leading cycle counters, lowercase hex, collapse whitespace runs; keep the last anchor line verbatim, appended after the hash input with a `|` separator.
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_harvest_fingerprint.py -v`. Expected: PASS.
- [ ] **Step 5: Review gate.** Fresh reviewer: collision behavior, normalization overreach (a volatile field that changes the diagnosis must survive normalization — reviewer names one from the recon doc).

### Task 3: Backward slice from the failing signal (real cone, bounded)

**Files:**
- Create: `agent/slice.py` (with `parse_drivers`, `backward_slice`)
- Test: `tests/test_slice.py`

**Interfaces:**
- Consumes: recon doc Table (which constructs appear: assign/always/instantiation)
- Produces: `parse_drivers(tree: str, rel: str) -> dict[str, list[str]]` (signal → driving signals, regex-based, documents what it cannot parse); `backward_slice(drivers_by_file, seed: str, max_depth: int = 3, max_lines: int = 40) -> list[str]` (line-numbered, coverage-reported)

**Background the signatures/tests do not determine:** parse `assign lhs = rhs;`, `always @(...)` blocks (every `<=`/`=` target driven by every identifier on RHS lines), and module instantiation port maps (`.port(sig)` → port driven by sig). Generate blocks and macros are recorded in an `unparsed` count, not silently skipped.

- [ ] **Step 1: Write the failing test.** Fixture tree: `top.v` instantiates `alu.v`; bug signal `rdata` driven by `adder_out` driven by `a,b`. Assert `backward_slice(..., "rdata")` contains all three levels within 40 lines and reports `coverage: 3/3 seeds resolved`. Second fixture with a `generate` block asserts graceful `unparsed: 1` and non-empty fallback.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_slice.py -v`. Expected: FAIL with import error.
- [ ] **Step 3: Implement `agent/slice.py`.** Regex parser per the Background above; slice BFS from seed, depth-capped, line-capped, coverage counted.
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_slice.py -v`. Expected: PASS.
- [ ] **Step 5: Review gate.** Fresh reviewer: adversarial fixture (macro-defined signal, `ifdef`'d driver) must degrade, not crash or empty-section.

### Task 4: Module graph + blame recency scoring

**Files:**
- Create: `agent/graph.py` (with `module_graph`, `rank_modules`, `blame_scores`)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `slice.backward_slice` output shape; `context.blame_snippet` behavior
- Produces: `module_graph(tree: str, rtl_files: list[str]) -> dict[str, list[str]]` (module → instantiated children); `rank_modules(graph, slice_files: list[str]) -> list[tuple[str, float]]`; `blame_scores(tree: str, rel: str, center: int) -> dict[int, float]` (line → recency weight 1.0 newest → 0.1 older than 20 commits, "" tree → {})

- [ ] **Step 1: Write the failing test.** Fixture: `top` instantiates `uart` + `alu`; slice hits `uart` only → `rank_modules` puts `uart` first with score > `alu`. `blame_scores` on a 3-commit git fixture gives the newest-touched line strictly higher weight; on a non-git tree returns {}.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_graph.py -v`. Expected: FAIL with import error.
- [ ] **Step 3: Implement `agent/graph.py`.** Instantiation scan `modulename instname (`; score = slice-overlap share + 0.2 × blame-recency of the file's hottest line.
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_graph.py -v`. Expected: PASS.
- [ ] **Step 5: Review gate.** Fresh reviewer: score formula sanity on a diamond-instantiation fixture (shared child must not double-count).

### Task 5: Scored packer under the fixed budget

**Files:**
- Modify: `agent/context.py` (add `score_sections`, `merge_scored`; `build_pack` gains `harvest=` kwarg, default off)
- Test: `tests/test_context.py` (append `TestScoredMerge`)

**Interfaces:**
- Consumes: `harvest.fingerprint/signature_index`, `slice.backward_slice`, `graph.rank_modules/blame_scores`, `collect.approx_tokens`
- Produces: `score_sections(sections: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]` (name, text, score sorted desc); `merge_scored(scored, budget: int) -> (text, dropped_names)`; `build_pack(..., harvest: dict | None)` unchanged output shape when `harvest=None`

**Background the signatures/tests do not determine:** fixed scores — fingerprint-memory hit 1.0, slice 0.9, localization 0.7, blame 0.5, past-fixes 0.4; fill budget greedily in score order; trace+verdict assertion (within 5% of v1 baseline) runs before merge.

- [ ] **Step 1: Write the failing test.** Sections totaling 3× budget with known scores → merged text contains the top two, `dropped_names` names the third; `build_pack(harvest=None)` output identical to current behavior on the existing fixtures.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_context.py -v`. Expected: FAIL on missing `merge_scored`.
- [ ] **Step 3: Implement scoring + merge in `agent/context.py`.** No changes to existing section builders.
- [ ] **Step 4: Run tests to verify they pass.** Run: `python3 tests/test_context.py -v` plus full suite. Expected: PASS, CI green.
- [ ] **Step 5: Review gate.** Fresh reviewer: a rigged low-score-but-critical section must still be droppable (scores are priorities, not guarantees — reviewer confirms no section is load-bearing-by-assertion).

### Task 6: Wire harvest into the loop + prompt v3 + eval per-class

**Files:**
- Modify: `agent/loop.py` (harvest assembly in COLLECT, meta fields), `eval/score.py` (per-class rows)
- Create: `agent/prompts/v3.txt`, `tests/test_harvest_wire.py`
- Test: `tests/test_harvest_wire.py`

**Interfaces:**
- Consumes: all Task 2–5 functions; `loop.bug_line_no`, `loop.rtl_files`
- Produces: `assemble_harvest(trace_block, tree, rtl_rel, case) -> (text, counts)`; prompt v3 with `{{HARVEST}}` + all v2 slots; meta fields `harvest_tokens`, `fingerprint`, `slice_coverage`, `top1_is_true`; eval rows per bug-class (localization accuracy, fingerprint-hit rate)

- [ ] **Step 1: Write the failing test.** Fixture case on a 3-file tree: `assemble_harvest` returns text containing slice lines + fingerprint hex + ranked modules, counts under budget; v3 template renders with all six slots substituted (no `{{` left).
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_harvest_wire.py -v`. Expected: FAIL with import error.
- [ ] **Step 3: Implement wiring + v3 + eval rows.** v1 default untouched; v3 opt-in via `--prompt`. Eval reads only bundles (no hand-typed numbers).
- [ ] **Step 4: Run tests to verify they pass.** Run: full suite + `bash scripts/ci.sh`. Expected: ALL PASS.
- [ ] **Step 5: Review gate.** Fresh reviewer, strictest one: confirms patch-target rules unchanged, token caps enforced with failing-loud asserts, no guardrail weakened.

### Task 7 (key-gated, with user): ablation — v1 vs v2 vs v3 on locked 3 + stranger set

**Files:**
- Modify: none (runs/ bundles only)
- Test: live runs, not unit tests

**Interfaces:**
- Consumes: Task 6 wiring, MODEL_API_KEY from the user at run time
- Produces: `runs/ablation-*.json` comparison (attempts, time-to-green, top1 accuracy per prompt version); decision: flip default to the winner only on locked 3/3

- [ ] **Step 1: Re-run locked 3 on v2.** Record attempts/time-to-green/top1 per case.
- [ ] **Step 2: Re-run locked 3 + stranger 6 on v3.** Same metrics.
- [ ] **Step 3: Publish the ablation table** in `eval/results.md` (generated, not hand-typed) and flip `--prompt` default only if the winner is 3/3 locked.

## Self-Review

1. **Spec coverage:** recon (spec: know the sim path) → Task 1; structural localization (spec: beyond lexical) → Tasks 3–4; memory upgrade (spec: learn from fixes) → Task 2; budgeted merge (spec: fixed budget) → Task 5; v3 + measurement (spec: ablation story) → Tasks 6–7. Covered.
2. **Step scan:** each test step names exact file, function, assertions; each code step gives exact signatures; bodies appear only for the regex-slice algorithm and the scoring formula, which signatures alone do not determine. Fixed inline.
3. **Type consistency:** `fingerprint -> str` feeds `signature_index -> dict[str, list[str]]`; `backward_slice -> list[str]` feeds packer sections as `(name, text, score)` tuples; meta/count key names (`harvest_tokens`, `fingerprint`, `slice_coverage`, `top1_is_true`) match in Tasks 5–6. Consistent.
4. **Review Focus:** all five lines have owning tasks (generate/macros → Task 3 test 2; fingerprint collision → Task 2; blame stall → Task 4 fail-soft + Review Focus log line; trace starvation → Task 5 assert; no-VCD TB → Task 1 spike). Pinned.
5. **Proportion:** plan ≈ spec section it implements; code blocks only where the regex algorithm and score table need them. OK.
