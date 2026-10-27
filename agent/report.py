#!/usr/bin/env python3
"""Afterglow proof-bundle report builder (AGENTS.md sections 1 + 4).

``build_report(run_dir)`` reads ONLY the bundle files inside ``runs/<id>/``
and writes ``report.md``. Every number (checksums, cycles, latencies,
patch lines, time-to-green, retries) is derived from the bundle -- no
hand-typed scores, no invented claims. The diagnosis is quoted VERBATIM
from ``api/text_response.json`` and ``explain.png`` is embedded next to
the input hashes so a judge can verify provenance.
"""

from __future__ import annotations

import hashlib
import json
import os

BUNDLE_FILES = (
    "artifacts/input_trace.txt",
    "artifacts/boot.log",
    "artifacts/boot_after.log",
    "artifacts/verdict_before.json",
    "artifacts/verdict_after.json",
    "api/text_request.json",
    "api/text_response.json",
    "api/image_request.json",
    "api/image_response.json",
    "explain.png",
    "patch.diff",
    "meta.json",
)


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _sha_file(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return "MISSING"
    return digest.hexdigest()


def _verdict_line(v: dict) -> str:
    if not v:
        return "MISSING"
    return "tohost=%s checksum=%s cycles=%s seed=%s pass=%s" % (
        v.get("tohost"), v.get("checksum"),
        v.get("cycles"), v.get("seed"), v.get("pass"))


def build_report(run_dir: str) -> str:
    """Build report.md from bundle files only. Returns the report path."""
    run_dir = os.path.abspath(run_dir)
    meta = _read_json(os.path.join(run_dir, "meta.json"))
    vb = _read_json(os.path.join(run_dir, "artifacts/verdict_before.json"))
    va = _read_json(os.path.join(run_dir, "artifacts/verdict_after.json"))
    treq = _read_json(os.path.join(run_dir, "api/text_request.json"))
    tresp = _read_json(os.path.join(run_dir, "api/text_response.json"))
    ireq = _read_json(os.path.join(run_dir, "api/image_request.json"))
    iresp = _read_json(os.path.join(run_dir, "api/image_response.json"))
    patch = _read(os.path.join(run_dir, "patch.diff")) or ""
    trace_path = os.path.join(run_dir, "artifacts/input_trace.txt")
    trace_sha = _sha_file(trace_path)

    diagnosis = (
        tresp.get("diagnosis")
        or (tresp.get("parsed") or {}).get("diagnosis")
        or "MISSING (text call produced no diagnosis)"
    )
    expected = (
        tresp.get("expected")
        or (tresp.get("parsed") or {}).get("expected") or "-"
    )
    patch_lines = meta.get("patch_lines")
    if patch_lines is None:
        patch_lines = sum(
            1 for ln in patch.splitlines()
            if (ln.startswith("+") or ln.startswith("-"))
            and not ln.startswith("+++") and not ln.startswith("---")
        )

    status = str(meta.get("status", "red")).upper()
    present = []
    missing = []
    for rel in BUNDLE_FILES:
        (present if os.path.isfile(os.path.join(run_dir, rel))
         else missing).append(rel)

    lines = []
    lines.append("# Afterglow run report: %s"
                 % meta.get("run_id", os.path.basename(run_dir)))
    lines.append("")
    lines.append("case `%s` | status **%s** | mode `%s` | seed `%s`"
                 % (meta.get("case_id", meta.get("case", "?")), status,
                    meta.get("mode", "?"), meta.get("sim_seed", "?")))
    lines.append("")
    lines.append("wall start `%s` | wall end `%s` | time-to-green `%ss`"
                 % (meta.get("wall_start_utc", "?"),
                    meta.get("wall_end_utc", "?"),
                    meta.get("time_to_green_s", "?")))
    lines.append("")
    lines.append("retries `%s` | human_touches `%s` | diagnosed `%s` | "
                 "fixed_unaided `%s` | patch_source `%s`"
                 % (meta.get("retries", "?"), meta.get("human_touches", "?"),
                    meta.get("diagnosed", "?"),
                    meta.get("fixed_unaided", "?"),
                    meta.get("patch_source", "?")))
    lines.append("")
    lines.append("## Verdicts (from sim, not typed)")
    lines.append("")
    lines.append("- BEFORE: %s" % _verdict_line(vb))
    lines.append("- AFTER: %s" % _verdict_line(va))
    lines.append("")
    lines.append("## Diagnosis (verbatim from api/text_response.json)")
    lines.append("")
    lines.append("```")
    lines.append(str(diagnosis))
    lines.append("```")
    lines.append("")
    lines.append("expected re-sim signal: %s" % expected)
    lines.append("")
    lines.append("## Patch (%s lines, guard-checked)" % patch_lines)
    lines.append("")
    lines.append("```diff")
    lines.append(patch if patch else "(no patch applied)")
    lines.append("```")
    lines.append("")
    lines.append("## Provenance (judge checks)")
    lines.append("")
    lines.append("- prompt file `%s` sha256 `%s`"
                 % (meta.get("prompt_file", treq.get("prompt_file", "?")),
                    treq.get("prompt_sha256", meta.get("prompt_hash", "?"))))
    lines.append("- input_trace.txt sha256 `%s` (%s lines, %s tokens)"
                 % (trace_sha, treq.get("trace_lines", "?"),
                    treq.get("trace_tokens", "?")))
    lines.append("- prompt tokens `%s` (cap 2500)"
                 % treq.get("prompt_tokens", "?"))
    lines.append("- text model `%s` latency `%sms` request `%s`"
                 % (tresp.get("model", meta.get("model_id", "?")),
                    tresp.get("latency_ms",
                             meta.get("text_latency_ms", "?")),
                    tresp.get("request_id",
                             meta.get("text_request_id", "?"))))
    lines.append("- image model `%s` latency `%sms` request `%s` status `%s`"
                 % (iresp.get("model", meta.get("image_model", "?")),
                    iresp.get("latency_ms",
                             meta.get("image_latency_ms", "?")),
                    iresp.get("request_id",
                             meta.get("image_request_id", "?")),
                    meta.get("explain", "?")))
    lines.append("- image prompt sha256 `%s`, trace sha `%s` "
                 "(see api/image_request.json)"
                 % (ireq.get("prompt_sha256", "?"),
                    ireq.get("trace_sha256", "?")))
    lines.append("")
    lines.append("## Explanation diagram")
    lines.append("")
    if os.path.isfile(os.path.join(run_dir, "explain.png")):
        lines.append("![explain.png](explain.png)")
    else:
        lines.append("(explain.png MISSING -- image call failed, "
                     "see meta.explain=FAILED)")
    lines.append("")
    lines.append("## Bundle completeness")
    lines.append("")
    lines.append("- present (%d): %s" % (len(present), ", ".join(present)))
    lines.append("- missing (%d): %s"
                 % (len(missing), ", ".join(missing) if missing else "none"))
    lines.append("")
    out = os.path.join(run_dir, "report.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Build report.md from a bundle.")
    ap.add_argument("run_dir")
    a = ap.parse_args(argv)
    print(build_report(a.run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
