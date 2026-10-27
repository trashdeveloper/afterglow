#!/usr/bin/env python3
"""Mutation-board scorer -- STUB (pilot phase: aiming, not firing).

Aggregates {mutants, red_confirmed, green_fixed_unaided, cost} for the
mutation-fault campaign. Current phase: reads ONLY static pilot
artifacts (eval/mutant_pilot/manifest.json + verdicts/*.json) and any
ALREADY-EXISTING live-fire bundles (runs/mut-pilot-*/meta.json,
runs/mut-pilot-*/cost.json). It NEVER builds, sims, or calls the API.

Exit 0 with the aggregate line. Non-zero only on missing/corrupt
pilot artifacts (proof discipline: no hand-typed scores).
"""
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PILOT = os.path.join(REPO, "eval", "mutant_pilot")


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def aggregate(repo=REPO):
    pilot = os.path.join(repo, "eval", "mutant_pilot")
    manifest = _load_json(os.path.join(pilot, "manifest.json"))
    mutant_ids = [m["mutant_id"] for m in manifest["pilot"]]
    red_confirmed = 0
    for mid in mutant_ids:
        v = _load_json(os.path.join(pilot, "verdicts", mid + ".json"))
        if v.get("mutant_id") != mid:
            raise ValueError("verdict id mismatch: %s" % mid)
        if v.get("pass", True) is False:
            red_confirmed += 1
    green = 0
    cost_usd = 0.0
    for mid in mutant_ids:
        meta_p = os.path.join(repo, "runs", mid, "meta.json")
        if os.path.exists(meta_p):
            meta = _load_json(meta_p)
            if (meta.get("verdict_after") == "GREEN"
                    and meta.get("human_touches", 1) == 0):
                green += 1
        cost_p = os.path.join(repo, "runs", mid, "cost.json")
        if os.path.exists(cost_p):
            cost_usd += float(_load_json(cost_p).get("total_usd", 0.0))
    return {"mutants": len(mutant_ids),
            "red_confirmed": red_confirmed,
            "green_fixed_unaided": green,
            "cost_usd": round(cost_usd, 6) if cost_usd else "TBD (no live runs)"}


def main(argv):
    del argv
    agg = aggregate()
    print("mutation-board: mutants=%(mutants)d red_confirmed="
          "%(red_confirmed)d green_fixed_unaided=%(green_fixed_unaided)d "
          "cost_usd=%(cost_usd)s" % agg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
