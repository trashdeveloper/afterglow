"""Draw + RED-verify the big-N mutant board (50 kept-RED mutants).

Seeded uniform draw from the eval/mutate.py pool over
picosoc/simpleuart.v + picorv32.v, verified one candidate at a time
under the existing Verilator sim backend (no agent firing): every kept
mutant FAILS its parent oracle, proven by sim logs. Discards
(equivalent / build-break / other) are recorded, never hidden::

    drawn == kept_new + equivalent + build_break + other   (exact)

The 5 pilot mutants (eval/mutant_pilot/manifest.json) are imported and
re-verified; 45 new kept mutants complete the board of 50.

Usage:
  python3 eval/draw_board.py --draw-seed 1234 --workdir /tmp/mutboard
  python3 eval/draw_board.py --draw-seed 1234 --workdir /tmp/mutboard --resume
  python3 eval/draw_board.py --self-check --workdir /tmp/mutboard
"""

import argparse
import copy
import json
import os
import random
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sim"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

from eval import mutate
import vbackend

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PILOT_MANIFEST = os.path.join(REPO, "eval", "mutant_pilot", "manifest.json")
LOGS_DIR = os.path.join(REPO, "eval", "mutant_pilot", "logs")
SPECS_DIR = os.path.join(REPO, "sim", "targets")

FILES_SCOPE = ["picosoc/simpleuart.v", "picorv32.v"]
# Scope note: picosoc.v (SoC top) is deliberately excluded -- neither parent
# TB (picosoc-uart-div, picorv32-fetch-misalign) builds it (TB-build rule).
PARENT_CASE = {
    "picosoc/simpleuart.v": "picosoc-uart-div",
    "picorv32.v": "picorv32-fetch-misalign",
}
SIM_SEED = 42
RUN_TIMEOUT = 600
TARGET_NEW = 45
DRAW_K = 600

SPEC_COMMENT = (
    "# mutant %s (%s %s:%d, draw seed %d; parent TB/oracle: %s)\n"
    "# red = mutant fails oracle; green = afterglow patch restores it.\n")


def _ignore(_dir, names):
    return [n for n in names
            if n in (".git", "__pycache__")
            or n.startswith("obj_afterglow_")
            or n.endswith(".orig")]


def fresh_copy(src, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=_ignore)


def oracle_lines(log):
    return [ln for ln in log.splitlines()
            if "tohost:" in ln or "TRAP:" in ln or "CYCLES=" in ln]


def parse_cycles(log):
    import re
    m = re.search(r"CYCLES=(\d+)", log)
    return int(m.group(1)) if m else None


def write_spec(kept, path):
    """Write a sanctioned sim/targets/<mutant_id>.yaml firing spec.

    Same flat key: value schema as the mut-pilot-*.yaml specs (parsed by
    afterglow_core.verilator.load_case_file); the firing path
    (agent/loop.py prepare_mutant_tree) needs mutant_file/mutant_line +
    anchor/bug, everything else is provenance.
    """
    parent = kept["parent"]
    lines = []
    lines.append(SPEC_COMMENT % (kept["mutant_id"], kept["op"],
                                kept["file"], kept["line"],
                                kept["seed"], kept["parent_case"]))
    lines.append("project: \"%s\"" % parent["project"])
    lines.append("files: [%s]" % ", ".join(parent["files"]))
    lines.append("top: \"%s\"" % parent["top"])
    lines.append("tb: \"%s\"" % parent["tb"])
    lines.append("vflags: [%s]" % ", ".join(parent["vflags"]))
    lines.append("anchor: %s" % kept["before"])
    lines.append("bug: %s" % kept["after"])
    lines.append("gate: \"%s mutant %s\""
                 % (parent["gate"], kept["mutant_id"]))
    lines.append("oracle_pass: \"%s\"" % parent["oracle_pass"])
    lines.append("oracle_trap: \"%s\"" % parent.get("oracle_trap", "TRAP:"))
    lines.append("mutant_id: \"%s\"" % kept["mutant_id"])
    lines.append("mutant_seed: %d" % kept["seed"])
    lines.append("mutant_op: \"%s\"" % kept["op"])
    lines.append("mutant_file: \"%s\"" % kept["file"])
    lines.append("mutant_line: %d" % kept["line"])
    lines.append("parent_case: \"%s\"" % kept["parent_case"])
    lines.append("mutant_tree: \"%s\"" % kept["tree"])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def load_parent_cases():
    parents = {}
    for rel in FILES_SCOPE:
        case = vbackend.load_case(PARENT_CASE[rel])
        parents[rel] = case
    return parents


def setup_base(workdir):
    """Pristine base tree for the campaign (copy of the sanctioned
    ensure_tree output, obj dirs excluded). Never mutated in place."""
    tree = vbackend.ensure_tree("picorv32")
    base = os.path.join(workdir, "base")
    fresh_copy(str(tree), base)
    sha = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    return base, str(tree), sha


def run_pristine_controls(base, parents, logs_dir):
    """Prove the harness goes GREEN without faults. Loud abort if not."""
    baseline = {}
    for rel, case in parents.items():
        dest = os.path.join(os.path.dirname(base), "control-%s" % case["name"])
        fresh_copy(base, dest)
        binary = vbackend.build(dest, case)
        rc, log = vbackend.sh([str(binary), "+seed=%d" % SIM_SEED],
                              cwd=dest, timeout=RUN_TIMEOUT)
        if case["oracle_pass"] not in log:
            raise SystemExit(
                "INFRA-FAIL: pristine control %s is not GREEN "
                "(rc=%d). Refusing to draw.\n%s"
                % (case["name"], rc, "\n".join(oracle_lines(log))))
        log_path = os.path.join(logs_dir, "board-baseline-%s.log" % case["name"])
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(log)
        baseline[case["name"]] = {
            "tree": dest, "boot_log": log_path,
            "cycles": parse_cycles(log),
            "oracle": case["oracle_pass"],
        }
        shutil.rmtree(dest, ignore_errors=True)
    return baseline


def judge_tree(dest, case):
    """Build + run + judge an already-faulted tree. Returns
    (status, rec) where rec may carry 'log' (kept only). status is kept /
    equivalent / build_break / other. Raises SystemExit on infra failure.
    """
    try:
        binary = vbackend.build(dest, case)
    except RuntimeError as e:
        return ("build_break", {"note": str(e)[-2000:]})
    newest_src = max(
        os.path.getmtime(os.path.join(dp, fn))
        for dp, _dn, fns in os.walk(dest) for fn in fns
        if fn.endswith(".v") or fn.endswith(".sv"))
    if os.path.getmtime(str(binary)) <= newest_src:
        raise SystemExit("INFRA-FAIL: binary not rebuilt in %s (stale). "
                         "Refusing to judge." % dest)
    try:
        rc, log = vbackend.sh([str(binary), "+seed=%d" % SIM_SEED],
                              cwd=dest, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ("other", {"note": "run timeout after %ds (no oracle "
                                  "verdict observed)" % RUN_TIMEOUT})
    olines = oracle_lines(log)
    cycles = parse_cycles(log)
    if case["oracle_pass"] in log:
        return ("equivalent", {"oracle_lines": olines, "cycles": cycles})
    if "tohost: FAIL" in log:
        return ("kept", {"oracle_lines": olines, "cycles": cycles,
                          "rc": rc, "log": log})
    return ("other", {"note": "no oracle verdict and no FAIL marker "
                              "(rc=%d)" % rc,
                      "log_tail": log[-2000:],
                      "oracle_lines": olines})


def verify_candidate(base, workdir, tag, cand, sign, parents):
    """Materialize + build + run ONE pool candidate in a fresh tree.

    Returns (status, rec) with rec carrying before/after line text.
    Non-kept trees are deleted; kept trees are left for the caller.
    """
    rel = cand["file"]
    case = parents[rel]
    dest = os.path.join(workdir, tag)
    fresh_copy(base, dest)
    path = os.path.join(dest, rel)
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    lineno = cand["line"]
    if not (1 <= lineno <= len(lines)):
        raise SystemExit("INFRA-FAIL: line %d out of range in %s"
                         % (lineno, rel))
    before = lines[lineno - 1]
    try:
        after = mutate.apply_candidate(before, cand["op"], cand["site"], sign)
    except mutate.NoSiteError as e:
        raise SystemExit("INFRA-FAIL: pool/tree drift for %r: %s"
                         % (cand, e))
    if after == before:
        raise SystemExit("INFRA-FAIL: no-op apply for %r" % (cand,))
    lines[lineno - 1] = after
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    status, rec = judge_tree(dest, case)
    rec["before"] = before
    rec["after"] = after
    if status != "kept":
        shutil.rmtree(dest, ignore_errors=True)
    return status, rec


def reverify_pilot_via_spec(base, workdir, mutant_id, parents):
    """Re-verify one pilot mutant through its sanctioned firing spec
    (agent/loop.py prepare_mutant_tree -- the exact path the live
    session will fire). Returns (status, rec, spec_case).

    This deliberately does NOT use the manifest's (op, site, sign):
    site numbering is an implementation detail of eval/mutate.py and has
    drifted since the pilot (mut-pilot-03's mh site no longer maps to
    its bug); the spec's anchor/bug + pinned line IS the fault.
    """
    import loop  # noqa: E402  (frozen; used, not modified)
    from afterglow_core.verilator import load_case_file  # noqa: E402
    spec = os.path.join(SPECS_DIR, "%s.yaml" % mutant_id)
    case = load_case_file(spec)
    dest = os.path.join(workdir, "reverify-%s" % mutant_id)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    loop.prepare_mutant_tree(case, base, dest)  # Loud on anchor mismatch.
    parent = vbackend.load_case(case.get("parent_case") or
                               PARENT_CASE[case["mutant_file"]])
    status, rec = judge_tree(dest, parent)
    if status != "kept":
        shutil.rmtree(dest, ignore_errors=True)
    return status, rec, case


def pilot_exclusion(pool, base):
    """Pool indices occupied by pilot faults, matched by SPEC text
    (file/line/op + apply == spec bug), immune to site renumbering."""
    from afterglow_core.verilator import load_case_file
    exclude = set()
    with open(PILOT_MANIFEST, encoding="utf-8") as fh:
        pilot = json.load(fh)
    for p in pilot["pilot"]:
        spec = os.path.join(SPECS_DIR, "%s.yaml" % p["mutant_id"])
        case = load_case_file(spec)
        rel, lineno, op = (case["mutant_file"], int(case["mutant_line"]),
                           case["mutant_op"])
        with open(os.path.join(base, rel), encoding="utf-8") as fh:
            line = fh.read().splitlines()[lineno - 1]
        hits = []
        for i, c in enumerate(pool):
            if not (c["file"] == rel and c["line"] == lineno
                    and c["op"] == op):
                continue
            for sign in (-1, +1):
                try:
                    after = mutate.apply_candidate(line, op, c["site"], sign)
                except mutate.NoSiteError:
                    continue
                if after.strip() == case["bug"].strip():
                    hits.append(i)
                    break  # same site via the other sign is the same fault
        if len(hits) != 1:
            raise SystemExit(
                "INFRA-FAIL: pilot %s matches %d pool sites (want 1): %r"
                % (p["mutant_id"], len(hits), hits))
        exclude.update(hits)
    return exclude


def draw_order(pool_size, draw_seed, exclude, k):
    rng = random.Random(draw_seed)
    indices = [i for i in range(pool_size) if i not in exclude]
    order = rng.sample(indices, min(k, len(indices)))
    signs = [rng.choice([-1, 1]) for _ in order]
    return order, signs


def board_path(workdir):
    return os.path.join(workdir, "board.json")


def save_board(out_path, board):
    board["complete"] = False
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(board, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, out_path)


def run_campaign(args):
    os.makedirs(args.workdir, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    skips = mutate.load_locked_skips()

    if args.resume and os.path.isfile(board_path(args.workdir)):
        with open(board_path(args.workdir)) as fh:
            board = json.load(fh)
        if board.get("complete"):
            print("board already complete: %s" % board_path(args.workdir))
            return 0
        if board.get("draw_seed") != args.draw_seed:
            raise SystemExit("resume seed mismatch: board has %r, asked %r"
                             % (board.get("draw_seed"), args.draw_seed))
        base = board["base"]["tree"]
        print("resuming: %d drawn so far" % board["accounting"]["drawn"])
    else:
        base, sanctree, pinsha = setup_base(args.workdir)
        parents = load_parent_cases()
        pool = mutate.enumerate_candidates(base, skips, files=FILES_SCOPE)
        with open(PILOT_MANIFEST, encoding="utf-8") as fh:
            pilot = json.load(fh)
        baseline = run_pristine_controls(base, parents, LOGS_DIR)
        board = {
            "board": "mutant-bigN-50",
            "draw_seed": args.draw_seed,
            "sim_seed": SIM_SEED,
            "pool": {"files": list(FILES_SCOPE), "size": len(pool),
                     "pin": "picorv32 %s" % pinsha,
                     "sanctioned_tree": sanctree},
            "base": {"tree": base},
            "baseline": baseline,
            "parents": {rel: {"case": c["name"], "top": c["top"],
                              "tb": c["tb"],
                              "oracle_pass": c["oracle_pass"]}
                        for rel, c in parents.items()},
            "pilot_import": {
                "manifest": os.path.relpath(PILOT_MANIFEST, REPO),
                "ids": [p["mutant_id"] for p in pilot["pilot"]],
                "reverified": {},
                "method": ("fire each sanctioned spec via "
                           "loop.prepare_mutant_tree (anchor/bug + line); "
                           "manifest mh site numbers are NOT used -- "
                           "mut-pilot-03's mh site predates a "
                           "_num_occurrences change and no longer maps to "
                           "its bug (true fault is site 2, sign -1)."),
            },
            "draw": {},
            "kept": [],
            "discarded_equivalent": [],
            "discarded_build_break": [],
            "discarded_other": [],
            "untried": [],
            "accounting": {"drawn": 0, "kept_new": 0, "kept_total": 0,
                           "equivalent": 0, "build_break": 0, "other": 0},
            "self_check": {"ran": False},
        }
    parents = load_parent_cases()
    pool = mutate.enumerate_candidates(base, skips, files=FILES_SCOPE)

    # Pilot re-verify (import, never redraw): fire each sanctioned spec
    # through the frozen prepare_mutant_tree path, then judge.
    if not board["pilot_import"]["reverified"]:
        for pid in board["pilot_import"]["ids"]:
            status, rec, _case = reverify_pilot_via_spec(
                base, args.workdir, pid, parents)
            log_path = os.path.join(
                LOGS_DIR, "board-reverify-%s.log" % pid)
            if status == "kept":
                with open(log_path, "w", encoding="utf-8") as fh:
                    fh.write(rec.pop("log"))
                board["pilot_import"]["reverified"][pid] = {
                    "red": True, "boot_log": os.path.relpath(log_path, REPO),
                    "cycles": rec["cycles"],
                    "oracle_lines": rec["oracle_lines"],
                    "tree": os.path.join(
                        args.workdir, "reverify-%s" % pid),
                }
            else:
                board["pilot_import"]["reverified"][pid] = {
                    "red": False, "status": status, "record": rec,
                }
                print("PILOT DRIFT: %s re-verified as %s (was RED)"
                      % (pid, status))
        save_board(board_path(args.workdir), board)

    pilot_red = sum(1 for v in board["pilot_import"]["reverified"].values()
                    if v.get("red"))
    pilot_shortfall = len(board["pilot_import"]["reverified"]) - pilot_red
    need_new = TARGET_NEW + pilot_shortfall
    print("pilot re-verified: %d/5 RED (shortfall %d); need %d new kept"
          % (pilot_red, pilot_shortfall, need_new))

    # Exclude pilot tuples so the draw never duplicates them.
    exclude = pilot_exclusion(pool, base)
    order, signs = draw_order(len(pool), args.draw_seed, exclude, args.draw_k)
    board["draw"] = {
        "note": ("Random(%d).sample(pool, %d) over pool minus %d pilot "
                 "tuples; signs from the same stream (pilot convention)."
                 % (args.draw_seed, args.draw_k, len(exclude))),
        "order": order, "signs": signs, "excluded_pilot": sorted(exclude),
    }

    done = {d["pool_index"] for d in (
        board["discarded_equivalent"] + board["discarded_build_break"]
        + board["discarded_other"])}
    done |= {k["pool_index"] for k in board["kept"]}
    kept_n = 0
    for pos, (idx, sign) in enumerate(zip(order, signs)):
        if board["accounting"]["kept_new"] >= need_new:
            board["untried"] = order[pos:]
            break
        if idx in done:
            continue
        cand = pool[idx]
        status, rec = verify_candidate(
            base, args.workdir, "cand-%04d" % pos, cand, sign, parents)
        entry = {"candidate": pos, "pool_index": idx, "sign": sign,
                 "record": {"file": cand["file"], "line": cand["line"],
                            "op": cand["op"], "site": cand["site"],
                            "before": rec.pop("before"),
                            "after": rec.pop("after")}}
        if status == "kept":
            kept_n = board["accounting"]["kept_new"] + 1
            mut_id = "mut-bigN-%02d" % kept_n
            kept_tree = os.path.join(args.workdir, "kept", mut_id)
            if os.path.exists(kept_tree):
                shutil.rmtree(kept_tree)
            shutil.move(os.path.join(args.workdir, "cand-%04d" % pos),
                        kept_tree)
            log_path = os.path.join(LOGS_DIR, "%s-boot.log" % mut_id)
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write(rec.pop("log"))
            spec_path = os.path.join(SPECS_DIR, "%s.yaml" % mut_id)
            kept_entry = {
                "mutant_id": mut_id, "candidate": pos,
                "pool_index": idx, "seed": args.draw_seed, "sign": sign,
                "file": cand["file"], "line": cand["line"],
                "op": cand["op"], "site": cand["site"],
                "before": entry["record"]["before"].strip(),
                "after": entry["record"]["after"].strip(),
                "parent_case": PARENT_CASE[cand["file"]],
                "parent": copy.deepcopy(parents[cand["file"]]),
                "tree": kept_tree,
                "red": {"boot_log": os.path.relpath(log_path, REPO),
                        "cycles": rec["cycles"], "rc": rec["rc"],
                        "oracle_lines": rec["oracle_lines"]},
                "spec": os.path.relpath(spec_path, REPO),
            }
            kept_entry["parent"].pop("name", None)
            write_spec(kept_entry, spec_path)
            board["kept"].append(kept_entry)
            board["accounting"]["kept_new"] += 1
            board["accounting"]["kept_total"] += 1
            print("KEPT %s %s:%d [%s] site%d cycles=%s" % (
                mut_id, cand["file"], cand["line"], cand["op"],
                cand["site"], rec["cycles"]), flush=True)
        elif status == "equivalent":
            entry["oracle_lines"] = rec["oracle_lines"]
            entry["cycles"] = rec["cycles"]
            board["discarded_equivalent"].append(entry)
            board["accounting"]["equivalent"] += 1
        elif status == "build_break":
            entry["note"] = rec["note"]
            board["discarded_build_break"].append(entry)
            board["accounting"]["build_break"] += 1
        else:
            entry.update(rec)
            board["discarded_other"].append(entry)
            board["accounting"]["other"] += 1
        board["accounting"]["drawn"] += 1
        save_board(board_path(args.workdir), board)

    board["pilot_import"]["red_count"] = pilot_red
    board["pilot_import"]["shortfall"] = pilot_shortfall
    board["accounting"]["kept_total"] = (board["accounting"]["kept_new"]
                                         + pilot_red)
    a = board["accounting"]
    assert a["drawn"] == (a["kept_new"] + a["equivalent"]
                          + a["build_break"] + a["other"]), \
        "reconciliation FAILED: %r" % (a,)
    save_board(board_path(args.workdir), board)
    print("drawn=%d kept_new=%d pilot_red=%d kept_total=%d equiv=%d "
          "buildbreak=%d other=%d" % (
              a["drawn"], a["kept_new"], pilot_red,
              a["kept_total"], a["equivalent"], a["build_break"],
              a["other"]))
    if a["kept_total"] != 50:
        print("TARGET UNMET: kept_total=%d (want 50). Extend --draw-k "
              "or investigate yield; board left incomplete for --resume."
              % a["kept_total"])
        return 1
    board["complete"] = True
    with open(board_path(args.workdir), "w", encoding="utf-8") as fh:
        json.dump(board, fh, indent=2)
        fh.write("\n")
    out = os.path.join(REPO, "eval", "mutant_pilot", "board.json")
    shutil.copyfile(board_path(args.workdir), out)
    print("board complete: %s" % out)
    return 0


def run_self_check(args):
    """Fire every board spec via loop.prepare_mutant_tree (frozen path)
    and byte-compare with the campaign trees. Records into board.json."""
    import loop  # noqa: E402  (frozen; used, not modified)
    from afterglow_core.verilator import load_case_file  # noqa: E402
    with open(board_path(args.workdir)) as fh:
        board = json.load(fh)
    base = board["base"]["tree"]
    passed, failed = [], []
    for kept in board["kept"]:
        spec = os.path.join(REPO, kept["spec"])
        case = load_case_file(spec)
        dest = os.path.join(args.workdir, "selfcheck-%s" % kept["mutant_id"])
        if os.path.exists(dest):
            shutil.rmtree(dest)
        try:
            loop.prepare_mutant_tree(case, base, dest)
        except Exception as e:  # noqa: BLE001 (record, don't hide)
            failed.append({"mutant_id": kept["mutant_id"],
                           "error": "%r" % e})
            continue
        with open(os.path.join(dest, kept["file"]), "rb") as fh:
            fired = fh.read()
        with open(os.path.join(kept["tree"], kept["file"]), "rb") as fh:
            camp = fh.read()
        if fired == camp:
            passed.append(kept["mutant_id"])
        else:
            failed.append({"mutant_id": kept["mutant_id"],
                           "error": "byte mismatch vs campaign tree"})
        shutil.rmtree(dest, ignore_errors=True)
    # Pilot members: fire via existing sanctioned specs (no new trees kept).
    with open(PILOT_MANIFEST, encoding="utf-8") as fh:
        pilot = json.load(fh)
    for p in pilot["pilot"]:
        spec = os.path.join(REPO, "sim", "targets",
                            "%s.yaml" % p["mutant_id"])
        case = load_case_file(spec)
        dest = os.path.join(args.workdir,
                            "selfcheck-%s" % p["mutant_id"])
        if os.path.exists(dest):
            shutil.rmtree(dest)
        try:
            loop.prepare_mutant_tree(case, base, dest)
            passed.append(p["mutant_id"])
        except Exception as e:  # noqa: BLE001
            failed.append({"mutant_id": p["mutant_id"], "error": "%r" % e})
        shutil.rmtree(dest, ignore_errors=True)
    board["self_check"] = {"ran": True, "passed": passed, "failed": failed}
    with open(board_path(args.workdir), "w", encoding="utf-8") as fh:
        json.dump(board, fh, indent=2)
        fh.write("\n")
    if not board.get("complete"):
        print("self-check: %d passed, %d failed (board incomplete)"
              % (len(passed), len(failed)))
    else:
        out = os.path.join(REPO, "eval", "mutant_pilot", "board.json")
        shutil.copyfile(board_path(args.workdir), out)
        print("self-check: %d passed, %d failed (synced to %s)"
              % (len(passed), len(failed), out))
    for f in failed:
        print("FAILED: %s" % (f,))
    return 0 if not failed else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--draw-seed", type=int, default=1234)
    ap.add_argument("--workdir", default="/tmp/mutboard")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--draw-k", type=int, default=DRAW_K)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        return run_self_check(args)
    return run_campaign(args)


if __name__ == "__main__":
    sys.exit(main())
