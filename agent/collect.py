"""Afterglow failure-trace collector (AGENTS.md section 3, anti-dump rule).

Serves eval cases: all 8 — every model prompt is built by :func:`collect`
so the trace window and token caps are enforced in one place and the
exact counts land in the proof bundle (``artifacts/input_trace.txt`` +
``api/text_request.json``).

Contract:

* input: full ``boot.log`` text, ``verdict.json`` text, prompt template text.
* The 200-line window is centered on the LAST anchor line matching
  ``MISCLASS`` | ``TRAP:`` | ``tohost:``. The window is positioned so the
  trap/``tohost`` tail is always included (window end extends past the
  last ``TRAP:``/``tohost:`` line).
* total prompt ``<= 2500`` tokens (whitespace-split estimate). Both caps
  are enforced with ``assert``: oversized input fails loudly, never
  silently truncates.
* returns ``{"trace_block", "full_prompt", "counts"}``; ``counts`` holds
  ``boot_lines / window_start / trace_lines / trace_tokens /
  verdict_tokens / prompt_tokens`` for logging.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Tuple

MAX_TRACE_LINES = 200
MAX_PROMPT_TOKENS = 2500

ANCHOR_RE = re.compile(r"MISCLASS|TRAP:|tohost:")
TAIL_RE = re.compile(r"TRAP:|tohost:")


def approx_tokens(text: str) -> int:
    """Whitespace-split token estimate (documented, deterministic)."""
    return len(text.split())


def sha256_file(path: str) -> str:
    """Hex sha256 of a file (used for prompt/patch/proof hashes)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _window(lines: List[str]) -> Tuple[List[str], int]:
    """Return (window_lines, window_start) per the centering contract."""
    n = len(lines)
    if n == 0:
        return [], 0
    if n <= MAX_TRACE_LINES:
        return list(lines), 0
    anchors = [i for i, l in enumerate(lines) if ANCHOR_RE.search(l)]
    anchor = anchors[-1] if anchors else n - 1
    tails = [i for i, l in enumerate(lines) if TAIL_RE.search(l)]
    tail = tails[-1] if tails else anchor
    # End past the further of (anchor centered) and (tail included), so
    # both the anchor context and the trap/tohost tail are always inside.
    end = min(n, max(anchor + MAX_TRACE_LINES // 2, tail + 1))
    start = max(0, end - MAX_TRACE_LINES)
    return lines[start:end], start


def collect(boot_log: str, verdict_json: str, prompt_text: str) -> Dict:
    """Build the capped model prompt from raw sim outputs.

    ``prompt_text`` is a versioned template (``agent/prompts/vX.txt``);
    ``{{TRACE}}``/``{{VERDICT}}`` are substituted when present, otherwise
    the trace and verdict are appended under clear headers.
    """
    lines = boot_log.splitlines()
    window, start = _window(lines)
    trace_block = "\n".join(window)
    if "{{TRACE}}" in prompt_text or "{{VERDICT}}" in prompt_text:
        full_prompt = prompt_text.replace("{{TRACE}}", trace_block).replace(
            "{{VERDICT}}", verdict_json
        )
    else:
        full_prompt = (
            prompt_text.rstrip()
            + "\n\nTRACE:\n"
            + trace_block
            + "\n\nVERDICT:\n"
            + verdict_json
            + "\n"
        )
    counts = {
        "boot_lines": len(lines),
        "window_start": start,
        "trace_lines": len(window),
        "trace_tokens": approx_tokens(trace_block),
        "verdict_tokens": approx_tokens(verdict_json),
        "prompt_tokens": approx_tokens(full_prompt),
    }
    assert counts["trace_lines"] <= MAX_TRACE_LINES, (
        f"trace window {counts['trace_lines']} lines exceeds cap {MAX_TRACE_LINES}"
    )
    assert counts["prompt_tokens"] <= MAX_PROMPT_TOKENS, (
        f"prompt {counts['prompt_tokens']} tokens exceeds cap {MAX_PROMPT_TOKENS}"
    )
    return {"trace_block": trace_block, "full_prompt": full_prompt, "counts": counts}
