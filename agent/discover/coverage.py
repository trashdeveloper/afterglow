#!/usr/bin/env python3
"""Task A3: coverage-driven explorer (plan item 3, Wave A FIND BUGS).

Parses Verilator --coverage line data, ranks uncovered lines in DUT
files (excluding TB files), emits stimulus goals (file:line + reason)
for the top-N uncovered lines.

Interfaces:
  build_with_coverage(tree, case, build_fn=None) -> binary path
  parse_coverage_text(text, source="<text>") -> {(file, line): hits}
  parse_coverage_dat(path) -> {(file, line): hits}  (line-type only)
  rank_uncovered(line_hits, dut_files, tb_file=None, top_n=10)
    -> [goal, ...]  (goal = {file, line, hits, reason})
  explore(tree, case, top_n=10, seed=42, build_fn=None, run_fn=None,
          workdir=None) -> report dict with goals
  write_discovery_report(path, case_name, report) -> path

Honesty contract (mirrors the fuzzer/blindspot: never invent signal):
  - Missing coverage file raises FileNotFoundError.
  - Empty coverage data (empty file/text, header-only, no C lines, or
    no line-type entries) raises ValueError -- never parsed into an
    empty dict that downstream code could mistake for "fully covered".
  - Any malformed C line (bad count, missing f/l/t fields, bad line
    number) raises ValueError loudly -- never skipped silently.
  - rank_uncovered raises ValueError on empty input AND when nothing
    is uncovered (fully-covered design): returning zero goals silently
    would look identical to "no data", so the caller must see the
    distinction as an exception.
  - TB files are excluded by basename: an uncovered TB line never
    becomes a goal.
  - Toggle/expr entries are ignored: only t == "line" counts.
  - Build reuses sim/vbackend.py primitives (vbackend.build via
    build_fn seam) with "--coverage" appended to a COPIED vflags list
    (the caller's case dict is never mutated).
  - Any build/run error or empty sim log propagates loudly and is
    never converted into goals.

Coverage.dat format (Verilator 5.x, observed):
  # SystemC::Coverage-3
  C '<\\x01f\\x02<file>\\x01l\\x02<line>\\x01n\\x02...\\x01t\\x02line...>' <hits>
Fields are \\x01<key>\\x02<value> pairs; keys of interest are f (file
basename), l (line number), t (coverage type: line/toggle/expr). The
trailing integer is the hit count. One (file, line) may appear in
several C lines (e.g. if/else arms on the same line): the line hit
count is the MAX over its entries (covered if any arm executed).
"""

import os
import re
import sys
import tempfile

_DISCOVER_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_DISCOVER_DIR)
_ROOT = os.path.dirname(_AGENT_DIR)
sys.path.insert(0, os.path.join(_ROOT, "sim"))
sys.path.insert(0, _ROOT)

import vbackend  # noqa: E402

COVERAGE_FLAGS = ["--coverage"]
COVERAGE_DAT = "coverage.dat"
SIM_TIMEOUT_S = 300

_C_LINE_RE = re.compile(r"^C '(.*)' (\S+)\s*$")


def _parse_fields(blob, source, lineno):
    """Split a C-line field blob into {key: value}; raises on garbage."""
    kv = {}
    for chunk in blob.split("\x01"):
        if not chunk:
            continue
        if "\x02" not in chunk:
            raise ValueError(
                "malformed coverage field %r at %s line %d: "
                "refusing to guess" % (chunk, source, lineno))
        k, v = chunk.split("\x02", 1)
        kv[k] = v
    return kv


def parse_coverage_text(text, source="<text>"):
    """Parse Verilator coverage.dat text into {(basename, line): hits}.

    Only t == "line" entries are kept; toggle/expr entries are ignored.
    Raises ValueError on empty input, header-only input, input with no
    C lines, input with no line-type entries, or any malformed C line.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "empty coverage data from %s: no lines to parse -- refusing "
            "to return zero goals silently" % source)
    hits = {}
    n_c_lines = 0
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _C_LINE_RE.match(raw.strip())
        if not m:
            raise ValueError(
                "malformed coverage line at %s line %d: %r -- refusing "
                "to guess" % (source, idx, raw))
        blob, count_s = m.group(1), m.group(2)
        try:
            count = int(count_s)
        except ValueError:
            raise ValueError(
                "malformed coverage hit count at %s line %d: %r -- "
                "refusing to guess" % (source, idx, count_s))
        if count < 0:
            raise ValueError(
                "negative coverage hit count at %s line %d: %r"
                % (source, idx, count_s))
        kv = _parse_fields(blob, source, idx)
        for key in ("f", "l", "t"):
            if key not in kv or not str(kv[key]).strip():
                raise ValueError(
                    "coverage line at %s line %d is missing field %r "
                    "(%r) -- refusing to guess" % (source, idx, key, raw))
        try:
            lineno = int(str(kv["l"]).strip())
        except ValueError:
            raise ValueError(
                "coverage line number not an integer at %s line %d: %r"
                % (source, idx, kv["l"]))
        if lineno < 1:
            raise ValueError(
                "coverage line number out of range at %s line %d: %r"
                % (source, idx, kv["l"]))
        if kv["t"] != "line":
            continue  # toggle/expr entries carry no line-execution signal
        fname = os.path.basename(str(kv["f"]).strip())
        if not fname:
            raise ValueError(
                "coverage filename blank at %s line %d" % (source, idx))
        key = (fname, lineno)
        hits[key] = count if key not in hits else max(hits[key], count)
        n_c_lines += 1
    if n_c_lines == 0 and not hits:
        # Distinguish "no C lines at all" from "C lines but no line-type".
        total_c = sum(1 for raw in text.splitlines()
                      if raw.strip() and not raw.strip().startswith("#"))
        if total_c == 0:
            raise ValueError(
                "empty coverage data from %s: header-only, no C lines "
                "-- refusing to return zero goals silently" % source)
        raise ValueError(
            "no line-type coverage entries from %s (%d C lines, all "
            "toggle/expr) -- refusing to return zero goals silently"
            % (source, total_c))
    return hits


def parse_coverage_dat(path):
    """Read + parse a coverage.dat file. Missing file raises
    FileNotFoundError; empty/malformed content raises ValueError."""
    if not os.path.isfile(path):
        raise FileNotFoundError("missing coverage data file: %s" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return parse_coverage_text(text, source=str(path))


def _uncovered_sorted(line_hits, dut_files, tb_file=None):
    """Full sorted uncovered [(file_rel, line)] before top-N cut."""
    if not isinstance(line_hits, dict) or not line_hits:
        raise ValueError(
            "empty coverage line data: nothing to rank -- refusing to "
            "return zero goals silently")
    if not dut_files:
        raise ValueError("no DUT files given: cannot rank coverage")
    base_to_rel = {}
    for rel in dut_files:
        base = os.path.basename(str(rel))
        if base and base not in base_to_rel:
            base_to_rel[base] = str(rel)
    tb_base = os.path.basename(str(tb_file)) if tb_file else None
    uncovered = []
    for (fname, lineno), count in line_hits.items():
        if fname not in base_to_rel:
            continue  # not a DUT file (TB or unrelated source)
        if tb_base is not None and fname == tb_base:
            continue  # TB files never become goals, even if listed
        if count == 0:
            uncovered.append((base_to_rel[fname], lineno))
    uncovered.sort(key=lambda fl: (fl[0], fl[1]))
    return uncovered


def rank_uncovered(line_hits, dut_files, tb_file=None, top_n=10):
    """Rank uncovered DUT lines, emit top-N stimulus goals.

    Each goal: {file, line, hits (0), reason}. Sorted by (file, line).
    Raises ValueError when there is nothing to rank (empty input) or
    when nothing is uncovered (fully covered) -- never returns [].
    """
    try:
        top_n = int(top_n)
    except (TypeError, ValueError):
        raise ValueError("top_n must be an integer, got %r" % (top_n,))
    if top_n < 1:
        raise ValueError("top_n must be >= 1, got %r" % (top_n,))
    uncovered = _uncovered_sorted(line_hits, dut_files, tb_file=tb_file)
    if not uncovered:
        raise ValueError(
            "no uncovered DUT lines: design fully covered under this "
            "stimulus -- refusing to return zero goals silently (this "
            "is a coverage success, not an explorer finding)")
    goals = []
    for rel, lineno in uncovered[:top_n]:
        goals.append({
            "file": rel,
            "line": int(lineno),
            "hits": 0,
            "reason": ("line %d in %s never executed (0 hits in "
                       "coverage line data); add stimulus exercising "
                       "this line (branch/case arm or gated block) and "
                       "re-run with --coverage to confirm" % (lineno, rel)),
        })
    return goals


def build_with_coverage(tree, case, build_fn=None):
    """Build the sim with Verilator --coverage via vbackend primitives.

    Copies the case dict (never mutates the caller's) and appends
    "--coverage" to vflags, then delegates to build_fn or
    vbackend.build. Returns the sim binary path; build errors raise.
    """
    if isinstance(case, str):
        case = vbackend.load_case(case)
    try:
        vflags = list(case.get("vflags", []) or [])
    except AttributeError:
        raise ValueError("case must be a dict, got %r" % (case,))
    cov_case = dict(case)
    cov_case["vflags"] = vflags
    for flag in COVERAGE_FLAGS:
        if flag not in vflags:
            vflags.append(flag)
    build = build_fn or vbackend.build
    return build(tree, cov_case)


def _default_run(binary, seed, outdir):
    """Run the coverage-built binary in outdir (coverage.dat lands
    there). Exit code is ignored (oracle marker decides, per the
    fuzzer/blindspot contract); timeouts raise."""
    rc, log = vbackend.sh([str(binary), "+seed=%d" % int(seed)],
                          cwd=str(outdir), timeout=SIM_TIMEOUT_S)
    del rc
    return log


def explore(tree, case, top_n=10, seed=42, build_fn=None, run_fn=None,
            workdir=None):
    """Full pipeline: coverage build -> run -> parse -> rank goals.

    tree: RTL tree path. case: vbackend case dict (or case-name string).
    top_n: number of stimulus goals. seed: sim +seed value.
    build_fn(tree, case) -> binary (default: coverage build via
      vbackend.build). run_fn(binary, seed, outdir) -> sim log text
      (default: run the binary in outdir via vbackend.sh; the fake
      run_fn in tests writes outdir/coverage.dat itself).
    workdir: run dir receiving coverage.dat (temp dir when omitted).

    Returns {"case", "tb", "seed", "top_n", "n_uncovered", "goals",
      "coverage_dat"}. Every failure mode raises loudly; zero-goal
    results are never returned silently.
    """
    if isinstance(case, str):
        case = vbackend.load_case(case)
    try:
        dut_files = list(case["files"])
    except (KeyError, TypeError):
        raise KeyError("case is missing required key 'files'")
    if not dut_files:
        raise ValueError("case files list is empty; nothing to cover")
    tb_file = case.get("tb")
    root = os.path.abspath(workdir) if workdir \
        else tempfile.mkdtemp(prefix="coverage-")
    os.makedirs(root, exist_ok=True)
    binary = build_with_coverage(tree, case, build_fn=build_fn)
    run = run_fn or _default_run
    log = run(binary, int(seed), root)
    if not isinstance(log, str):
        raise TypeError("run_fn must return sim log text, got %r"
                        % type(log))
    if not log.strip():
        raise RuntimeError(
            "sim produced no log output: cannot judge coverage without "
            "a run -- refusing to invent goals")
    cov_path = os.path.join(root, COVERAGE_DAT)
    line_hits = parse_coverage_dat(cov_path)  # raises if missing/empty
    full = _uncovered_sorted(line_hits, dut_files, tb_file=tb_file)
    goals = rank_uncovered(line_hits, dut_files, tb_file=tb_file,
                           top_n=top_n)
    return {
        "case": case.get("name", "?"),
        "tb": tb_file,
        "seed": int(seed),
        "top_n": int(top_n),
        "n_uncovered": len(full),
        "goals": goals,
        "coverage_dat": cov_path,
    }


def write_discovery_report(path, case_name, report):
    """Write eval/discovery_coverage.md-style report: counts + goals.

    Discovery reports NEVER carry verdicts; they list stimulus goals.
    A zero-goal report is refused (rank/explore never produce one).
    """
    goals = report.get("goals", [])
    if not goals:
        raise ValueError(
            "refusing to write an empty coverage report: zero goals "
            "would fake signal from nothing")
    lines = [
        "# Discovery: coverage-driven explorer (Task A3)",
        "",
        "- case: %s" % case_name,
        "- seed: %s" % report.get("seed", "?"),
        "- uncovered DUT lines: %s" % report.get("n_uncovered", len(goals)),
        "- goals emitted: %d" % len(goals),
        "",
        "Top uncovered DUT lines (file:line + reason):",
    ]
    for g in goals:
        lines.append("- %s:%s -- %s" % (g.get("file"), g.get("line"),
                                        g.get("reason", "")))
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
