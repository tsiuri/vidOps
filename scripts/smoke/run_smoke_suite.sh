#!/usr/bin/env bash
# Run the VidOps smoke-test suite (transcription + voice/analysis flows).

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -d ".venv" ]]; then
  echo "[smoke] Python venv not found. Run 'python -m venv .venv' and install requirements first."
  exit 1
fi

source ".venv/bin/activate"

export PYTHONPATH="$PROJECT_ROOT"
export VIDOPS_WORKER_MAX_JOBS="${VIDOPS_WORKER_MAX_JOBS:-1}"
export VIDOPS_WORKER_HEARTBEAT_INTERVAL="${VIDOPS_WORKER_HEARTBEAT_INTERVAL:-2}"

pytest tests/smoke -m smoke "$@"
