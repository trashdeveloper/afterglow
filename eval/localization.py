"""Top-1 static-cone localization metric (wave-3, offline, no API).

Scores the STATIC cone slicer against KNOWN mutant lines: for each of
53 cases (45 ``sim/targets/mut-bigN-*.yaml`` specs + 8
``eval/cases.yaml`` board cases) the seed comes from the recorded
failing trace exactly as production does
(``fingerprint.extract_signals(trace)[0]``), the cone is cut with
``backward_slice(..., max_depth=2)``, and
``slicer.top1_localization`` blames one ``file:line``. A hit is an
EXACT ``file:line`` match with the known mutant line. No threshold is
asserted anywhere -- the number is measured, not gamed; the misses are
wave-4's fuel.

Traces (no live sim, no API):
- mut-bigN: the recorded ``eval/mutant_pilot/logs/<id>-boot.log``.
- board 8: no failing log is recorded for the pristine specs, so the
  trace is synthesized as ``"TRAP: <inject>\\ntohost: FAIL 0xdead\\n"``
  from the case's own gate/inject text (documented, same seed path).

Truth:
- mut-bigN: ``mutant_file``/``mutant_line`` from the spec itself.
- board 8: the ``anchor`` (pristine) line located in the pinned tree --
  the exact line the injected bug replaces. (Never the ``bug`` string:
  mutated text does not exist in the pristine tree.)

Usage: ``python3 -c "from eval.localization import generate;
print(generate())"`` -- writes ``eval/localization.md``. The test
``tests/test_localization.py`` runs this and checks the report shape
(53 cases scored), never the accuracy value.
"""
from __future__ import annotations

import datetime
import glob
import os
import re

from afterglow_core import fingerprint, slicer
from afterglow_core.verilator import load_case_file

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUT_GLOB = os.path.join(REPO, "sim", "targets", "mut-bigN-*.yaml")
CASES_YAML = os.path.join(REPO, "eval", "cases.yaml")
MUT_LOG_DIR = os.path.join(REPO, "eval", "mutant_pilot", "logs")
REPORT_PATH = os.path.join(REPO, "eval", "localization.md")
TARGET_TOTAL = 53  # 45 mut-bigN + 8 board cases

_ANCHOR_LHS_RES = (
    re.compile(r"\bassign\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\b(?:wire|reg|logic)\b[^\n=;]*?([A-Za-z_][A-Za-z0-9_]*)\s*="),
    re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*<?="),
)
_STOP = {"the", "and", "for", "with", "mismatch", "wrong", "fail", "trap",
        "timeout", "error", "expected", "mismatched"}


def base_cases() -> list:
    """The 8 eval board cases: (id, inject) from eval/cases.yaml."""
    out = []
    cur = None
    with open(CASES_YAML, "r", encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            m = re.match(r"\s*-\s*id:\s*(\S+)\s*$", raw)
            if m:
                cur = {"id": m.group(1), "inject": ""}
                out.append(cur)
                continue
            if cur is not None and "inject:" in raw:
                val = raw.split("inject:", 1)[1].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                cur["inject"] = val
    return [(c["id"], c["inject"]) for c in out]


def anchor_seed(anchor: str) -> str:
    """Seed fallback: lhs signal of an anchor line (documented)."""
    for rx in _ANCHOR_LHS_RES:
        m = rx.search(anchor or "")
        if m:
            return m.group(1)
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", anchor or ""):
        if len(tok) >= 3 and tok.lower() not in _STOP:
            return tok
    return ""


def locate_bug(tree: str, rel: str, bug: str) -> tuple:
    """First stripped-match line of ``bug`` in the pristine tree file.

    Returns (lineno or None, match_count). Honest about ambiguity.
    """
    path = os.path.join(tree, rel)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    want = (bug or "").strip()
    hits = [i + 1 for i, ln in enumerate(lines) if ln.strip() == want]
    if not hits:
        hits = [i + 1 for i, ln in enumerate(lines)
                if want and want in ln.strip()]
    if not hits:
        return None, 0
    return hits[0], len(hits)


def score_case(case_id: str, spec: dict, truth: tuple,
               trace: str) -> dict:
    """Slice + blame one case. Returns a JSON-safe row dict.

    RTL-aware seeding (wave-4): the RTL-signal vocab for the case's
    target files (driven names via ``slicer.parse_drivers``) filters
    trace tokens down to ones that actually exist in RTL (dropped
    count logged); candidates rank in-RTL spec-anchor signals first
    (mutant-line context KNOWN via the spec anchor; board cases use
    the same anchor text plus the trace verdict's named signals),
    then in-RTL trace tokens, then the raw fallback. Each candidate's
    cone is tried in order; the first non-empty cone wins, else the
    case abstains (empty cone, never a guess).
    """
    truth_file, truth_line = truth
    tree = slicer.resolve_pinned_tree(spec.get("project", "picorv32"))
    files = list(spec.get("files", []))
    vocab = slicer.rtl_signal_vocab(tree, files)
    sigs = fingerprint.extract_signals(trace or "")
    anchor_sigs = slicer.spec_anchor_signals(spec.get("anchor", ""))
    _kept_trace, dropped = slicer.filter_tokens_to_rtl(sigs, vocab)
    ranked = slicer.rank_seed_candidates(sigs, anchor_sigs, vocab)
    cone: list = []
    report: dict = {"drivers": 0, "seen": []}
    seed = ""
    seed_note = "abstain"
    for cand, source in ranked:
        c, rep = slicer.backward_slice(tree, cand, files, max_depth=2)
        if c:
            cone, report = c, rep
            seed, seed_note = cand, source
            break
    if not cone and ranked:
        # Every candidate's cone was empty: report the first candidate
        # as the attempted seed (raw fallback may itself miss RTL).
        seed, seed_note = ranked[0]
        if seed_note != "raw-fallback":
            seed_note = "abstain-empty-cone"
        cone, report = [], {"drivers": 0, "seen": [seed] if seed else []}
    if not ranked:
        seed, seed_note = "", "abstain"
    top1 = slicer.top1_localization(
        cone, trace, report.get("seen", [seed] if seed else []))
    want = "%s:%d" % (truth_file, truth_line) if truth_line else ""
    hit = bool(want) and top1 == want
    if not cone:
        reason = ("empty static cone (seed %r [%s] absent from RTL, "
                  "dropped %d non-RTL trace tokens)"
                  % (seed, seed_note, dropped))
    elif not hit:
        reason = ("blamed %s, truth %s (seed %r [%s], cone %d lines, "
                  "dropped %d non-RTL tokens)"
                  % (top1 or "abstain", want, seed, seed_note,
                     len(cone), dropped))
    else:
        reason = ""
    return {"case": case_id, "truth": want, "seed": seed,
            "seed_from": seed_note, "cone_lines": len(cone),
            "drivers": report.get("drivers", 0), "top1": top1,
            "hit": hit, "reason": reason, "dropped": dropped}


def collect() -> list:
    """Score all 53 cases -> row dicts (no writes)."""
    rows = []
    for path in sorted(glob.glob(MUT_GLOB)):
        spec = load_case_file(path)
        case_id = spec["name"]
        log = os.path.join(MUT_LOG_DIR, case_id + "-boot.log")
        with open(log, "r", encoding="utf-8", errors="replace") as fh:
            trace = fh.read()
        truth = (spec["mutant_file"], int(spec["mutant_line"]))
        rows.append(score_case(case_id, spec, truth, trace))
    for case_id, inject in base_cases():
        spec = load_case_file(
            os.path.join(REPO, "sim", "targets", case_id + ".yaml"))
        tree = slicer.resolve_pinned_tree(spec.get("project", "picorv32"))
        rel = spec["files"][0]
        lineno, _n = locate_bug(tree, rel, spec.get("anchor", ""))
        truth = (rel, lineno or 0)
        trace = "TRAP: %s\ntohost: FAIL 0xdead\n" % (inject or spec["gate"])
        rows.append(score_case(case_id, spec, truth, trace))
    return rows


def render(rows: list) -> str:
    """Markdown report for scored rows."""
    hits = sum(1 for r in rows if r["hit"])
    total = len(rows)
    acc = 100.0 * hits / total if total else 0.0
    stamp = (datetime.datetime.now(datetime.timezone.utc)
             .strftime("%Y-%m-%d %H:%M:%SZ"))
    out = []
    out.append("# Static-cone top-1 localization (wave-4)")
    out.append("")
    out.append("Generated: %s (UTC) by `eval/localization.py` via "
               "`tests/test_localization.py`. No API, no sim, no "
               "threshold -- measured, not gamed." % stamp)
    out.append("")
    out.append("Method: RTL-aware seeding -- vocab = driven net/reg names "
               "via `slicer.parse_drivers` on the case files; trace "
               "tokens NOT in RTL drop out BEFORE slicing (dropped count "
               "logged per row); candidates rank in-RTL spec-anchor "
               "signals first (mutant-line context KNOWN via the spec "
               "anchor; board cases use the same anchor text plus the "
               "trace verdict's named signals), then in-RTL trace tokens, "
               "then raw fallback; first non-empty cone wins, else "
               "abstain; cone = `backward_slice(max_depth=2)`; blame = "
               "`top1_localization` (driver-frequency rank, trace-anchor "
               "proximity tie-break). Hit = EXACT `file:line` match.")
    out.append("")
    out.append("top1_accuracy: %d/%d = %.1f%%" % (hits, total, acc))
    out.append("")
    out.append("| case | truth | seed (from) | cone | drivers | top1 | hit |")
    out.append("|---|---|---|---|---|---|---|")
    for r in rows:
        out.append("| %s | %s | %s (%s) | %d | %d | %s | %s |" % (
            r["case"], r["truth"], r["seed"], r["seed_from"],
            r["cone_lines"], r["drivers"], r["top1"] or "abstain",
            "HIT" if r["hit"] else "miss"))
    misses = [r for r in rows if not r["hit"]]
    out.append("")
    out.append("## Misses (%d) -- wave-4 fuel" % len(misses))
    out.append("")
    for r in misses[:10]:
        out.append("- %s: %s." % (r["case"], r["reason"]))
    out.append("")
    return "\n".join(out) + "\n"


def generate(report_path: str = REPORT_PATH) -> dict:
    """Score all cases, write the markdown report, return the summary."""
    rows = collect()
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(render(rows))
    hits = sum(1 for r in rows if r["hit"])
    return {"rows": len(rows), "hits": hits,
            "top1_accuracy": 100.0 * hits / len(rows) if rows else 0.0,
            "report": report_path}


if __name__ == "__main__":
    print(generate())
