#!/usr/bin/env python3
"""Seeded single-point Verilog mutator: the aiming half of the PILOT.

Mutation-fault campaign (Depth centerpiece: big-N fix-rate statistics
instead of 8 hand cases). This file AIMS (creates + documents faults);
it never fires (no network, no API keys, no live sim runs -- a static
scan in tests/test_mutate.py enforces the no-network rule).

Operators (each rewrites exactly ONE site on ONE line):
  constant_flip  sized-binary bit flip, e.g. 1'b0 <-> 1'b1
  cmp_swap       STATEMENT-AWARE comparison swap, e.g. == <-> !=, > <-> <=.
    A depth-0 `<=` with no bare `=`/`?` before it is a non-blocking
    assignment (e.g. `recv_state <= 0;`), never a comparison site: the
    naive swap used to emit `recv_state > 0;` expression statements
    that Verilator rejects (build-breakers, never RED). True
    comparisons -- inside if/while/for parens, ternary branches,
    `assign ... = a <= b` -- keep their sites and land RED.
  cond_grow      grown conditions: strict `>` -> `>=`, `<` -> `<=` (true
    on one more value). Only strict inequalities are sites; shifts
    (`>>`, `<<`, `->`) and `<=`/`>=`/equality are never sites, so every
    site compiles and changes behavior (RED or honest equivalent).
  logic_swap     `&&` <-> `||`. These tokens occur only inside expressions,
    so every swap compiles and changes behavior (RED or equivalent).
  num_bump       numeric literal +/-1, e.g. 16'h8E06 -> 16'h8E07

Locked-case regions are NEVER touched. The skip list is READ from
eval/cases.yaml (tier: locked ids) joined with sim/targets/<id>.yaml
(anchor + bug stripped lines per file) -- never hardcoded. A candidate
line whose stripped form equals any locked anchor/bug line is refused.

Tree discipline: the SOURCE tree is never modified (copied first, then
exactly one line in the copy is rewritten). Mutated trees live under
/tmp (or runs/scratch) ONLY -- any dest resolving elsewhere in the
repo is refused with DestRefused.

Reproducibility: random.Random(seed) drives site choice (and bump
direction); the record carries {seed, op, file, line, before, after}
so any mutant tree is rebuildable with one command.

Spec emitter: emit_mutant_spec() writes the SAME flat YAML schema as
sim/targets/*.yaml (project/files/top/tb/anchor/bug/gate/oracle_pass/
oracle_trap) plus mutant_* metadata, so red = mutant fails oracle and
green = afterglow patch restores the oracle match.

Usage:
  python3 eval/mutate.py --tree SRC --dest /tmp/mut-x --seed 42
  python3 eval/mutate.py --tree SRC --dest /tmp/mut-x --seed 42 --op cmp_swap
  python3 eval/mutate.py --tree SRC --dest /tmp/mut-x --seed 42 \\
      --emit-spec eval/mutant_pilot/mut-x.yaml --mutant-id mut-x \\
      --top tb_picosoc_uart --tb picosoc-uart-div_tb.sv \\
      --parent-case picosoc-uart-div --tree-label /tmp/mut-x
  python3 eval/mutate.py --list-skips
"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CASES = os.path.join(ROOT, "eval", "cases.yaml")
DEFAULT_TARGETS = os.path.join(ROOT, "sim", "targets")


class NoSiteError(RuntimeError):
    """No applicable mutation site (operator absent on the line/tree)."""


class DestRefused(RuntimeError):
    """Dest is inside the repo outside runs/scratch -- refused."""


OPS = ("constant_flip", "cmp_swap", "cond_grow", "logic_swap", "num_bump")

CMP_SWAP = {
    "==": "!=",
    "!=": "==",
    "===": "!==",
    "!==": "===",
    "<=": ">",
    ">=": "<",
    "<": ">=",
    ">": "<=",
}
COND_GROW = {
    ">": ">=",
    "<": "<=",
}
LOGIC_SWAP = {
    "&&": "||",
    "||": "&&",
}
_CMP_RE = re.compile(r"!==|===|!=|==|<=|>=|<|>")
# Strict inequalities only: the lookarounds exclude shifts (<<, >>, >>>),
# `->`, and `<=`/`>=` (so non-blocking `x <= 0;` is never a site here).
_STRICT_RE = re.compile(r"(?<![<>-])<(?![<=])|(?<![<>-])>(?![>=])")
_LOGIC_RE = re.compile(r"&&|\|\|")
_CONST_RE = re.compile(r"(\d+)'([bB])([01xXzZ_?]+)")
_NUM_RE = re.compile(
    r"(?P<sized>\d+'[hHdD][0-9a-fA-F_]+)"
    r"|(?P<bare>(?<![\w'])(\d+)(?![\w']))"
)

_COPY_IGNORE = (".git", "__pycache__")


_BLOCK_SAME_LINE_RE = re.compile(r"/\*.*?\*/")


def _code_part(line: str) -> str:
    """Verilog code before any // comment, with same-line /*...*/ spans
    blanked (same length, so occurrence positions still index the
    original line). Multi-line block interiors are skipped by
    enumerate_candidates, never by this single-line view."""
    no_block = _BLOCK_SAME_LINE_RE.sub(
        lambda m: " " * len(m.group(0)), line)
    return no_block.split("//", 1)[0]


# --------------------------------------------------------------------------
# locked-case skip list (read, never hardcoded)
# --------------------------------------------------------------------------

def _parse_cases_locked(cases_yaml: str) -> list:
    """Locked ids from eval/cases.yaml (same minimal `- id:`/`tier:` scan)."""
    ids: list = []
    cur: str | None = None
    tier = "stress"
    with open(cases_yaml, encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            m = re.match(r"\s*-\s*id:\s*(\S+)\s*$", raw)
            if m:
                if cur is not None and tier == "locked":
                    ids.append(cur)
                cur, tier = m.group(1), "stress"
                continue
            m2 = re.match(r"\s*tier:\s*(\S+)\s*$", raw)
            if m2 and cur is not None:
                tier = m2.group(1)
    if cur is not None and tier == "locked":
        ids.append(cur)
    return ids


def _parse_flat_yaml(path: str) -> dict:
    """Flat `key: value` parse (same shape as vbackend.load_case)."""
    d: dict = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh.read().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                inner = v[1:-1].strip()
                items = []
                if inner:
                    for x in inner.split(","):
                        x = x.strip()
                        if len(x) >= 2 and x[0] == x[-1] and x[0] in "\"'":
                            x = x[1:-1]
                        items.append(x)
                d[k] = items
            else:
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                d[k] = v
    return d


def load_locked_skips(cases_yaml: str = DEFAULT_CASES,
                      targets_dir: str = DEFAULT_TARGETS) -> dict:
    """{ids: [locked ids], files: {rel_path: [stripped anchor+bug lines]}}.

    SKIP LIST (documented, derived at runtime -- current values):
    ids picosoc-uart-div, picorv32-fetch-misalign, uart-tx-div map to
      picosoc/simpleuart.v: the RX-divisor anchor/bug and the TX-divisor
        anchor/bug (4 unique stripped lines; the uart-start-half stress
        case reuses one of them as its bug line but is NOT locked)
      picorv32.v: the fetch-misalign anchor/bug (2 stripped lines)
    """
    ids = _parse_cases_locked(cases_yaml)
    files: dict = {}
    for cid in ids:
        case = _parse_flat_yaml(os.path.join(targets_dir, cid + ".yaml"))
        rels = case.get("files", [])
        if isinstance(rels, str):
            rels = [rels]
        for rel in rels:
            bucket = files.setdefault(rel, [])
            for key in ("anchor", "bug"):
                line = str(case.get(key, "")).strip()
                if line and line not in bucket:
                    bucket.append(line)
    return {"ids": ids, "files": files}


def locked_line_set(skips: dict) -> set:
    return {ln.strip() for lines in skips["files"].values() for ln in lines}


# --------------------------------------------------------------------------
# operators (pure: input line never modified, new string returned)
# --------------------------------------------------------------------------

def _flippable_positions(code: str) -> list:
    """(match_start, bit_index, bit_char) for every 0/1 sized-binary bit."""
    out = []
    for m in _CONST_RE.finditer(code):
        for i, ch in enumerate(m.group(3)):
            if ch in "01":
                out.append((m.start(3) + i, ch))
    return out


def apply_constant_flip(line: str, site: int, _sign: int = 0) -> str:
    code = _code_part(line)
    positions = _flippable_positions(code)
    if site < 0 or site >= len(positions):
        raise NoSiteError("constant_flip: no 0/1 bit at site %d" % site)
    pos, ch = positions[site]
    flipped = "1" if ch == "0" else "0"
    return line[:pos] + flipped + line[pos + 1:]


def _cmp_occurrences(code: str) -> list:
    """True comparison sites (statement-aware).

    A `<=` match at round-paren depth 0 with no bare `=`/`?` before it
    (after stripping ==/!=/<=/>=/===/!==) is a statement-level
    non-blocking assignment, not a comparison -- skipped, so cmp_swap
    can no longer emit `x > 0;` expression statements. `<=` inside
    if/while/for parens (depth > 0) or in `assign x = a <= b;`
    (bare `=` before) stays a site.
    """
    out = []
    for m in _CMP_RE.finditer(code):
        if m.group(0) == "<=" and _is_assign_le(code, m.start()):
            continue
        out.append((m.start(), m.group(0)))
    return out


def _blank_strings(code: str) -> str:
    """Copy of code with double-quoted literal spans blanked (parens inside
    $display formats must not disturb depth counting)."""
    chars = list(code)
    in_str = False
    for i, ch in enumerate(chars):
        if in_str:
            chars[i] = " "
            if ch == '"':
                in_str = False
        elif ch == '"':
            chars[i] = " "
            in_str = True
    return "".join(chars)


def _paren_depth(code: str, pos: int) -> int:
    """Round-paren depth at pos (on string-blanked code)."""
    blanked = _blank_strings(code[:pos])
    return blanked.count("(") - blanked.count(")")


_BARE_ASSIGN_SCAN = re.compile(r"===|!==|==|!=|<=|>=")


def _is_assign_le(code: str, pos: int) -> bool:
    """True iff the `<=` at pos is a statement-level non-blocking assign."""
    if _paren_depth(code, pos) > 0:
        return False
    before = _BARE_ASSIGN_SCAN.sub("", code[:pos])
    return ("=" not in before) and ("?" not in before)


def _cond_grow_occurrences(code: str) -> list:
    """(pos, op) for strict `<`/`>` (shifts/assigns already excluded)."""
    return [(m.start(), m.group(0)) for m in _STRICT_RE.finditer(code)]


def _logic_occurrences(code: str) -> list:
    """(pos, op) for `&&`/`||` (expression-only tokens)."""
    return [(m.start(), m.group(0)) for m in _LOGIC_RE.finditer(code)]


def apply_cmp_swap(line: str, site: int, _sign: int = 0) -> str:
    code = _code_part(line)
    occurrences = _cmp_occurrences(code)
    if site < 0 or site >= len(occurrences):
        raise NoSiteError("cmp_swap: no comparison op at site %d" % site)
    pos, op = occurrences[site]
    return line[:pos] + CMP_SWAP[op] + line[pos + len(op):]


def apply_cond_grow(line: str, site: int, _sign: int = 0) -> str:
    code = _code_part(line)
    occurrences = _cond_grow_occurrences(code)
    if site < 0 or site >= len(occurrences):
        raise NoSiteError("cond_grow: no strict inequality at site %d"
                          % site)
    pos, op = occurrences[site]
    return line[:pos] + COND_GROW[op] + line[pos + len(op):]


def apply_logic_swap(line: str, site: int, _sign: int = 0) -> str:
    code = _code_part(line)
    occurrences = _logic_occurrences(code)
    if site < 0 or site >= len(occurrences):
        raise NoSiteError("logic_swap: no &&/|| at site %d" % site)
    pos, op = occurrences[site]
    return line[:pos] + LOGIC_SWAP[op] + line[pos + len(op):]


def _num_occurrences(code: str) -> list:
    """(start, end, kind, value_str, width_str) left-to-right numerics."""
    out = []
    for m in _NUM_RE.finditer(code):
        if m.group("sized") is not None:
            text = m.group("sized")
            size_s, rest = text.split("'", 1)
            radix, digits = rest[0], rest[1:]
            out.append((m.start(), m.end(), "sized", size_s, radix, digits))
        else:
            out.append((m.start(), m.end(), "bare", m.group("bare"),
                        "", ""))
    return out


def _bump_digits(radix: str, digits: str, delta: int) -> str:
    clean = digits.replace("_", "")
    base = 16 if radix in "hH" else 10
    width = len(digits)
    if radix in "hH":
        mod = 16 ** width
    else:
        mod = 10 ** width
    bumped = (int(clean, base) + delta) % mod
    text = format(bumped, "x" if base == 16 else "d").zfill(width)
    if base == 16 and any(c in "ABCDEF" for c in digits):
        text = text.upper()
    return text[-width:]


def apply_num_bump(line: str, site: int, sign: int = 0) -> str:
    code = _code_part(line)
    occurrences = _num_occurrences(code)
    if site < 0 or site >= len(occurrences):
        raise NoSiteError("num_bump: no numeric literal at site %d" % site)
    delta = -1 if sign < 0 else +1
    start, end, kind, v0, radix, digits = occurrences[site]
    if kind == "sized":
        new_digits = _bump_digits(radix, digits, delta)
        new_text = "%s'%s%s" % (v0, radix, new_digits)
    else:
        value = int(v0) + delta
        if value < 0:  # bare 0 bumped down: force +1, never a no-op
            value = int(v0) + 1
        new_text = str(value)
    return line[:start] + new_text + line[end:]


_APPLY = {
    "constant_flip": apply_constant_flip,
    "cmp_swap": apply_cmp_swap,
    "cond_grow": apply_cond_grow,
    "logic_swap": apply_logic_swap,
    "num_bump": apply_num_bump,
}


# --------------------------------------------------------------------------
# tree copy + seeded single-point mutation
# --------------------------------------------------------------------------

def dest_allowed(dest: str) -> bool:
    here = os.path.abspath(dest)
    for base in ("/tmp", "/var/tmp",
                 os.path.join(ROOT, "runs", "scratch")):
        norm = os.path.abspath(base)
        if here == norm or here.startswith(norm + os.sep):
            return True
    return False


def _copy_tree(src: str, dest: str, force: bool = False) -> None:
    if os.path.exists(dest):
        if not force:
            raise DestRefused("dest exists (use --force): %s" % dest)
        shutil.rmtree(dest)

    def ignore(_dir, names):
        out = [n for n in names
               if n in _COPY_IGNORE or n.startswith("obj_afterglow_")
               or n.endswith(".orig")]
        return out

    shutil.copytree(src, dest, ignore=ignore)


def _iter_rtl_files(tree: str) -> list:
    """Tree-relative *.v/*.sv files, testbenches (*tb*) excluded, sorted."""
    found = []
    for dirpath, _dirs, fns in os.walk(tree):
        for fn in sorted(fns):
            if not (fn.endswith(".v") or fn.endswith(".sv")):
                continue
            if "tb" in fn.lower():
                continue
            found.append(os.path.relpath(os.path.join(dirpath, fn), tree))
    return sorted(found)


def enumerate_candidates(tree: str, skips: dict | None = None,
                         files: list | None = None) -> list:
    """Every (file, line, op, site) triple, deterministic order.

    Lines whose stripped form is a locked anchor/bug line, or that are
    // comments, are never candidates. `files` optionally restricts to
    a subset of tree-relative paths (e.g. the TB-relevant RTL only).
    """
    locked = locked_line_set(skips) if skips else set()
    wanted = set(files) if files else None
    candidates = []
    site_counts: dict = {}

    def _next_site(rel, idx, op):
        key = (rel, idx, op)
        site = site_counts.get(key, 0)
        site_counts[key] = site + 1
        return site

    for rel in _iter_rtl_files(tree):
        if wanted is not None and rel not in wanted:
            continue
        with open(os.path.join(tree, rel), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        in_block = False
        for idx, line in enumerate(lines):
            # Multi-line /* ... */ interiors are comments, never
            # statements. (Scope files never share these lines with code,
            # so the whole line is skipped; same-line /*...*/ spans are
            # blanked inside _code_part instead.)
            if in_block:
                if "*/" in line:
                    in_block = False
                continue
            opener = line.find("/*")
            if opener >= 0 and "*/" not in line[opener:]:
                in_block = True
                line = line[:opener]
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if stripped in locked:
                continue
            code = _code_part(line)
            for _pos, _ch in _flippable_positions(code):
                candidates.append({"file": rel, "line": idx + 1,
                                   "op": "constant_flip",
                                   "site": _next_site(rel, idx + 1,
                                                      "constant_flip")})
            for _m in _cmp_occurrences(code):
                candidates.append({"file": rel, "line": idx + 1,
                                   "op": "cmp_swap",
                                   "site": _next_site(rel, idx + 1,
                                                      "cmp_swap")})
            for _m in _cond_grow_occurrences(code):
                candidates.append({"file": rel, "line": idx + 1,
                                   "op": "cond_grow",
                                   "site": _next_site(rel, idx + 1,
                                                      "cond_grow")})
            for _m in _logic_occurrences(code):
                candidates.append({"file": rel, "line": idx + 1,
                                   "op": "logic_swap",
                                   "site": _next_site(rel, idx + 1,
                                                      "logic_swap")})
            for _m in _num_occurrences(code):
                candidates.append({"file": rel, "line": idx + 1,
                                   "op": "num_bump",
                                   "site": _next_site(rel, idx + 1,
                                                      "num_bump")})
    return candidates


def apply_candidate(line: str, op: str, site: int, sign: int = 0) -> str:
    """Apply operator op at site on a single line. Public entry for
    campaign drivers (draw_board.py); raises NoSiteError/ValueError."""
    try:
        apply = _APPLY[op]
    except KeyError:
        raise ValueError("unknown op %r (want one of %s)" % (op, OPS))
    return apply(line, site, sign)


def mutate_tree(src: str, dest: str, seed: int, op: str | None = None,
                skips: dict | None = None, force: bool = False,
                files: list | None = None) -> dict:
    """Copy src -> dest, apply ONE seeded mutation in the copy. Pure src.

    Returns the mutation record {seed, op, file, line, col, site,
    sign, before, after}. Raises NoSiteError (no candidate) or
    DestRefused (dest outside /tmp or runs/scratch, or exists).
    """
    if op is not None and op not in OPS:
        raise ValueError("unknown op %r (want one of %s)" % (op, OPS))
    if not dest_allowed(dest):
        raise DestRefused(
            "refusing dest outside /tmp or runs/scratch: %s" % dest)
    if skips is None:
        skips = load_locked_skips()
    _copy_tree(src, dest, force=force)
    pool = enumerate_candidates(dest, skips, files=files)
    if op is not None:
        pool = [c for c in pool if c["op"] == op]
    if not pool:
        raise NoSiteError("no %s candidates in %s" % (op or "mutation", src))
    rng = random.Random(seed)
    pick = pool[rng.randrange(len(pool))]
    sign = rng.choice((-1, +1)) if pick["op"] == "num_bump" else 0
    path = os.path.join(dest, pick["file"])
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    before = lines[pick["line"] - 1]
    after = _APPLY[pick["op"]](before, pick["site"], sign)
    if after == before:
        raise NoSiteError("operator no-op at %s:%d (skipped)"
                          % (pick["file"], pick["line"]))
    lines[pick["line"] - 1] = after
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    col = next((i for i, (a, b) in enumerate(zip(before, after)) if a != b),
               min(len(before), len(after)))
    return {"seed": seed, "op": pick["op"], "file": pick["file"],
            "line": pick["line"], "col": col + 1, "site": pick["site"],
            "sign": sign, "before": before.strip(),
            "after": after.strip()}


# --------------------------------------------------------------------------
# per-mutant CaseSpec YAML (same flat schema as sim/targets/*.yaml)
# --------------------------------------------------------------------------

def emit_mutant_spec(record: dict, mutant_id: str, project: str, top: str,
                     tb: str, gate: str, oracle_pass: str, oracle_trap: str,
                     parent_case: str, tree: str, vflags=None) -> str:
    """Render the mutant spec: sim-format keys + mutant_* metadata.

    vflags mirrors the parent target yaml (e.g. ["-Wno-WIDTH"] for the
    uart TB); pass explicitly, never default, so the spec carries the
    same build flags as the TB it reuses.
    """
    vf = ""
    if vflags:
        vf = "vflags: [%s]\n" % ", ".join('"%s"' % f for f in vflags)
    return (
        "# mutant %s (%s %s:%d, seed %s; parent TB/oracle: %s)\n"
        "# red = mutant fails oracle; green = afterglow patch restores it.\n"
        "project: \"%s\"\n"
        "files: [%s]\n"
        "top: \"%s\"\n"
        "tb: \"%s\"\n"
        "%s"
        "anchor: \"%s\"\n"
        "bug: \"%s\"\n"
        "gate: \"%s\"\n"
        "oracle_pass: \"%s\"\n"
        "oracle_trap: \"%s\"\n"
        "mutant_id: \"%s\"\n"
        "mutant_seed: %s\n"
        "mutant_op: \"%s\"\n"
        "parent_case: \"%s\"\n"
        "mutant_tree: \"%s\"\n"
        % (mutant_id, record["op"], record["file"], record["line"],
           record["seed"], parent_case, project, record["file"], top, tb,
           vf,
           record["before"].replace('"', "'"),
           record["after"].replace('"', "'"), gate, oracle_pass,
           oracle_trap, mutant_id, record["seed"], record["op"],
           parent_case, tree)
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seeded Verilog mutator (PILOT).")
    ap.add_argument("--tree", default=None, help="pristine source tree")
    ap.add_argument("--dest", default=None, help="mutant copy (/tmp/... only)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--op", default=None, choices=list(OPS))
    ap.add_argument("--files", default=None,
                    help="comma-separated tree-relative RTL subset")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="enumerate only, mutate nothing")
    ap.add_argument("--list-skips", action="store_true")
    ap.add_argument("--emit-spec", default=None)
    ap.add_argument("--mutant-id", default=None)
    ap.add_argument("--top", default="tb_picosoc_uart")
    ap.add_argument("--tb", default="picosoc-uart-div_tb.sv")
    ap.add_argument("--parent-case", default="picosoc-uart-div")
    ap.add_argument("--gate", default="mutant pilot")
    ap.add_argument("--oracle-pass", default="tohost: PASS")
    ap.add_argument("--oracle-trap", default="TRAP:")
    ap.add_argument("--tree-label", default=None)
    ap.add_argument("--project", default="picorv32")
    args = ap.parse_args(argv)

    if args.list_skips:
        skips = load_locked_skips()
        print("locked ids: %s" % " ".join(skips["ids"]))
        for rel in sorted(skips["files"]):
            print("skip %s:" % rel)
            for ln in skips["files"][rel]:
                print("  %s" % ln)
        return 0

    if not args.tree or (not args.dest and not args.dry_run):
        print("need --tree SRC --dest DST (or --tree SRC --dry-run, "
              "or --list-skips)", file=sys.stderr)
        return 2
    skips = load_locked_skips()
    only = args.files.split(",") if args.files else None
    if args.dry_run:
        pool = enumerate_candidates(args.tree, skips, files=only)
        if args.op:
            pool = [c for c in pool if c["op"] == args.op]
        print("candidates: %d (op=%s)" % (len(pool), args.op or "any"))
        for c in pool[:20]:
            print("  %(file)s:%(line)d [%(op)s#%(site)d]" % c)
        return 0
    record = mutate_tree(args.tree, args.dest, seed=args.seed, op=args.op,
                         skips=skips, force=args.force, files=only)
    print("mutant %s:%d [%s#%d sign=%s] seed=%s" % (
        record["file"], record["line"], record["op"], record["site"],
        record["sign"], record["seed"]))
    print("  - %s" % record["before"])
    print("  + %s" % record["after"])
    if args.emit_spec:
        text = emit_mutant_spec(
            record, mutant_id=args.mutant_id or "mutant",
            project=args.project, top=args.top, tb=args.tb, gate=args.gate,
            oracle_pass=args.oracle_pass, oracle_trap=args.oracle_trap,
            parent_case=args.parent_case,
            tree=args.tree_label or os.path.abspath(args.dest))
        with open(args.emit_spec, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("spec: %s" % args.emit_spec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
