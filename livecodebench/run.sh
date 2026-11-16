#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT/framework:$ROOT/shared:${PYTHONPATH:-}"
exec python "$ROOT/run_lcb_o1.py"
