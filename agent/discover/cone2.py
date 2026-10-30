"""Multi-hop cone slicer v2 (Task E4, plan item 54, Wave E).

Iterative deepening over fan-in boundaries. Level 0 slices the seed in
its home file; each further hop follows one boundary upward: a reached
input port is resolved at its module's instantiation site in another
file, and the wired actual is sliced there. This reuses the B5/record
machinery (`agent/evidence/cone_plus` site maps,
`slicer.spec_anchor_signals`, `slicer.backward_slice`) but steps from
the grandparent actual — the B5 hop record's parent signal bottoms out
in its own file, so re-seeding from it cannot climb further.

A visited set plus a call budget (`cycle_guard`) bounds the work.
stdlib + repo imports only; no loop/guard wiring (default-off).
"""
import os

from agent.evidence import cone_plus
from agent.evidence.cone_plus import _home_file
from afterglow_core import slicer


def _check_args(tree, seed, rtl_files, depth, cycle_guard):
    if isinstance(tree, os.PathLike):
        tree = os.fspath(tree)
    if (isinstance(tree, bool) or not isinstance(tree, str)
            or not tree.strip()):
        raise ValueError("tree must be a non-blank directory path")
    if (isinstance(seed, bool) or not isinstance(seed, str)
            or not seed.strip()):
        raise ValueError("seed must be a non-blank signal name")
    if rtl_files is not None and not isinstance(rtl_files, list):
        raise ValueError("rtl_files must be a list of file names or None")
    if (isinstance(depth, bool) or not isinstance(depth, int)
            or depth < 1):
        raise ValueError("depth must be a positive int")
    if (isinstance(cycle_guard, bool) or not isinstance(cycle_guard, int)
            or cycle_guard < 1):
        raise ValueError("cycle_guard must be a positive int")


def _read(tree, rel):
    with open("%s/%s" % (tree, rel), "r", encoding="utf-8",
              errors="replace") as handle:
        return handle.read()


def _owner_module(tree, rel, sig):
    """Module in rel owning sig (ports first), else first module."""
    try:
        ports = cone_plus.parse_module_ports(_read(tree, rel))
    except OSError:
        return None
    for mod, entry in ports.items():
        if sig in entry.get("inputs", []) + entry.get("outputs", []):
            return mod
    try:
        drivers = slicer.parse_drivers(tree, rel)
    except OSError:
        drivers = {}
    for mod in ports:
        if sig in drivers:
            return mod
    return next(iter(ports), None)


def _input_ports(tree, rel, mod):
    try:
        ports = cone_plus.parse_module_ports(_read(tree, rel))
    except OSError:
        return []
    return list(ports.get(mod, {}).get("inputs", []))


def slice_cone(tree, seed, rtl_files=None, depth=2, cycle_guard=10000):
    """Bounded multi-hop driver cone for seed.

    Returns {"lines", "depth_reached", "truncated",
    "cycle_guard_tripped", "hops", "files"}. depth=1 is exactly one
    boundary crossing (B5-equivalent reach on multi-file trees).
    """
    _check_args(tree, seed, rtl_files, depth, cycle_guard)
    tree = os.fspath(tree)
    all_files = (list(rtl_files) if rtl_files is not None
                 else sorted(slicer.list_rtl_files(tree)))
    budget = [cycle_guard]
    tripped = [False]
    visited_slice = set()
    visited_port = set()
    merged = []
    emitted = set()
    hops = []

    def spend():
        if budget[0] <= 0:
            tripped[0] = True
            return False
        budget[0] -= 1
        return True

    def merge(lines):
        added = 0
        for ln in lines:
            if ln not in emitted:
                emitted.add(ln)
                merged.append(ln)
                added += 1
        return added

    def do_slice(sig, rel):
        """backward_slice once; None when the budget refuses."""
        if not spend():
            return None
        try:
            lines, report = slicer.backward_slice(tree, sig, [rel])
        except OSError:
            return []
        return lines, report

    home = _home_file(tree, seed, all_files)
    if home is None:
        lines, _ = slicer.backward_slice(tree, seed, all_files)
        merge(lines)
        return {"lines": merged, "depth_reached": 0, "truncated": False,
                "cycle_guard_tripped": tripped[0], "hops": [],
                "files": sorted({ln.split(":", 1)[0] for ln in merged
                                 if ":" in ln})}
    first = do_slice(seed, home)
    if first is None:
        return {"lines": [], "depth_reached": 0, "truncated": True,
                "cycle_guard_tripped": True, "hops": [], "files": []}
    base_lines, base_report = first
    merge(base_lines)
    mod = _owner_module(tree, home, seed) or os_basename_module(home)
    queue = [(home, mod, p) for p in _input_ports(tree, home, mod)
             if p in (base_report.get("seen") or [])]
    for entry in queue:
        visited_port.add((entry[0], entry[1], entry[2]))
    depth_reached = 0
    while queue and len(hops) < depth and not tripped[0]:
        rel, owner, port = queue.pop(0)
        try:
            sites = [st for st in cone_plus.instantiation_sites(
                tree, all_files) if st[2] == owner and st[1] != rel]
        except OSError:
            continue
        stepped = False
        for site_mod, site_file, _child, pairs in sorted(
                sites, key=lambda st: (st[0], st[1], st[2],
                                       sorted(st[3].items()))):
            if port not in pairs:
                continue
            actuals = slicer.spec_anchor_signals(pairs[port])
            if not actuals:
                continue  # constant actual: nothing to follow
            hop = {"child": owner, "child_file": rel, "port": port,
                   "parent": site_mod, "parent_file": site_file,
                   "parent_signal": actuals[0]}
            hops.append(hop)
            for actual in actuals:
                key = (site_file, actual)
                if key in visited_slice:
                    continue
                visited_slice.add(key)
                got = do_slice(actual, site_file)
                if got is None:
                    break
                sub_lines, sub_report = got
                merge(sub_lines)
                sub_mod = _owner_module(
                    tree, site_file, actual) or os_basename_module(
                        site_file)
                for nxt in _input_ports(tree, site_file, sub_mod or ""):
                    nkey = (site_file, sub_mod, nxt)
                    if (nxt in (sub_report.get("seen") or [])
                            and nkey not in visited_port):
                        visited_port.add(nkey)
                        queue.append((site_file, sub_mod, nxt))
            stepped = True
            break  # first resolving site wins (deterministic)
        if stepped:
            depth_reached = len(hops)
        if tripped[0]:
            break
    return {"lines": merged, "depth_reached": depth_reached,
            "truncated": bool(queue), "cycle_guard_tripped": tripped[0],
            "hops": hops,
            "files": sorted({ln.split(":", 1)[0] for ln in merged
                             if ":" in ln})}


def os_basename_module(rel):
    return rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
