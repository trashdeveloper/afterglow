#!/usr/bin/env bash
# Afterglow judge-proof repro: RED -> live agent -> GREEN, one command.
#   ./repro/run.sh --case picosoc-uart-div [--seed 42]
#                  [--prompt agent/prompts/v1.txt] [--run-id ID] [--rtl path]
#
# Live mode is the DEFAULT (and only) mode: requires MODEL_API_KEY in the
# environment (SPARK_API_KEY / META_API_KEY also accepted). There is no
# replay/verify mode here -- a replay log must NEVER be presented as live.
#
# Exit 0 on GREEN with  "checksum MATCH <after-sha>"
# Exit 1 on RED   with  "checksum MISMATCH ..." (BEFORE/AFTER both printed).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CASE="picosoc-uart-div"
SEED="42"
PROMPT="agent/prompts/v1.txt"
RUN_ID=""
RTL_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --case)   CASE="${2:?--case needs a value}"; shift 2;;
    --seed)   SEED="${2:?--seed needs a value}"; shift 2;;
    --prompt) PROMPT="${2:?--prompt needs a value}"; shift 2;;
    --run-id) RUN_ID="${2:?--run-id needs a value}"; shift 2;;
    --rtl)    RTL_ARGS=(--rtl "${2:?--rtl needs a value}"); shift 2;;
    -h|--help)
      echo "usage: ./repro/run.sh [--case NAME] [--seed N] [--prompt FILE] [--run-id ID] [--rtl path]"; exit 0;;
    *) echo "unknown arg: $1 (see --help)" >&2; exit 2;;
  esac
done

if [ -z "${MODEL_API_KEY:-}" ] && [ -z "${SPARK_API_KEY:-}" ] && [ -z "${META_API_KEY:-}" ]; then
  echo "FATAL: MODEL_API_KEY is required for the live agent loop (no replay mode)." >&2
  echo "  export MODEL_API_KEY=...  (SPARK_API_KEY / META_API_KEY also accepted)" >&2
  exit 2
fi
if [ ! -f "sim/targets/${CASE}.yaml" ]; then
  echo "FATAL: unknown case '${CASE}' (no sim/targets/${CASE}.yaml)." >&2
  exit 2
fi
if [ -z "$RUN_ID" ]; then
  RUN_ID="repro-${CASE}-$(date -u +%Y%m%dT%H%M%SZ)"
fi

echo "=== afterglow repro: case=${CASE} seed=${SEED} run=${RUN_ID} ==="
python3 agent/loop.py --case "$CASE" --seed "$SEED" --prompt "$PROMPT" \
  --run-id "$RUN_ID" "${RTL_ARGS[@]:-}"
LOOP_RC=$?

BEFORE_SHA="$(python3 -c "import json;print(json.load(open('runs/${RUN_ID}/artifacts/verdict_before.json')).get('checksum') or 'MISSING')" 2>/dev/null || echo MISSING)"
AFTER_SHA="$(python3 -c "import json;print(json.load(open('runs/${RUN_ID}/artifacts/verdict_after.json')).get('checksum') or 'MISSING')" 2>/dev/null || echo MISSING)"
BEFORE_TO="$(python3 -c "import json;print(json.load(open('runs/${RUN_ID}/artifacts/verdict_before.json')).get('tohost'))" 2>/dev/null || echo MISSING)"
AFTER_TO="$(python3 -c "import json;print(json.load(open('runs/${RUN_ID}/artifacts/verdict_after.json')).get('tohost'))" 2>/dev/null || echo MISSING)"
echo "BEFORE tohost=${BEFORE_TO} checksum=${BEFORE_SHA}"
echo "AFTER  tohost=${AFTER_TO} checksum=${AFTER_SHA}"
echo "bundle: runs/${RUN_ID}/"

if [ "$LOOP_RC" -eq 0 ] && [ "$AFTER_SHA" != "MISSING" ] && [ "$AFTER_SHA" != "None" ]; then
  echo "checksum MATCH ${AFTER_SHA}"
  exit 0
fi
echo "checksum MISMATCH (before=${BEFORE_SHA} after=${AFTER_SHA})" >&2
exit 1
