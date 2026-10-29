"""Wave B evidence helpers (plan Tasks B1+). Each module is independent,
additive, and default-off: importing this package never changes loop
behavior (see test_loop_untouched_by_evidence_wiring)."""

from evidence.vcd_slice import (  # noqa: F401  (package re-export)
    build_evidence_pack,
    build_trace_run,
    find_fail_time,
    rank_toggles,
    slice_vcd,
    summarize_slice,
)
