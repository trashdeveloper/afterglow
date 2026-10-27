"""Cone slicer + module graph over the pinned RTL tree (read-only).

Serves eval cases: from one failing signal (e.g. ``recv_divcnt``) the
slicer walks driver edges backwards and emits the ranked cone of RTL
lines with ``file:line`` provenance, capped at 200 lines.

- Foundation is REFERENCED, never copied: :func:`resolve_pinned_tree`
  reads ``targets.pin`` and returns the work-tree path. It never clones,
  never writes, never copies (CI fails on ``weft/`` copies).
- Parser is regex-based (stdlib only) and DOCUMENTS what it cannot
  parse: ``generate`` / macro / ``ifdef`` lines are counted in
  ``unparsed`` and reported, never silently skipped. An empty driver
  cone degrades to a lexical fallback (lines mentioning the seed) so
  the section is never empty.
- Port maps (``.port(sig)``) are treated as BIDIRECTIONAL edges: without
  port-direction analysis the conservative over-approx keeps the cone
  connected through instantiations (``adder_out <-> y``).
- Declaration initializers (``wire done = a & b;``) are driver edges:
  without them the 2nd BFS level through wire-defined signals (e.g.
  ``wire mem_done`` in picorv32.v) is empty.
- :func:`top1_localization` blames one ``file:line`` per cone, ranked
  by driver-signal frequency with trace-anchor proximity tie-break.

Cap: ``MAX_CONE_LINES = 200`` (parent spec). Depth default 3.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

MAX_CONE_LINES = 200
DEFAULT_DEPTH = 3

_ASSIGN_RE = re.compile(r"assign\s+(.*?)\s*=\s*(.*?);", re.DOTALL)
_NB_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(<=|=)\s*([^;]+);", re.MULTILINE)
_PORT_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([^)]*?)\)")
_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_INST_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\([^;]*?\)\s*)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DECL_INIT_RE = re.compile(
    r"\b(?:wire|reg|logic)\b\s*(?:\[[^\]]*\]\s*)?(?:signed\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+?);",
    re.DOTALL)
_SIZED_LITERAL_RE = re.compile(
    r"[0-9][0-9_]*'[sS]?[bBdDhHoO][0-9a-fA-F_xXzZ?]+")

_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg",
    "assign", "always", "initial", "begin", "end", "if", "else", "case",
    "endcase", "for", "while", "posedge", "negedge", "edge", "parameter",
    "localparam", "generate", "endgenerate", "genvar", "integer", "gen",
    "function", "endfunction", "task", "endtask", "logic", "bit",
}

_UNPARSED_RES = (
    re.compile(r"^\s*generate\b"),
    re.compile(r"^\s*endgenerate\b"),
    re.compile(r"^\s*genvar\b"),
    re.compile(r"^\s*`"),  # macro invocation / `ifdef / `define / `include
    re.compile(r"^\s*ifdef\b"),
    re.compile(r"^\s*ifndef\b"),
    re.compile(r"^\s*endif\b"),
)


def _is_unparsed_line(stripped: str) -> bool:
    return any(rx.search(stripped) for rx in _UNPARSED_RES)


def _idents(text: str) -> List[str]:
    # Sized literals (8'h00, 32'd0, ...) are constants, not signals:
    # strip them so fragments like ``h00`` never enter the cone.
    text = _SIZED_LITERAL_RE.sub(" ", text)
    return [t for t in _IDENT_RE.findall(text) if t not in _KEYWORDS]


def _strip_extra_declarators(rhs: str) -> str:
    """Cut trailing ``, other = ...`` declarators from a declaration RHS.

    ``wire a = x, b = y;`` declares two signals; the ``, b = y`` part is
    NOT a driver of ``a``. Commas nested inside ``{}``/``[]``/``()``
    (concatenations, slices, calls) are kept -- only a depth-0 comma
    whose remainder contains ``=`` truncates the RHS.
    """
    depth = 0
    for i, ch in enumerate(rhs):
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0 and "=" in rhs[i + 1:]:
            return rhs[:i]
    return rhs


def _read_lines(tree: str, rel: str) -> List[str]:
    path = os.path.join(tree, rel)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def resolve_pinned_tree(project: str = "picorv32", pin_path: str | None = None,
                        work_dir: str | None = None) -> str:
    """Resolve the pinned RTL work-tree path (READ-ONLY, never copies).

    Reads ``<repo>/targets.pin`` (``"<name> <url> <sha>"`` per line),
    computes ``$AF_WORK/<project>-<sha[:12]>`` (same convention as
    ``sim/vbackend.py:tree_dir``). Returns the path; raises
    FileNotFoundError when the pin or the tree is missing. NEVER clones,
    NEVER writes, NEVER copies foundation files.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pin = pin_path or os.path.join(here, "targets.pin")
    sha = ""
    with open(pin, "r", encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[0] == project:
                sha = parts[2]
                break
    if not sha:
        raise FileNotFoundError(
            "no pin for project %r in %s" % (project, pin))
    work = work_dir or os.environ.get("AF_WORK") or os.path.join(
        os.path.expanduser("~"), ".cache", "afterglow", "work")
    tree = os.path.join(work, "%s-%s" % (project, sha[:12]))
    if not os.path.isdir(tree):
        raise FileNotFoundError(
            "pinned tree missing (read-only, refusing to clone): %s" % tree)
    return tree


def list_rtl_files(tree: str,
                   exts: Tuple[str, ...] = (".v", ".sv")) -> List[str]:
    """Tree-relative RTL files, sorted, excluding verilator build dirs."""
    found = []
    for root, _dirs, files in os.walk(tree):
        if "obj_afterglow_" in root:
            continue
        for fn in sorted(files):
            if fn.endswith(exts):
                rel = os.path.relpath(os.path.join(root, fn), tree)
                if "obj_afterglow_" not in rel:
                    found.append(rel)
    return sorted(found)


def parse_drivers(tree: str, rel: str) -> Dict[str, List[str]]:
    """Signal -> driving signals for one file (regex, over-approx).

    Covers ``assign lhs = rhs;``, nonblocking/blocking targets
    (``q <= d;``), declaration initializers (``wire done = a & b;`` --
    the form ``wire mem_done = resetn && ...`` takes in picorv32.v) and
    bidirectional port-map edges. Generate/macro lines are skipped
    here and counted by :func:`unparsed_count`. Only single-declarator
    initializers are modeled (``wire a = x, b = y;`` keeps ``a``'s
    edge; later comma declarators are skipped). Returns plain dict of
    sorted lists (JSON-safe).
    """
    lines = _read_lines(tree, rel)
    text = "\n".join(lines)
    drivers: Dict[str, set] = {}

    def _add(target: str, rhss: List[str]) -> None:
        if not target or target in _KEYWORDS:
            return
        drivers.setdefault(target, set()).update(
            r for r in rhss if r != target and r not in _KEYWORDS)

    for m in _ASSIGN_RE.finditer(text):
        lhs_idents = _idents(m.group(1))
        if not lhs_idents:
            continue
        _add(lhs_idents[0], _idents(m.group(2)))
    for m in _NB_RE.finditer(text):
        target = m.group(1)
        if target in _KEYWORDS:
            continue
        _add(target, _idents(m.group(3)))
    for m in _DECL_INIT_RE.finditer(text):
        _add(m.group(1), _idents(_strip_extra_declarators(m.group(2))))
    for m in _PORT_RE.finditer(text):
        formal = m.group(1)
        actuals = _idents(m.group(2))
        for act in actuals:
            _add(formal, [act])  # port driven by actual
            _add(act, [formal])  # actual driven by port (bidir over-approx)
    return {k: sorted(v) for k, v in drivers.items()}


def unparsed_count(tree: str, rel: str) -> int:
    """Lines the regex parser cannot model (generate/macro/ifdef)."""
    n = 0
    for ln in _read_lines(tree, rel):
        if _is_unparsed_line(ln.strip()):
            n += 1
    return n


def build_module_graph(tree: str,
                       rtl_files: List[str] | None = None) -> Dict[str, List[str]]:
    """Module -> instantiated children (instantiation scan, stdlib).

    Two passes: first collect defined module names, then attribute
    ``<mod> <inst> (`` lines to the enclosing module. Unknown
    identifiers (keywords, primitive gates) are ignored.
    """
    files = list(rtl_files) if rtl_files is not None else list_rtl_files(tree)
    defined: Dict[str, str] = {}  # module -> file
    for rel in files:
        try:
            text = "\n".join(_read_lines(tree, rel))
        except OSError:
            continue
        for m in _MODULE_RE.finditer(text):
            defined.setdefault(m.group(1), rel)
    graph: Dict[str, set] = {mod: set() for mod in defined}
    for rel in files:
        try:
            text = "\n".join(_read_lines(tree, rel))
        except OSError:
            continue
        owners = [m.group(1) for m in _MODULE_RE.finditer(text)]
        owner = owners[0] if owners else os.path.splitext(
            os.path.basename(rel))[0]
        graph.setdefault(owner, set())
        for m in _INST_RE.finditer(text):
            mod, _inst = m.group(1), m.group(2)
            if mod in defined and mod != owner:
                graph[owner].add(mod)
    return {k: sorted(v) for k, v in graph.items()}


def rank_modules(graph: Dict[str, List[str]],
                 slice_files: List[str]) -> List[Tuple[str, float]]:
    """Rank modules by slice-file overlap (shared child never double-counts).

    Score 1.0 when the module name matches a slice-file stem (either
    direction of containment); 0.2 bonus when instantiated by a 1.0
    module; else 0.0. Tie-break alphabetical. Returns
    [(module, score)] desc.
    """
    stems = {os.path.splitext(os.path.basename(f))[0].lower()
             for f in (slice_files or [])}
    hits = set()
    for mod in graph:
        ml = mod.lower()
        for st in stems:
            if ml == st or ml in st or st in ml:
                hits.add(mod)
                break
    parents_hit = set()
    for parent, children in graph.items():
        if parent in hits:
            parents_hit.update(children)
    scored = []
    for mod in graph:
        if mod in hits:
            score = 1.0
        elif mod in parents_hit:
            score = 0.2
        else:
            score = 0.0
        scored.append((mod, score))
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored


def backward_slice(tree: str, seed: str,
                   rtl_files: List[str] | None = None,
                   max_depth: int = DEFAULT_DEPTH,
                   max_lines: int = MAX_CONE_LINES) -> Tuple[List[str], Dict]:
    """BFS driver cone for ``seed`` -> (lines, report).

    Lines are ``"rel:lineno: text"`` with file:line provenance, ordered
    by BFS depth (seed first) then file. Falls back to lexical lines
    mentioning the seed when no driver edges resolve, so the result is
    NEVER empty when the seed is mentioned anywhere. Report carries
    seed/resolved/sought/unparsed/lines/files for the pack provenance
    header, plus ``drivers`` (driver edges among seen signals) and
    ``seen`` (BFS signal order, for :func:`top1_localization`).
    """
    files = list(rtl_files) if rtl_files is not None else list_rtl_files(tree)
    drivers_by_file: Dict[str, Dict[str, List[str]]] = {}
    unparsed = 0
    for rel in files:
        try:
            drivers_by_file[rel] = parse_drivers(tree, rel)
            unparsed += unparsed_count(tree, rel)
        except OSError:
            drivers_by_file[rel] = {}
    merged: Dict[str, set] = {}
    for dm in drivers_by_file.values():
        for sig, ds in dm.items():
            merged.setdefault(sig, set()).update(ds)

    # BFS over driver edges (exact names + lowercase fallback for
    # trace-cased seeds: the trace lowercases, RTL keeps case).
    seen: Dict[str, int] = {seed: 0}
    queue: List[Tuple[str, int]] = [(seed, 0)]
    while queue:
        sig, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        cands = set(merged.get(sig, ()))
        if not cands:
            for k in merged:
                if k.lower() == sig.lower():
                    cands.update(merged[k])
        for nxt in sorted(cands):
            if nxt not in seen:
                seen[nxt] = depth + 1
                queue.append((nxt, depth + 1))

    # Line index: signal -> [(rel, lineno, text)] (whole-word).
    index: Dict[str, List[Tuple[str, int, str]]] = {}
    for rel in files:
        try:
            lines = _read_lines(tree, rel)
        except OSError:
            continue
        for i, ln in enumerate(lines):
            for sig in seen:
                if re.search(r"\b%s\b" % re.escape(sig), ln):
                    index.setdefault(sig, []).append((rel, i + 1, ln.strip()))
                    break

    ordered = sorted(seen, key=lambda s: (seen[s], s))
    out: List[str] = []
    emitted = set()
    for sig in ordered:
        for rel, lineno, text in index.get(sig, []):
            key = (rel, lineno)
            if key in emitted:
                continue
            emitted.add(key)
            out.append("%s:%d: %s" % (rel, lineno, text))
            if len(out) >= max_lines:
                break
        if len(out) >= max_lines:
            break

    # Lexical fallback: seed mentioned nowhere via drivers but present
    # as substring (e.g. bus slices) -- still ground the model.
    if not out:
        for rel in files:
            try:
                lines = _read_lines(tree, rel)
            except OSError:
                continue
            for i, ln in enumerate(lines):
                if seed.lower() in ln.lower():
                    out.append("%s:%d: %s" % (rel, i + 1, ln.strip()))
                    if len(out) >= max_lines:
                        break
            if len(out) >= max_lines:
                break

    files_touched = sorted({ln.split(":", 1)[0] for ln in out})
    resolved = sum(1 for s in seen if s in index)
    drivers = sum(len(merged[s]) for s in seen if s in merged)
    report = {"seed": seed, "resolved": resolved, "sought": len(seen),
              "unparsed": unparsed, "lines": len(out),
              "files": files_touched, "drivers": drivers,
              "seen": sorted(seen, key=lambda s: (seen[s], s))}
    return out[:max_lines], report


def top1_localization(cone_lines: List[str], trace_block: str,
                      driver_signals: List[str] | None = None) -> str:
    """Blame ``file:line`` ranked by driver frequency + trace proximity.

    Rank: each cone line scores the SUM of cone-wide mention frequencies
    of the distinct driver signals it names (whole-word) -- the line
    that shares vocabulary with the most cone lines wins. Tie-break:
    trace-anchor proximity -- the line whose signals appear EARLIEST in
    the failing anchor lines (``TRAP:``/``MISCLASS:``/``tohost:``, in
    order) wins. Final tie-break: file, then line number. Returns
    ``"rel:lineno"`` or ``""`` when the cone is empty (an abstention,
    counted as a miss by the localization metric -- never a guess).
    """
    if not cone_lines:
        return ""
    sigs = [s for s in (driver_signals or []) if s]
    freq: Dict[str, int] = {}
    line_sigs: List[List[str]] = []
    parsed: List[Tuple[str, int]] = []
    for ln in cone_lines:
        head = ln.split(":", 2)
        if len(head) >= 2:
            try:
                parsed.append((head[0], int(head[1])))
            except ValueError:
                parsed.append((head[0], 10 ** 9))
        else:
            parsed.append((ln, 10 ** 9))
        text = head[2] if len(head) >= 3 else ln
        present = []
        for sig in sigs:
            if re.search(r"\b%s\b" % re.escape(sig), text):
                if sig not in present:
                    present.append(sig)
        line_sigs.append(present)
        for sig in present:
            freq[sig] = freq.get(sig, 0) + 1
    # Trace-anchor appearance order (first anchor line first).
    order: Dict[str, int] = {}
    for raw in (trace_block or "").splitlines():
        stripped = raw.strip()
        if (stripped.startswith("TRAP:") or stripped.startswith("MISCLASS:")
                or stripped.startswith("tohost:")):
            for ident in _idents(stripped):
                if ident not in order:
                    order[ident] = len(order)
    missing = 10 ** 9

    def _key(i: int) -> Tuple[int, int, str, int]:
        present = line_sigs[i]
        score = sum(freq[s] for s in present)
        prox = min([order.get(s, missing) for s in present],
                   default=missing)
        fil, num = parsed[i]
        return (-score, prox, fil, num)

    best = min(range(len(cone_lines)), key=_key)
    fil, num = parsed[best]
    if num >= 10 ** 9:
        return ""
    return "%s:%d" % (fil, num)


FALLBACK_HALF = 30


def fallback_window(tree: str, rel: str, seed: str, half: int = FALLBACK_HALF,
                     max_lines: int = MAX_CONE_LINES) -> Tuple[List[str], Dict]:
    """Last-resort window around the first seed hit in one file.

    Used only when the static driver-cone slice is EMPTY (cone_lines 0):
    fall back to ``seed`` +/- ``half`` lines so the model still gets
    grounded file:line context instead of an empty CONE_SLICE. Whole-word
    match first, then plain substring. Returns (lines, report); the report
    carries ``source: "fallback-window"`` and ``center`` (1-based line of
    the hit, None when the seed is absent from the file).
    """
    report = {"seed": seed, "resolved": 0, "sought": 1, "unparsed": 0,
              "lines": 0, "files": [], "source": "fallback-window",
              "center": None}
    try:
        lines = _read_lines(tree, rel)
    except OSError:
        return [], report
    center = None
    try:
        whole = re.compile(r"\b%s\b" % re.escape(seed), re.IGNORECASE)
    except re.error:
        whole = None
    for i, ln in enumerate(lines):
        if whole is not None and whole.search(ln):
            center = i + 1
            break
    if center is None:
        lowered = (seed or "").lower()
        if lowered:
            for i, ln in enumerate(lines):
                if lowered in ln.lower():
                    center = i + 1
                    break
    if center is None:
        return [], report
    start = max(0, center - 1 - half)
    window = lines[start:center - 1 + half + 1]
    out = ["%s:%d: %s" % (rel, start + i + 1, text.strip())
           for i, text in enumerate(window)]
    out = out[:max_lines]
    report["lines"] = len(out)
    report["files"] = [rel]
    report["center"] = center
    return out, report
