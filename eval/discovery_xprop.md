# Discovery: reset/X-prop hunter (Task A8)

- case: picorv32-fetch-misalign (stock TB, pristine tree, --x-initial-edge variant build)
- targets: reg_op1 -> INCONCLUSIVE (no X anywhere in trace; refusing to claim clean)
- all_clean: NO (inconclusive is not clean)

INCONCLUSIVE (honest): the stock fetch TB never prints X-valued state, so
forcing could not be demonstrated and no clean verdict was faked.

Positive control (same tree, scratch copy, probe-only patch, no logic
change): XPROBE commit-signal lines carrying simulator-generated X
(`DIV=xxxxxxxx`, 2011 lines) -> LEAK with line-number + signal evidence.
Scanner verified end-to-end on a real Verilator 5.052 build+run log.

Verilator X-semantics note (7 mechanisms probed): x-literals, multidriver
last-wins, OOB reads, $readmemh x-digits, undriven nets, and runtime
div-by-zero all fold to 0 or last-wins; only display-inline div-by-zero prints X. A DUT-internal
X-leak mutant is therefore infeasible under this simulator -- reported,
not faked.
