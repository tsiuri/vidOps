#!/usr/bin/env bash
set -euo pipefail

TUNNEL_PORT=8443
SERVER_HOST="192.168.0.187"
SSH_USER="billie"

# Start tunnel if it's not running
if ! lsof -i :${TUNNEL_PORT} >/dev/null 2>&1; then
  echo "Starting SSH tunnel on port ${TUNNEL_PORT}..."
  ssh -f -N -L ${TUNNEL_PORT}:127.0.0.1:8443 ${SSH_USER}@${SERVER_HOST}
  sleep 1
fi

source .venv/bin/activate
PYTHONPATH=$(pwd) "$@"
