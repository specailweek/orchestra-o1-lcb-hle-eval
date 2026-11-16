#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT/framework:$ROOT/shared:${PYTHONPATH:-}"
case "${1:-}" in
 lcb) exec python "$ROOT/livecodebench/run_lcb_o1.py" ;;
 hle) exec python "$ROOT/hle/run_hle_o1.py" ;;
 judge) exec python "$ROOT/hle/hle_judge.py" ;;
 *) echo 'Usage: bash run.sh {lcb|hle|judge}'; exit 2 ;;
esac
