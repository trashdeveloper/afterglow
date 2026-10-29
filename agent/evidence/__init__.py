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
from evidence.spec_rag import (  # noqa: F401  (Task B2 re-export)
    build_corpus,
    build_query,
    chunk_markdown,
    format_prompt_section,
    jaccard,
    load_default_corpus,
    retrieve,
    retrieve_for_prompt,
    tokenize,
)
from evidence.blame_ctx import (  # noqa: F401  (Task B3 re-export)
    blame_for_prompt,
    blame_line,
    blame_lines,
    format_prompt_section,
)
