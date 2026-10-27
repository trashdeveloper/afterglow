# Impact (measured, 2026-10-27 — not projected)

The claim is narrow and receipted: an unseen single-point RTL fault goes from
RED to verified-GREEN in about a minute, for about seven cents, with no human
touching the RTL. Everything below reads out of proof bundles, not slides.

- Fix rate on unseen faults: 11/11 live greens (5 mutants × v3, 5 × v2 incl.
  one rerun, 1 locked ablation pair) + 1 loud RED that refused to invent.
- Time: median time-to-green 54s; mean ≈ 92s including flake storms.
- Cost: mean $0.069/fix (meta.json est_usd, ESTIMATES table) → a $100 budget
  funds ≈ 1,400 fixes. Tonight's whole campaign (tournament + ablation + opt
  pilot, 14 runs) cost ≈ $0.97.
- Human touches per fix: 0 (meta.json human_touches; pressing Enter excluded by
  rule, editing RTL would disqualify the case).
- Trust mechanism: guard rejects non-verbatim/oversize/off-allowlist patches;
  resim checksum must MATCH; transport flakes retry with backoff, never invent;
  exhausted budgets go loud-RED with the bundle kept.
- What it is NOT (kept out of the submission): no watt claims, no Hz claims,
  optimize mode shows only the safety half so far (unsafe opt rejected live;
  first cycles_saved still to be demonstrated).

Who this serves: the verification engineer in-flow — the loop runs where the
testbench already runs, reads the same RED a human would read, and hands back
a 4-line verbatim patch plus the re-sim that proves it.
