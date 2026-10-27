#!/usr/bin/env bash
# Afterglow Task 6 failing check: the fresh-design acceptance record must
# exist and have shape BEFORE the acceptance run counts. TDD Step 1: this
# script MUST FAIL on a fresh tree ("ACCEPTANCE.md missing") and PASS
# only when eval/ACCEPTANCE.md logs a real fresh design (not picorv32,
# not simpleuart/picosoc) with repo + pinned commit, setup minutes
# (number, <=30 target), a triage run-id AND an --auto run-id that both
# resolve to real runs/<id>/ bundles, and an outcome (GREEN or RED --
# honesty over green: a RED outcome still passes the shape check).
#
# Usage: bash scripts/check_acceptance.sh   (exit 0 PASS, exit 1 FAIL)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MD="$ROOT/eval/ACCEPTANCE.md"

if [ ! -f "$MD" ]; then
  echo "FAIL: ACCEPTANCE.md missing ($MD not found)"
  exit 1
fi

python3 - "$MD" "$ROOT" <<'EOF'
import os
import re
import sys

md_path, root = sys.argv[1], sys.argv[2]
fails = []

with open(md_path, encoding="utf-8") as fh:
    text = fh.read()

def field(name):
    m = re.search(r"(?mi)^\s*%s\s*:\s*(\S[^\n]*?)\s*$" % re.escape(name), text)
    return m.group(1).strip() if m else ""

design = field("design")
repo = field("repo")
commit = field("commit")
setup = field("setup_minutes")
triage = field("triage_run")
auto = field("auto_run")
outcome = field("outcome").upper()

if not design:
    fails.append("missing/blank `design:` line")
elif re.search(r"picorv32|simpleuart|picosoc", design, re.I):
    fails.append("design %r is not fresh (picorv32/simpleuart/picosoc seen before)" % design)
if repo and not re.match(r"^https?://", repo):
    fails.append("repo is not an http(s) URL: %r" % repo)
else:
    if not repo:
        fails.append("missing/blank `repo:` line")
if not commit:
    fails.append("missing/blank `commit:` (pinned sha) line")
elif not re.match(r"^[0-9a-f]{7,40}$", commit):
    fails.append("commit is not a hex sha: %r" % commit)
try:
    mins = float(setup)
    if mins <= 0:
        fails.append("setup_minutes must be > 0, got %r" % setup)
    elif mins > 30:
        fails.append("setup_minutes %s exceeds the 30-minute target (log it honestly, check stays RED)" % setup)
except (TypeError, ValueError):
    fails.append("missing/non-numeric `setup_minutes:` line (got %r)" % setup)
for label, rid in (("triage_run", triage), ("auto_run", auto)):
    if not rid:
        fails.append("missing/blank `%s:` line" % label)
    elif not os.path.isfile(os.path.join(root, "runs", rid, "meta.json")):
        fails.append("%s %r has no runs/<id>/meta.json bundle" % (label, rid))
if outcome not in ("GREEN", "RED"):
    fails.append("outcome must be GREEN or RED (got %r)" % outcome)

if fails:
    print("FAIL: acceptance record invalid (%d problem(s)):" % len(fails))
    for f in fails:
        print("  - %s" % f)
    sys.exit(1)
print("PASS: acceptance record OK (design=%s setup=%smin triage=%s auto=%s outcome=%s)"
      % (design, setup, triage, auto, outcome))
EOF
