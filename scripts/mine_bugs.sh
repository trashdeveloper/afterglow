#!/usr/bin/env bash
# Afterglow Task 5 failing check: the stranger-bug set must exist and
# have shape BEFORE any live loop runs. TDD Step 1: this script MUST
# FAIL on a fresh tree ("stranger.yaml missing") and PASS only when
# eval/stranger.yaml lists >=3 REAL historical bugs, each with
# repo + commit + upstream_author/date/subject + ground-truth fix +
# manual baseline + baseline_source + runnable proxy (local_spec) +
# proxy_rationale + quarantine.
#
# Usage: bash scripts/mine_bugs.sh   (exit 0 PASS, exit 1 FAIL)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YAML="$ROOT/eval/stranger.yaml"

if [ ! -f "$YAML" ]; then
  echo "FAIL: stranger.yaml missing ($YAML not found)"
  exit 1
fi

python3 - "$YAML" "$ROOT" <<'EOF'
import os
import re
import sys

yaml_path, root = sys.argv[1], sys.argv[2]
fails = []

# Minimal flat-YAML reader with `key: |` literal-block support
# (stdlib only; same line discipline as sim target YAMLs).
entries = []
cur = None
lit_key = None
lit_indent = None
with open(yaml_path, encoding="utf-8") as fh:
    raw = fh.read().splitlines()
for ln in raw:
    if lit_key is not None:
        m = re.match(r"^(\s*)\S", ln)
        if ln.strip() == "" or (m and len(m.group(1)) > lit_indent):
            cur[lit_key] += ln.strip() + "\n" if ln.strip() else "\n"
            continue
        lit_key = None
    s = ln.strip()
    if not s or s.startswith("#"):
        continue
    m = re.match(r"-\s*id:\s*(\S+)\s*$", s)
    if m:
        if cur and "id" in cur:
            entries.append(cur)
        cur = {"id": m.group(1)}
        continue
    m = re.match(r"([A-Za-z_]+):\s*(\|)?\s*(.*)$", s)
    if m and cur is not None:
        key, pipe, val = m.group(1), m.group(2), m.group(3)
        if pipe:
            cur[key] = ""
            lit_key = key
            lit_indent = len(ln) - len(ln.lstrip())
        else:
            v = val.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            cur[key] = v
if cur and "id" in cur:
    entries.append(cur)

REQUIRED = ("id", "repo", "commit", "upstream_author",
            "upstream_date", "upstream_subject", "file", "bug_desc",
            "ground_truth_fix", "manual_baseline_hours", "baseline_source",
            "local_spec", "proxy_rationale", "quarantine")

if len(entries) < 3:
    fails.append("need >=3 stranger bugs, found %d" % len(entries))

seen = set()
for e in entries:
    sid = e.get("id", "?")
    if sid in seen:
        fails.append("%s: duplicate id" % sid)
    seen.add(sid)
    for k in REQUIRED:
        if not (e.get(k) or "").strip():
            fails.append("%s: missing/blank required field %r" % (sid, k))
    repo = e.get("repo", "")
    if repo and not re.match(r"^https?://", repo):
        fails.append("%s: repo is not an http(s) URL: %r" % (sid, repo))
    commit = e.get("commit", "")
    if commit and not re.match(r"^[0-9a-f]{40}$", commit):
        fails.append("%s: commit is not a 40-hex sha: %r" % (sid, commit))
    try:
        hrs = float(e.get("manual_baseline_hours", ""))
        if hrs <= 0:
            fails.append("%s: manual_baseline_hours must be > 0" % sid)
    except (TypeError, ValueError):
        fails.append("%s: manual_baseline_hours is not a number" % sid)
    fix = e.get("ground_truth_fix", "")
    if fix and len(fix.strip().splitlines()) < 2:
        fails.append("%s: ground_truth_fix must carry the real diff "
                     "(>=2 lines)" % sid)
    spec = e.get("local_spec", "")
    if spec and not os.path.isfile(os.path.join(root, spec)):
        fails.append("%s: local_spec not found in tree: %r" % (sid, spec))

if fails:
    print("FAIL: stranger set invalid (%d problem(s)):" % len(fails))
    for f in fails:
        print("  - %s" % f)
    sys.exit(1)
print("PASS: stranger set OK (%d bugs: %s)"
      % (len(entries), ", ".join(sorted(seen))))
EOF
