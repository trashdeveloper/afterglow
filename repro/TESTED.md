# Cold-run log

- 2026-10-26: author self-run live-20261026T174854Z-picosoc-uart-div GREEN 92.6s (local, not Docker).
- 2026-10-31: repro script validated locally (no daemon on host, so Docker
  image build not run here): `./repro/run.sh --case picosoc-uart-div
  --seed 44` → GREEN model-verbatim 4-line, BEFORE FAIL 0xdead
  (27ee482b) → AFTER PASS (5c8f58d0 MATCH golden), bundle
  runs/repro-picosoc-uart-div-20261031T145922Z/. Fail-loud without key
  also verified (exit 2, no replay). Dockerfile updated to current tree
  (afterglow CLI, afterglow_core, tests, root pins).
- TODO: non-author MUST run `docker build -t afterglow . && docker run
  -e MODEL_API_KEY=... afterglow --case picosoc-uart-div` cold <10min,
  log run-id here.
