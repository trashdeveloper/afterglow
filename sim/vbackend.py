#!/usr/bin/env python3
"""REAL Verilator backend for the Afterglow sim board. No stubs, no mocks.

Pipeline per case: ensure_tree (clean checkout) -> inject (line-anchored bug)
-> build (verilator --binary --timing) -> run (binary +seed) -> boot.log + verdict.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIMDIR = Path(__file__).resolve().parent
WORK = Path(os.environ.get("AF_WORK", str(Path.home() / ".cache" / "afterglow" / "work")))


def sh(cmd, cwd=None, timeout=600):
    p = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return p.returncode, p.stdout


def load_pins(path=None):
    pins = {}
    pinfile = Path(path) if path else REPO / "targets.pin"
    for raw in pinfile.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            raise ValueError("bad pin line: %r" % raw)
        pins[parts[0]] = (parts[1], parts[2])
    return pins


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def load_case(name):
    """Minimal YAML parser for sim/targets/<case>.yaml (flat keys only,
    plus one nested block: the pair-spec `faults:` list of
    {file,line,anchor,bug} faults).

    Any OTHER indented or dash line raises ValueError loudly -- the
    parser used to strip indentation and silently mangle nested blocks
    into garbage keys, which mis-fired pair REDs down the single-fault
    sim path. Pair specs also skip the anchor/bug required keys (they
    pin faults, not one anchor/bug).
    """
    text = (SIMDIR / "targets" / ("%s.yaml" % name)).read_text()
    d = {}
    raw_lines = text.splitlines()
    i = 0
    n = len(raw_lines)
    while i < n:
        raw = raw_lines[i]
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        if indent > 0 or stripped.startswith("- "):
            raise ValueError("bad yaml line in %s (only a faults: block "
                             "may nest): %r" % (name, raw))
        if ":" not in stripped:
            raise ValueError("bad yaml line in %s: %r" % (name, raw))
        k, v = stripped.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k == "faults":
            if v:
                raise ValueError("faults in %s must be a block, got %r"
                                 % (name, raw))
            block = []
            while i < n:
                nxt = raw_lines[i]
                if not nxt.strip() or nxt.strip().startswith("#"):
                    i += 1
                    continue
                if len(nxt) - len(nxt.lstrip(" \t")) == 0:
                    break
                block.append(nxt)
                i += 1
            d[k] = _parse_faults_block(block, name)
            continue
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            items = []
            if inner:
                items = [_unquote(x) for x in inner.split(",")]
            d[k] = items
        else:
            d[k] = _unquote(v)
    required = ("project", "files", "top", "tb", "anchor", "bug", "gate",
                "oracle_pass")
    if "faults" in d:
        required = tuple(k for k in required if k not in ("anchor", "bug"))
    for req in required:
        if req not in d:
            raise KeyError("case %s missing key %s" % (name, req))
    if isinstance(d["files"], str):
        d["files"] = [d["files"]]
    d.setdefault("oracle_trap", "TRAP:")
    d.setdefault("vflags", [])
    if isinstance(d["vflags"], str):
        d["vflags"] = [d["vflags"]] if d["vflags"] else []
    d["name"] = name
    return d


_FAULT_KEYS = ("file", "line", "anchor", "bug")


def _finish_fault(current, where):
    """Validate one fault dict; raises ValueError on any deviation."""
    if set(current) != set(_FAULT_KEYS):
        raise ValueError("fault in %s needs exactly %s, got %s"
                         % (where, list(_FAULT_KEYS), sorted(current)))
    for key in ("file", "anchor", "bug"):
        if not str(current[key]).strip():
            raise ValueError("fault %s blank in %s" % (key, where))
    return current


def _parse_faults_block(lines, where):
    """Parse the indented lines of a `faults:` block (stdlib only).

    Loud ValueError on anything not exactly `- ` items of
    {file,line,anchor,bug} -- never silently mangles.
    """
    faults = []
    current = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        if indent == 0:
            raise ValueError("faults block in %s interrupted by %r"
                             % (where, raw))
        if stripped.startswith("- "):
            if current is not None:
                faults.append(_finish_fault(current, where))
            sub = stripped[2:].strip()
            if ":" not in sub:
                raise ValueError("bad fault item in %s: %r" % (where, raw))
            key, val = sub.split(":", 1)
            key = key.strip()
            if key != "file":
                raise ValueError("fault item in %s must start with "
                                 "'file:', got %r" % (where, raw))
            current = {"file": _unquote(val)}
        else:
            if current is None:
                raise ValueError("fault field outside an item in %s: %r"
                                 % (where, raw))
            if ":" not in stripped:
                raise ValueError("bad fault line in %s: %r" % (where, raw))
            key, val = stripped.split(":", 1)
            key = key.strip()
            if key not in _FAULT_KEYS or key in current:
                raise ValueError("bad fault key in %s: %r" % (where, raw))
            if key == "line":
                try:
                    current[key] = int(_unquote(val))
                except ValueError:
                    raise ValueError("fault line not an integer in %s: %r"
                                     % (where, raw))
            else:
                current[key] = _unquote(val)
    if current is not None:
        faults.append(_finish_fault(current, where))
    if not faults:
        raise ValueError("empty faults block in %s" % where)
    return faults


def tree_dir(project, sha):
    return WORK / ("%s-%s" % (project, sha[:12]))


def ensure_tree(project, sha=None, url=None, files=()):
    """Clone (fetch depth 1) + checkout sha. DIRTY TREE IS RESET with
    `git checkout -- <files>` before returning so inject state never leaks."""
    pins = load_pins()
    if project not in pins:
        raise KeyError("unknown project %r (see targets.pin)" % project)
    purl, psha = pins[project]
    url = url or purl
    sha = sha or psha
    td = tree_dir(project, sha)
    if not (td / ".git").exists():
        WORK.mkdir(parents=True, exist_ok=True)
        if td.exists():
            shutil.rmtree(td)
        rc, out = sh(["git", "clone", "--depth", "1", url, str(td)], timeout=300)
        if rc:
            raise RuntimeError("clone failed:\n%s" % out)
    sh(["git", "-C", str(td), "fetch", "--depth", "1", "origin", sha], timeout=300)
    rc, out = sh(["git", "-C", str(td), "checkout", sha], timeout=120)
    if rc:
        raise RuntimeError("checkout %s failed:\n%s" % (sha, out))
    # Reset the WHOLE tree, not just this case's files: a previous case may
    # have injected into a different file, and that state must never leak.
    del files
    rc, out = sh(["git", "-C", str(td), "checkout", "--", "."], timeout=120)
    if rc:
        raise RuntimeError("dirty-tree reset failed:\n%s" % out)
    return td


def _find_all(tree, case):
    """Resolve case RTL files to absolute build sources. Raises if missing."""
    srcs = []
    for rel in case["files"]:
        p = Path(tree) / rel
        if not p.is_file():
            raise FileNotFoundError("missing RTL source: %s" % p)
        srcs.append(str(p))
    return srcs


def is_fixed(tree, case):
    """True iff the pristine anchor line is present in all case files."""
    anchor = case["anchor"].strip()
    for rel in case["files"]:
        text = (Path(tree) / rel).read_text()
        stripped = [ln.strip() for ln in text.splitlines()]
        if anchor not in stripped:
            return False
    return True


def inject(tree, case):
    """Line-anchored injection: every line whose stripped form equals the
    anchor is replaced by (that line's indent + bug). Returns hit count."""
    if "faults" in case:
        raise RuntimeError(
            "case %s is a multi-fault pair spec: sim.py inject is "
            "line-anchored and would mis-fire the pair as single-fault "
            "injections -- fire via agent/loop.py --spec "
            "sim/targets/%s.yaml instead (it applies the pinned faults "
            "list, verified multi-line)"
            % (case.get("name"), case.get("name")))
    if "mutant_line" in case:
        raise RuntimeError(
            "case %s is a line-pinned mutant: sim.py inject is "
            "line-anchored (replaces EVERY anchor match) and would "
            "mis-fire multi-match anchors as double faults -- fire via "
            "agent/loop.py --spec sim/targets/%s.yaml instead (it "
            "applies the pinned line only, verified single-line)"
            % (case.get("name"), case.get("name")))
    anchor = case["anchor"].strip()
    bug = case["bug"].strip()
    total = 0
    for rel in case["files"]:
        p = Path(tree) / rel
        lines = p.read_text().splitlines(keepends=True)
        out = []
        for ln in lines:
            body = ln.rstrip("\n")
            stripped = body.strip()
            if stripped == anchor:
                indent = body[:len(body) - len(body.lstrip())]
                out.append("%s%s\n" % (indent, bug))
                total += 1
            else:
                out.append(ln)
        p.write_text("".join(out))
    if total == 0:
        raise RuntimeError("inject: anchor not found: %r" % anchor)
    return total


def build(tree, case):
    """REAL verilator build. Returns path to the sim binary."""
    top = case["top"]
    mdir = Path(tree) / ("obj_afterglow_%s" % case["name"])
    binary = mdir / ("V%s" % top)
    tb = SIMDIR / "tb" / case["tb"]
    if not tb.is_file():
        raise FileNotFoundError("missing testbench: %s" % tb)
    cmd = ["verilator", "--binary", "--timing", "-j", "4",
           "--top-module", top, "-Mdir", str(mdir), "-o", "V%s" % top]
    cmd += _find_all(tree, case) + [str(tb)] + list(case.get("vflags", []))
    rc, out = sh(cmd, cwd=str(tree), timeout=900)
    if rc or not binary.exists():
        raise RuntimeError("verilator build failed (exit %d):\n%s" % (rc, out))
    return binary


def run_case(name, seed=42, boot="/tmp/boot.log", verdict="/tmp/v.json"):
    """Full board run: clean tree -> inject bug -> build -> run -> verdict.
    Returns 0 on PASS, 1 on RED. Always writes boot log + verdict json."""
    case = load_case(name)
    if "faults" in case:
        # Fail fast: pair specs refuse in inject(), but check here so a
        # mis-fired pair stem never even clones/resets a tree first.
        raise RuntimeError(
            "case %s is a multi-fault pair spec: fire via agent/loop.py "
            "--spec sim/targets/%s.yaml instead" % (name, name))
    pins = load_pins()
    if case["project"] not in pins:
        raise KeyError("unknown project %r" % case["project"])
    url, sha = pins[case["project"]]
    tree = ensure_tree(case["project"], sha, url, case["files"])
    ninj = inject(tree, case)
    binary = build(tree, case)
    try:
        rc, log = sh([str(binary), "+seed=%d" % seed], cwd=str(tree), timeout=300)
    except subprocess.TimeoutExpired:
        log = "- TIMEOUT after 300s\ntohost: FAIL 0xdead\nTRAP: sim timeout\n"
    Path(boot).write_text(log)
    lines = log.splitlines()
    det = [ln for ln in lines if not ln.startswith("- ")]
    checksum = hashlib.sha256(("\n".join(det) + "\n").encode()).hexdigest()
    tohost = None
    for ln in lines:
        if "tohost:" in ln:
            tohost = ln.split("tohost:", 1)[1].strip()
    passed = case["oracle_pass"] in log
    m = re.search(r"CYCLES=(\d+)", log)
    cycles = int(m.group(1)) if m else None
    now = datetime.datetime.now(datetime.timezone.utc)
    v = {"case": name, "pass": bool(passed), "tohost": tohost,
         "checksum": checksum, "cycles": cycles, "seed": int(seed),
         "sim_time_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    Path(verdict).write_text(json.dumps(v, indent=2) + "\n")
    print("%s %s tohost=%s checksum=%s cycles=%s seed=%s (inject x%d)" % (
        name, "PASS" if passed else "RED", tohost,
        checksum[:12], cycles, seed, ninj), flush=True)
    return 0 if passed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Afterglow REAL Verilator backend")
    ap.add_argument("--inject", metavar="CASE", help="inject case bug into tree and exit")
    ap.add_argument("--case", help="run this case board")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--boot", default="/tmp/boot.log")
    ap.add_argument("--verdict", default="/tmp/v.json")
    a = ap.parse_args(argv)
    if a.inject:
        case = load_case(a.inject)
        pins = load_pins()
        url, sha = pins[case["project"]]
        tree = ensure_tree(case["project"], sha, url, case["files"])
        n = inject(tree, case)
        print("injected %s into %s (x%d lines)" % (a.inject, tree, n), flush=True)
        return 0
    if a.case:
        return run_case(a.case, seed=a.seed, boot=a.boot, verdict=a.verdict)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
