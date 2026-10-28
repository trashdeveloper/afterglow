#!/usr/bin/env bash
# Afterglow pre-submit gate (AGENTS.md section 11).
# Fails loudly on the first broken invariant; exit 0 only if ALL pass.
# Checks: git window + no-weft-copy + forbidden-strings + collector caps +
# py_compile + prompt hashes.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
FAIL=0
say()  { printf '%s\n' "$*"; }
pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*"; FAIL=1; }

# 1. git window check: only PLAN.md + AGENTS.md may predate Oct 27 2026.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git rev-parse HEAD >/dev/null 2>&1; then
    # NOTE: bare --before="2026-10-27" matches the whole day in approxidate,
    # false-positiving window commits. Pin to midnight UTC explicitly.
    PREFILES="$(git log --before="2026-10-27 00:00:00 +0000" --name-only --pretty=format: -- . \
      | sort -u | grep -v -e '^$' -e '^PLAN\.md$' -e '^AGENTS\.md$' || true)"
    if [ -n "$PREFILES" ]; then
      fail "pre-window files present (only PLAN.md + AGENTS.md allowed): $PREFILES"
    else
      pass "git window clean (all implementation commits Oct 27 - Nov 4)"
    fi
  else
    say "SKIP: git repo has no commits yet (window check n/a)"
  fi
else
  say "SKIP: not a git repo (window check n/a)"
fi

# 2. Foundation split: weft is referenced, never copied (AGENTS.md section 7).
if [ -e "$ROOT/weft" ]; then
  fail "weft/ directory copied into repo; use foundation.pin reference only"
else
  WEFTCOPIES="$(find "$ROOT" -iname '*weft*' -not -path "$ROOT/.git/*" 2>/dev/null || true)"
  if [ -n "$WEFTCOPIES" ]; then
    fail "weft-named files copied into repo: $WEFTCOPIES"
  else
    pass "no weft files copied"
  fi
fi

# 3. Forbidden-strings scan over rtl/ + runs/ diffs ONLY.
#    agent/guard.py holds the pattern *definitions* and is never scanned.
PAT='tohost.*=.*1|PASS.*force|checksum.*hardcode|if.*test.*pass'
HITS=""
if [ -d "$ROOT/rtl" ]; then
  HITS="$HITS$(grep -rInE "$PAT" "$ROOT/rtl" 2>/dev/null || true)"
fi
if [ -d "$ROOT/runs" ]; then
  HITS="$HITS$(grep -rInE "$PAT" "$ROOT/runs" --include='patch.diff' 2>/dev/null || true)"
fi
if [ -n "$HITS" ]; then
  fail "forbidden strings found in rtl/runs diffs:"; printf '%s\n' "$HITS"
else
  pass "forbidden-strings scan clean (rtl/ + runs/patch.diff)"
fi

# 4. Collector caps: constants + enforced windowing + token assertion.
python3 - <<'EOF'
import sys
sys.path.insert(0, "agent")
import collect
assert collect.MAX_TRACE_LINES == 200, "MAX_TRACE_LINES != 200"
assert collect.MAX_PROMPT_TOKENS == 2500, "MAX_PROMPT_TOKENS != 2500"
lines = [f"cycle {i} fetch ok" for i in range(1000)]
lines[800] = "MISCLASS priv bit wrong"
lines[900] = "TRAP: mstatus.MPP fault"
lines[950] = "tohost: FAIL"
out = collect.collect("\n".join(lines), '{"verdict":"fail"}', "fix it\n{{TRACE}}\n{{VERDICT}}")
assert out["counts"]["trace_lines"] <= 200, out["counts"]
assert out["counts"]["prompt_tokens"] <= 2500, out["counts"]
assert "TRAP:" in out["trace_block"] and "tohost:" in out["trace_block"], "tail missing"
try:
    collect.collect("x", "{}", "word " * 3000)
except AssertionError:
    pass
else:
    sys.exit("token cap not enforced")
print("collector caps OK:", out["counts"])
EOF
if [ $? -ne 0 ]; then fail "collector caps assert failed"; else pass "collector caps enforced (200 lines / 2500 tokens)"; fi

# 5. Compile all owned Python: guard + collect required, loop/sim if present.
for f in agent/guard.py agent/collect.py; do
  if [ ! -f "$ROOT/$f" ]; then fail "missing required file: $f"; fi
done
for f in agent/guard.py agent/collect.py agent/loop.py agent/report.py agent/visual.py sim/sim.py; do
  if [ -f "$ROOT/$f" ]; then
    if python3 -m py_compile "$ROOT/$f"; then pass "py_compile $f"; else fail "py_compile $f"; fi
  else
    say "SKIP: $f not present (parallel agent owns it?)"
  fi
done

# 6. Prompt hashes: every versioned prompt hashed + logged.
if ls "$ROOT"/agent/prompts/v*.txt >/dev/null 2>&1; then
  sha256sum "$ROOT"/agent/prompts/v*.txt
  pass "prompt hashes logged"
else
  fail "no versioned prompts in agent/prompts/"
fi

# 7. Eval board green: score.py regenerates locked 3/3 + full 8/8 from runs/ only.
if python3 eval/score.py >/dev/null 2>&1; then
  pass "eval/score.py green (locked 3/3, full 8/8 from runs/ bundles)"
else
  fail "eval/score.py not green"
fi

# 8. Bundle completeness: every locked board row's bundle holds all 13 proof files.
python3 - <<'EOF'
import re, os, sys
rows = open("eval/results.md").read()
run_ids = re.findall(r"\|\s*(?:picosoc-uart-div|picorv32-fetch-misalign|uart-tx-div|uart-start-half|fetch-highword|rdata-latch|trap-stuck|pc-incr)\s*\|\s*locked\s*\|\s*GREEN\s*\|\s*(\S+)", rows)
assert len(run_ids) >= 3, f"fewer than 3 locked GREEN rows: {run_ids}"
required = ["artifacts/input_trace.txt", "artifacts/boot.log",
  "artifacts/boot_after.log", "artifacts/verdict_before.json",
  "artifacts/verdict_after.json", "api/text_request.json",
  "api/text_response.json", "api/image_request.json",
  "api/image_response.json", "explain.png", "patch.diff",
  "report.md", "meta.json"]
missing = []
for rid in run_ids:
    for f in required:
        p = os.path.join("runs", rid, f)
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            missing.append(p)
if missing:
    sys.exit("incomplete bundles:\n" + "\n".join(missing))
print(f"bundle completeness OK: {len(run_ids)} locked bundles x 13 files")
EOF
if [ $? -ne 0 ]; then fail "bundle completeness check failed"; else pass "locked bundles complete (13 files each)"; fi

# 9. Repro contract probe (no API needed): --help exits 0, unknown flag exits 2.
if ./repro/run.sh --help >/dev/null 2>&1; then pass "repro --help exit 0"; else fail "repro --help contract broken"; fi
if ./repro/run.sh --bogus-flag >/dev/null 2>&1; then fail "repro unknown-flag should exit 2"; else
  if [ $? -eq 2 ]; then pass "repro unknown-flag exit 2"; else fail "repro unknown-flag exit $? (want 2)"; fi
fi

# 10. Tracked-dirt gate: scratch, caches, secrets must never be committed.
DIRTY="$(git ls-files | grep -e 'runs/scratch/' -e '__pycache__' -e '\.pyc$' -e '(^|/)\.env$' -e '\.key$' || true)"
if [ -n "$DIRTY" ]; then fail "tracked dirt (scratch/caches/secrets): $DIRTY"; else pass "no tracked scratch/caches/secrets"; fi

if [ "$FAIL" -eq 0 ]; then say "CI GATE: ALL PASS"; else say "CI GATE: RED"; fi
exit "$FAIL"
