#!/usr/bin/env python3
"""`afterglow explain` story renderer (dev-happiness track).

Reads a FINISHED proof bundle (``runs/<id>/``) and renders it as a
human story: RED symptom -> diagnosis (verbatim quote) -> patch ->
VERIFY checksums -> cost. Bundle-only, like agent/report.py: every
line comes from a bundle file, never re-invented. A file that is not
in the bundle renders as an honest ``MISSING (... not in bundle ...)``
marker, never guessed text.
"""

from __future__ import annotations

import json
import os

MISSING = "MISSING"


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


def _extract_diagnosis(tresp: dict) -> str | None:
    """Verbatim diagnosis from api/text_response.json shapes."""
    direct = tresp.get("diagnosis")
    if isinstance(direct, str) and direct.strip():
        return direct
    parsed = tresp.get("parsed")
    if isinstance(parsed, dict):
        diag = parsed.get("diagnosis")
        if isinstance(diag, str) and diag.strip():
            return diag
    for blob_key in ("content",):
        blob = tresp.get(blob_key)
        if isinstance(blob, str) and blob.strip():
            try:
                obj = json.loads(blob[blob.find("{"):blob.rfind("}") + 1])
            except (ValueError, IndexError):
                continue
            if isinstance(obj, dict):
                diag = obj.get("diagnosis")
                if isinstance(diag, str) and diag.strip():
                    return diag
    raw = tresp.get("raw")
    if isinstance(raw, dict):
        try:
            choices = raw.get("choices") or []
            msg = (choices[0].get("message") or {}) if choices else {}
            content = msg.get("content") or ""
            if isinstance(content, str) and content.strip():
                obj = json.loads(content[content.find("{"):
                                         content.rfind("}") + 1])
                if isinstance(obj, dict):
                    diag = obj.get("diagnosis")
                    if isinstance(diag, str) and diag.strip():
                        return diag
        except (ValueError, IndexError, AttributeError, KeyError):
            pass
    return None


def _red_symptom_lines(boot_log: str | None, verdict_before: dict) -> list:
    lines: list = []
    if boot_log is None:
        lines.append("%s (artifacts/boot.log not in bundle)" % MISSING)
    else:
        kept = [ln for ln in boot_log.splitlines()
                if ("TRAP:" in ln or "tohost:" in ln)][:4]
        if kept:
            lines.extend(kept)
        else:
            first = next((ln for ln in boot_log.splitlines() if ln.strip()),
                         "")
            lines.append(first or "%s (boot.log empty)" % MISSING)
    if verdict_before:
        lines.append("verdict_before: tohost=%s checksum=%s cycles=%s" % (
            verdict_before.get("tohost", MISSING),
            verdict_before.get("checksum", MISSING),
            verdict_before.get("cycles", MISSING)))
    else:
        lines.append("%s (artifacts/verdict_before.json not in bundle)"
                     % MISSING)
    return lines


def render(run_dir: str) -> str:
    """Render the human story for a finished bundle dir. Never raises."""
    run_dir = os.path.abspath(run_dir)
    meta = _read_json(os.path.join(run_dir, "meta.json"))
    vb = _read_json(os.path.join(run_dir, "artifacts/verdict_before.json"))
    va = _read_json(os.path.join(run_dir, "artifacts/verdict_after.json"))
    boot_log = _read(os.path.join(run_dir, "artifacts/boot.log"))
    tresp = _read_json(os.path.join(run_dir, "api/text_response.json"))
    patch = _read(os.path.join(run_dir, "patch.diff"))
    cost = _read_json(os.path.join(run_dir, "cost.json"))

    run_id = str(meta.get("run_id") or os.path.basename(run_dir))
    case = str(meta.get("case_id") or meta.get("case") or MISSING)
    status = str(meta.get("status", MISSING)).upper()

    out: list = []
    out.append("# Afterglow explain: %s" % run_id)
    out.append("")
    out.append("case `%s` | status **%s**" % (case, status))
    out.append("")
    out.append("## 1. RED symptom (from artifacts/boot.log)")
    out.append("")
    for ln in _red_symptom_lines(boot_log, vb):
        out.append("  " + ln)
    out.append("")
    out.append("## 2. Diagnosis (verbatim from api/text_response.json)")
    out.append("")
    diagnosis = _extract_diagnosis(tresp)
    if diagnosis:
        out.append('  "%s"' % diagnosis.strip())
    else:
        out.append("  %s (no diagnosis recorded in api/text_response.json "
                   "-- the run never diagnosed, nothing quoted)" % MISSING)
    out.append("")
    out.append("## 3. PATCH (from patch.diff)")
    out.append("")
    if patch is None:
        out.append("  %s (patch.diff not in bundle)" % MISSING)
    elif not patch.strip():
        out.append("  (no patch applied -- triage or RED before PATCH)")
    else:
        n = sum(1 for ln in patch.splitlines()
                if (ln.startswith("+") or ln.startswith("-"))
                and not ln.startswith("+++") and not ln.startswith("---"))
        out.append("  %d changed lines:" % n)
        out.append("  ```diff")
        for ln in patch.splitlines()[:40]:
            out.append("  " + ln)
        out.append("  ```")
    out.append("")
    out.append("## 4. VERIFY (checksums from verdict_after.json)")
    out.append("")
    if vb and va:
        before = vb.get("checksum", MISSING)
        after = va.get("checksum", MISSING)
        out.append("  BEFORE tohost=%s checksum=%s" % (
            vb.get("tohost", MISSING), before))
        out.append("  AFTER  tohost=%s checksum=%s" % (
            va.get("tohost", MISSING), after))
        if (isinstance(before, str) and isinstance(after, str)
                and before != MISSING and after != MISSING):
            out.append("  checksums %s" % (
                "MATCH -- fix verified by re-sim"
                if before != after and va.get("pass") else
                "MATCH (identical)" if before == after else
                "MISMATCH"))
        else:
            out.append("  checksums %s (verdicts incomplete)" % MISSING)
    else:
        out.append("  %s (verdict_before/after.json not in bundle)" % MISSING)
    out.append("")
    out.append("## 5. COST (from cost.json / meta.json)")
    out.append("")
    est = cost.get("est_usd", meta.get("est_usd", MISSING))
    ttg = meta.get("time_to_green_s", MISSING)
    out.append("  time-to-green: %ss | est. cost: $%s" % (ttg, est))
    out.append("")
    out.append("All lines above come from files in %s -- nothing invented."
               % run_dir)
    out.append("")
    return "\n".join(out)
