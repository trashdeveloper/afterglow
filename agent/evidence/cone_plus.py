#!/usr/bin/env python3
"""Task B5: Cone-plus (plan item 15, Wave B SEE MORE).

EvidencePack extension: extends the wave-3 static slicer
(afterglow_core/slicer.py) across ONE module boundary following fan-in.
Given a fault signal in module M, when its within-module driver cone
bottoms out at a module input port, resolve that port's connection at
the instantiation site in the parent module and continue the cone one
level up.

Interfaces:
  extended_cone(tree, seed, rtl_files, max_depth, max_lines)
    -> (lines, report)
  format_prompt_section(lines, report, max_chars) -> str (cited, "" if none)
  cone_for_prompt(tree, seed, rtl_files, enable_cone_plus, ...)
    -> EvidencePack section dict

Reuse contract (no duplicated slicing logic): the within-module cone,
per-file driver maps, module graph, file listing, identifier splitting,
and depth/line caps ALL come from the wave-3 slicer (imported, never
reimplemented). This module adds ONLY what the slicer lacks: ANSI /
non-ANSI port-direction parsing, instantiation-site port-map
extraction, and the one-hop merge. Driver-edge semantics (assign /
nonblocking / declaration-initializer / port over-approx) stay the
slicer's -- a cone-plus line means exactly what a slicer line means.

Boundary algorithm:
  1. base = slicer.backward_slice(tree, seed, rtl_files) (the whole
     base result is reused verbatim).
  2. home = the file whose own parse_drivers defines seed (exact name,
     else case-insensitive fallback mirroring the slicer). No home
     (lexical-fallback-only seed) -> base returned unchanged.
  3. frontier = base seen-signals that are INPUT ports of the home
     module yet have no driver edge inside the home file's own parse
     (the cone bottomed out at the port within M).
  4. For each frontier port (base BFS order): find the home module's
     instantiation in another tree file, read the actual expression
     wired to that formal, take its first signal
     (slicer.spec_anchor_signals, appearance order), and run
     slicer.backward_slice for that parent signal restricted to the
     parent file (one level up, same reuse).
  5. Merge parent lines (dedupe by file:line, hard cap max_lines).

Default-off contract (Wave B header: every addition is additive +
default-off): cone_for_prompt with enable_cone_plus=False (the
default) returns {"cone_plus_enabled": False, ...} WITHOUT reading
any file or touching the slicer. The loop (agent/loop.py) is NOT
edited by this task; prompt builders opt in by passing
enable_cone_plus=True with a caller-built seed. Single-module trees
(no instantiation of the home module elsewhere) return the base
slicer lines byte-identical (same list object).

Honesty contract (mirrors slicer.py: never invent drivers):
- Depth cap = 1 boundary crossing. When the parent-level cone itself
  bottoms out at a parent input port that IS instantiated higher up,
  report truncated=True and emit NO grandparent lines (stop, don't
  chase). Deeper hierarchies are flagged, never walked.
- An unresolvable connection (port unmapped at the site, actual is a
  constant with no signals) is skipped loudly: the hop is not
  recorded, the base cone stands.
- A parent file that cannot be read contributes nothing (its hop is
  dropped), never a guessed line.
- format_prompt_section([]) is "" -- no cone, no section, no citation.

Pure stdlib (os, re) + afterglow_core.slicer; no API, no keys, no network.
"""
import os
import re

from afterglow_core import slicer

_MOD_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)")
_DIR_DECL_RE = re.compile(r"^\s*(input|output|inout)\b([^;]*);",
                           re.MULTILINE)
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TYPE_KW_RE = re.compile(
    r"^(?:reg|wire|logic|signed|int|integer|bit)\b\s*")
_INST_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\([^;]*?\)\s*)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
_PORT_PAIR_RE = re.compile(
    r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([^)]*?)\)")
_SKIP_OWNER = frozenset({
    "module", "assign", "always", "initial", "if", "else", "for",
    "case", "while", "generate",
})


def _balanced_inner(text, open_i):
    """Inner text of the paren group opened at open_i (None if unbalanced)."""
    depth = 0
    for i in range(open_i, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_i + 1:i]
    return None


def _clean_names(chunk):
    """Identifier names in a port-declaration chunk (types/brackets out)."""
    chunk = _BRACKET_RE.sub(" ", chunk)
    names = []
    for tok in _IDENT_RE.findall(chunk):
        if tok in ("input", "output", "inout", "reg", "wire",
                   "logic", "signed", "int", "integer", "bit"):
            continue
        if tok not in names:
            names.append(tok)
    return names


def parse_module_ports(text):
    """Module -> {"inputs": [...], "outputs": [...]} (appearance order).

    Covers ANSI headers (``module m(input clk, output [7:0] y)``,
    direction carried across comma segments, ``#(parameter)`` skipped)
    and non-ANSI bodies (``input a;`` declarations). ``inout``
    counts as an output edge (conservative, like the slicer's
    bidirectional port over-approx).
    """
    ports = {}

    def _add(mod, direction, names):
        entry = ports.setdefault(mod, {"inputs": [], "outputs": []})
        dst = entry["inputs"] if direction == "input" else entry["outputs"]
        for n in names:
            if n not in entry["inputs"] and n not in entry["outputs"]:
                dst.append(n)

    for m in _MOD_RE.finditer(text):
        mod = m.group(1)
        ports.setdefault(mod, {"inputs": [], "outputs": []})
        i = m.end()
        while i < len(text) and text[i] in " \t\n\r":
            i += 1
        if i < len(text) and text[i] == "#":
            j = text.find("(", i)
            if j == -1:
                continue
            inner = _balanced_inner(text, j)
            if inner is None:
                continue
            i = j + len(inner) + 2
            while i < len(text) and text[i] in " \t\n\r":
                i += 1
        if i >= len(text) or text[i] != "(":
            continue
        inner = _balanced_inner(text, i)
        if inner is None:
            continue
        direction = None
        for seg in inner.split(","):
            seg = seg.strip()
            low = seg.lower()
            for key in ("input", "output", "inout"):
                if low == key or low.startswith(key + " ") or \
                        low.startswith(key + "\t") or \
                        low.startswith(key + "["):
                    direction = key
                    seg = seg[len(key):].strip()
                    break
            else:
                # No fresh direction: carried from previous segment
                # (``input [7:0] a, b``) -- keep current direction.
                pass
            if direction is None:
                continue
            seg = _BRACKET_RE.sub(" ", seg).strip()
            seg = _TYPE_KW_RE.sub("", seg).strip()
            names = [t for t in _IDENT_RE.findall(seg)
                     if t not in ("reg", "wire", "logic", "signed")]
            if names:
                _add(mod, direction, [names[-1]])
    for m in _DIR_DECL_RE.finditer(text):
        direction, chunk = m.group(1), m.group(2)
        # Attribute to the enclosing module (last header before decl).
        owner = None
        for hm in _MOD_RE.finditer(text, 0, m.start()):
            owner = hm.group(1)
        if owner is None:
            continue
        _add(owner, direction, _clean_names(chunk))
    return ports


def _defined_modules(tree, files):
    """Module -> defining file (first definition wins, like the slicer)."""
    defined = {}
    for rel in files:
        try:
            with open(os.path.join(tree, rel), "r", encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in _MOD_RE.finditer(text):
            defined.setdefault(m.group(1), rel)
    return defined


def instantiation_sites(tree, files):
    """[(parent_mod, parent_file, child_mod, {formal: actual_expr})].

    Only sites whose child names a module defined somewhere in the
    file set (module headers and keywords never qualify). File order,
    then appearance order (deterministic).
    """
    try:
        defined = _defined_modules(tree, files)
    except Exception:
        return []
    sites = []
    for rel in files:
        try:
            with open(os.path.join(tree, rel), "r", encoding="utf-8",
                      errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        owners = [m.group(1) for m in _MOD_RE.finditer(text)]
        owner = owners[0] if owners else os.path.splitext(
            os.path.basename(rel))[0]
        for m in _INST_RE.finditer(text):
            child, _inst = m.group(1), m.group(2)
            if child in _SKIP_OWNER or child not in defined:
                continue
            inner = _balanced_inner(text, m.end() - 1)
            if inner is None:
                continue
            pairs = {}
            for pm in _PORT_PAIR_RE.finditer(inner):
                pairs.setdefault(pm.group(1), pm.group(2).strip())
            sites.append((owner, rel, child, pairs))
    return sites


def _home_file(tree, seed, files):
    """File owning seed: port match first, else driver-key match.

    A seed that IS a module boundary port (e.g. ``reg_div_di`` of
    simpleuart: an input with no driver edge in its home file, only
    port-map edges where it is instantiated) homes to its port's
    module -- otherwise the cone would start on the wrong side of the
    boundary. Non-port seeds use the driver-key scan (exact, then the
    slicer's case-insensitive fallback). Returns None when the seed
    is neither a port nor a driver anywhere (lexical-fallback-only).
    """
    low = (seed or "").lower()
    for want_input in (True, False):
        for rel in sorted(files):
            try:
                with open(os.path.join(tree, rel), "r",
                          encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for _mod, entry in parse_module_ports(text).items():
                if want_input and seed in entry["inputs"]:
                    return rel
                if not want_input and (
                        seed in entry["outputs"]
                        or seed.lower() in
                        [p.lower() for p in entry["outputs"]]):
                    return rel
    for rel in sorted(files):
        try:
            drivers = slicer.parse_drivers(tree, rel)
        except OSError:
            continue
        if seed in drivers:
            return rel
    for rel in sorted(files):
        try:
            drivers = slicer.parse_drivers(tree, rel)
        except OSError:
            continue
        for key in drivers:
            if key.lower() == low:
                return rel
    return None


def _line_key(line):
    """(rel, lineno) for a ``rel:lineno: text`` cone line."""
    head, _, _ = line.partition(":")
    rest, _, _ = (line[len(head) + 1:]).partition(":")
    try:
        return (head, int(rest))
    except ValueError:
        return (line, -1)


def _needs_deeper(tree, parent_mod, parent_file, parent_seen,
                  search_files):
    """True when the parent cone bottoms out at a higher-up port.

    A parent-level seen signal that is an INPUT of the parent module
    and is mapped to an actual signal at an instantiation site of the
    parent module in yet another file would require a second boundary
    crossing -- the caller must stop and flag truncated. (In-file
    port over-approx edges are ignored here on purpose: they describe
    the same unfollowed upward connection, not a local driver.)
    """
    try:
        with open(os.path.join(tree, parent_file), "r",
                  encoding="utf-8", errors="replace") as fh:
            parent_text = fh.read()
    except OSError:
        return False
    ports = parse_module_ports(parent_text)
    entry = ports.get(parent_mod, {"inputs": [], "outputs": []})
    parent_inputs = set(entry["inputs"])
    if not parent_inputs:
        return False
    for sig in parent_seen:
        if sig not in parent_inputs:
            continue
        for (_owner, rel, child, pairs) in instantiation_sites(
                tree, search_files):
            if child == parent_mod and rel != parent_file \
                    and sig in pairs:
                return True
    return False


def extended_cone(tree, seed, rtl_files=None, max_depth=None,
                  max_lines=None):
    """One-hop cross-module driver cone for seed -> (lines, report).

    Runs the wave-3 slicer for the base cone, then follows fan-in
    across at most ONE module boundary (see module docstring). The
    report carries every base-slicer key with identical values plus
    ``crossed`` (a hop resolved), ``truncated`` (a second hop would
    be needed -- deeper lines deliberately absent), ``hops``
    ([{child, child_file, port, parent, parent_file,
    parent_signal}]), and ``home`` ({"module", "file"} or None).
    With no resolvable boundary the BASE lines object is returned
    unchanged (single-module behavior byte-identical to the slicer).
    """
    if max_depth is None:
        max_depth = slicer.DEFAULT_DEPTH
    if max_lines is None:
        max_lines = slicer.MAX_CONE_LINES
    files = (list(rtl_files) if rtl_files is not None
             else slicer.list_rtl_files(tree))
    base_lines, base_report = slicer.backward_slice(
        tree, seed, files, max_depth=max_depth, max_lines=max_lines)
    report = dict(base_report)
    report["crossed"] = False
    report["truncated"] = False
    report["hops"] = []
    report["home"] = None
    if not isinstance(seed, str) or not seed:
        return base_lines, report
    home_rel = _home_file(tree, seed, files)
    if home_rel is None:
        return base_lines, report
    try:
        with open(os.path.join(tree, home_rel), "r", encoding="utf-8",
                  errors="replace") as fh:
            home_text = fh.read()
    except OSError:
        return base_lines, report
    home_ports = parse_module_ports(home_text)
    home_mod = next(iter(home_ports), os.path.splitext(
        os.path.basename(home_rel))[0])
    report["home"] = {"module": home_mod, "file": home_rel}
    try:
        home_drivers = slicer.parse_drivers(tree, home_rel)
    except OSError:
        return base_lines, report
    home_inputs = set(home_ports.get(
        home_mod, {"inputs": []})["inputs"])
    seen = list(base_report.get("seen", []))
    frontier = [s for s in seen
                if s in home_inputs and not home_drivers.get(s)]
    if not frontier:
        return base_lines, report
    search_files = sorted(set(slicer.list_rtl_files(tree)) | set(files))
    sites = [st for st in instantiation_sites(tree, search_files)
             if st[2] == home_mod and st[1] != home_rel]
    if not sites:
        return base_lines, report
    merged = list(base_lines)
    emitted = {_line_key(ln) for ln in merged}
    for port in frontier:
        for (parent_mod, parent_file, _child, pairs) in sites:
            if port not in pairs:
                continue
            parent_sigs = slicer.spec_anchor_signals(pairs[port])
            if not parent_sigs:
                continue  # constant actual: no signal to follow
            parent_sig = parent_sigs[0]
            try:
                plines, preport = slicer.backward_slice(
                    tree, parent_sig, [parent_file],
                    max_depth=max_depth, max_lines=max_lines)
            except OSError:
                continue
            report["hops"].append({
                "child": home_mod, "child_file": home_rel,
                "port": port, "parent": parent_mod,
                "parent_file": parent_file,
                "parent_signal": parent_sig})
            for ln in plines:
                if len(merged) >= max_lines:
                    break
                key = _line_key(ln)
                if key not in emitted:
                    emitted.add(key)
                    merged.append(ln)
            if _needs_deeper(tree, parent_mod, parent_file,
                             preport.get("seen", []), search_files):
                report["truncated"] = True
            break  # first resolving site wins (deterministic)
    if not report["hops"]:
        return base_lines, report
    report["crossed"] = True
    report["lines"] = len(merged)
    report["files"] = sorted({ln.split(":", 1)[0] for ln in merged})
    return merged, report


def format_prompt_section(lines, report, max_chars=1200):
    """Render the extended cone as a cited prompt section ("" if none)."""
    if not lines:
        return ""
    report = report or {}
    hops = report.get("hops", []) or []
    head = "CONE_PLUS seed=%s crossed=%s truncated=%s lines=%d" % (
        report.get("seed", "?"), report.get("crossed", False),
        report.get("truncated", False), len(lines))
    parts = [head]
    for hop in hops:
        parts.append("hop: %s.%s -> %s (%s)" % (
            hop.get("child", "?"), hop.get("port", "?"),
            hop.get("parent_signal", "?"),
            hop.get("parent_file", "?")))
    for ln in lines:
        parts.append(ln)
    out = "\n".join(parts)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n...[truncated]"
    return out


def cone_for_prompt(tree, seed, rtl_files=None, enable_cone_plus=False,
                    max_depth=None, max_lines=None, max_chars=1200):
    """EvidencePack section dict (default-off: disabled reads nothing)."""
    if not enable_cone_plus:
        return {"cone_plus_enabled": False, "cone_lines": [],
                "cone_report": None, "prompt_section": ""}
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError(
            "cone_plus needs a non-empty seed signal (refusing to guess)")
    if not isinstance(tree, str) or not os.path.isdir(tree):
        raise ValueError(
            "cone_plus tree is not a readable directory: %r" % (tree,))
    lines, report = extended_cone(
        tree, seed, rtl_files, max_depth=max_depth, max_lines=max_lines)
    return {"cone_plus_enabled": True, "cone_lines": lines,
            "cone_report": report,
            "prompt_section": format_prompt_section(
                lines, report, max_chars=max_chars)}
