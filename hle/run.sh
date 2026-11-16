#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT/framework:$ROOT/shared:${PYTHONPATH:-}"
case "${1:-hle}" in
 hle) exec python "$ROOT/run_hle_o1.py" ;;
 judge) exec python "$ROOT/hle_judge.py" ;;
 *) echo 'Usage: bash run.sh [hle|judge]'; exit 2 ;;
esac
