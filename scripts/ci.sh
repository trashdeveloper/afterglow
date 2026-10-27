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
    PREFILES="$(git log --before="2026-10-27" --name-only --pretty=format: -- . \
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

if [ "$FAIL" -eq 0 ]; then say "CI GATE: ALL PASS"; else say "CI GATE: RED"; fi
exit "$FAIL"
