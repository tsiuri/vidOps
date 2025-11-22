#!/usr/bin/env bash
# Wrapper to run voice filtering scripts with auto-venv activation
# Usage: voicefil_w_venv.sh <script.py> [args...]

set -euo pipefail

VENV_DIR="$(cd "$(dirname "$0")" && pwd)/venv"
REQUIREMENTS="$(cd "$(dirname "$0")" && pwd)/requirements.txt"

# Color output
log(){ printf '\033[0;34m[venv]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[venv]\033[0m %s\n' "$*" >&2; }

# Check if venv exists and is valid
if [[ ! -d "$VENV_DIR" ]] || [[ ! -f "$VENV_DIR/bin/python" ]]; then
    warn "Virtual environment not found at: $VENV_DIR"
    warn "Creating new virtual environment..."
    python3 -m venv "$VENV_DIR"
    log "Virtual environment created"
fi

# Activate venv in a subshell so it doesn't affect parent shell
(
    source "$VENV_DIR/bin/activate"

    # Check if dependencies are installed
    if ! python -c "import torch, torchaudio, resemblyzer" >/dev/null 2>&1; then
        log "Installing dependencies from $REQUIREMENTS..."
        pip install -q --upgrade pip
        pip install -q -r "$REQUIREMENTS"
        log "Dependencies installed"
    fi

    # Run the actual Python script with all arguments
    exec python "$@"
)
