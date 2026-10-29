#!/usr/bin/env python3
"""Task A7: spec-vs-RTL differ (plan item 7, Wave A FIND BUGS).

Takes a small spec excerpt (a list of documented behaviors, each with
an ID + keywords/signals) and checks each behavior has a TB path: the
TB + DUT sources are searched for the named signals/behaviors and each
behavior is reported covered (with file:line evidence) or uncovered.

Interfaces:
  check_spec(behaviors, sources) -> report
  check_spec_files(behaviors, paths) -> report
  write_discovery_report(path, case_name, report) -> path

Honesty contract (mirrors fuzzer/blindspot/seeddiff: never invent signal):
  - A behavior with zero hits is reported uncovered, never silently
    dropped: every input ID appears in the report exactly once.
  - Malformed spec entries (missing/blank id, missing/empty signals
    list, blank/non-string signal, non-dict entry, duplicate ids)
    raise ValueError loudly -- never skipped.
  - An empty spec raises ValueError: zero behaviors is not agreement,
    so a vacuous all-covered report is impossible.
  - Retrieval-only: no model calls, no API. Matching is literal
    case-sensitive substring search over the provided source text, and
    every covered claim cites file:line hits a human can re-grep.
  - Discovery reports NEVER carry verdicts.
"""

import os


def _validate_entry(entry):
    """Validate one spec behavior; return (id, signals). Loud ValueError."""
    if not isinstance(entry, dict):
        raise ValueError("spec entry must be a dict with 'id' + "
                         "'signals', got %r" % (entry,))
    bid = entry.get("id")
    if not isinstance(bid, str) or not bid.strip():
        raise ValueError("spec entry needs a non-blank string 'id', "
                         "got %r" % (entry,))
    signals = entry.get("signals")
    if not isinstance(signals, list) or not signals:
        raise ValueError("spec entry %r needs a non-empty 'signals' "
                         "list, got %r" % (bid, signals))
    for sig in signals:
        if not isinstance(sig, str) or not sig.strip():
            raise ValueError("spec entry %r has a blank/non-string "
                             "signal, got %r" % (bid, sig))
    return bid.strip(), [s.strip() for s in signals]


def check_spec(behaviors, sources):
    """Check each spec behavior against TB+DUT source text.

    behaviors: non-empty list of {"id", "description"?, "signals": [...]}.
    sources: dict {filepath: text} of TB + DUT sources to search.

    Returns {"total", "covered", "uncovered", "all_covered", "results"}
    where each result is {"id", "description", "status", "hits", "matched"}:
    status is "covered" (>= 1 substring hit) or "uncovered" (zero hits);
    hits are "filepath:lineno" strings; matched names the signals that hit.
    """
    if not isinstance(behaviors, list) or not behaviors:
        raise ValueError("spec excerpt must be a non-empty list of "
                         "behaviors, got %r: refusing a vacuous "
                         "all-covered report" % (behaviors,))
    if not isinstance(sources, dict) or not sources:
        raise ValueError("sources must be a non-empty {filepath: text} "
                         "dict, got %r" % (sources,))
    for path, text in sources.items():
        if not isinstance(text, str):
            raise ValueError("source %r must be text, got %r"
                             % (path, type(text)))
    seen = set()
    results = []
    for entry in behaviors:
        bid, signals = _validate_entry(entry)
        if bid in seen:
            raise ValueError("duplicate spec id %r: refusing to judge "
                             "the same behavior twice" % (bid,))
        seen.add(bid)
        hits = []
        matched = []
        for sig in signals:
            sig_hits = []
            for path, text in sources.items():
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if sig in line:
                        sig_hits.append("%s:%d" % (path, lineno))
            if sig_hits:
                matched.append(sig)
                hits.extend(sig_hits)
        hits.sort()
        results.append({
            "id": bid,
            "description": entry.get("description", ""),
            "signals": signals,
            "status": "covered" if hits else "uncovered",
            "hits": hits,
            "matched": matched,
        })
    covered = sum(1 for r in results if r["status"] == "covered")
    return {
        "total": len(results),
        "covered": covered,
        "uncovered": len(results) - covered,
        "all_covered": covered == len(results),
        "results": results,
    }


def check_spec_files(behaviors, paths):
    """Load TB+DUT files from disk and check_spec against them.

    paths: non-empty list of file paths. Missing/unreadable files raise
    loudly -- a behavior is never judged against sources that failed
    to load.
    """
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list of source "
                         "files, got %r" % (paths,))
    sources = {}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            sources[str(path)] = fh.read()
    return check_spec(behaviors, sources)


def write_discovery_report(path, case_name, report):
    """Write eval/discovery_specdiff.md-style report: covered/uncovered
    split with file:line evidence. Discovery reports NEVER carry verdicts."""
    lines = [
        "# Discovery: spec-vs-RTL differ (Task A7)",
        "",
        "- case: %s" % case_name,
        "- behaviors: %d/%d covered" % (report.get("covered", 0),
                                       report.get("total", 0)),
        "- all_covered: %s" % ("YES" if report.get("all_covered") else "NO"),
        "",
    ]
    for res in report.get("results", []):
        if res.get("status") == "covered":
            lines.append("- COVERED %s (%s): %s"
                         % (res.get("id"), ", ".join(res.get("matched", [])),
                            "; ".join(res.get("hits", []))))
        else:
            lines.append("- UNCOVERED %s: no TB/DUT path mentions %s"
                         % (res.get("id"),
                            ", ".join(res.get("signals", []))
                            or "(no signals listed)"))
    lines.append("")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
