#!/usr/bin/env bash
# H10: 100-mutant nightly mining (controller-authored, new seeds).
# Controller-run sim only, no API. Resumable; takes hours.
#   bash eval/mine_100.sh            # full campaign
#   bash eval/mine_100.sh --resume   # resume after interruption
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 eval/mine100.py --workdir /tmp/mutboard100 "$@"
