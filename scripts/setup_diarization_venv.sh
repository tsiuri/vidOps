#!/usr/bin/env bash
#
# setup_diarization_venv.sh
# One-shot venv setup for diarization with pinned PyTorch/torchaudio/pyannote.
# Defaults to CUDA 12.8 wheels; use --cpu to install CPU wheels.
# Flags:
#   --cpu        Use CPU wheels (torch/torchaudio +cpu, no extra index)
#   --force      Reinstall even if pinned versions are already present
#   --no-shell   Do not drop into an activated shell after setup
#

set -euo pipefail

USE_CPU=false
FORCE=false
LAUNCH_SHELL=true
for arg in "$@"; do
  case "$arg" in
    --cpu) USE_CPU=true ;;
    --force) FORCE=true ;;
    --no-shell) LAUNCH_SHELL=false ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

TOOL_ROOT="${TOOL_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
VENV_PATH="${VENV_PATH:-$TOOL_ROOT/.venv}"

if $USE_CPU; then
  TORCH_VER="2.8.0+cpu"
  TORCHAUDIO_VER="2.8.0+cpu"
  PIP_EXTRA_INDEX_URL=""
else
  TORCH_VER="2.8.0+cu128"
  TORCHAUDIO_VER="2.8.0+cu128"
  PIP_EXTRA_INDEX_URL="https://download.pytorch.org/whl/cu128"
fi
PYANNOTE_VER="4.0.2"
HF_TOKEN_SET="${HF_TOKEN:+yes}"
PYANNOTE_TOKEN_SET="${PYANNOTE_AUTH_TOKEN:+yes}"

log() { echo "[setup_venv] $*"; }
err() { echo "[setup_venv][ERROR] $*" >&2; }

log "Using TOOL_ROOT=$TOOL_ROOT"
log "Target venv: $VENV_PATH"
log "Torch: $TORCH_VER  Torchaudio: $TORCHAUDIO_VER  Pyannote: $PYANNOTE_VER"

# Create venv if missing
if [[ ! -x "$VENV_PATH/bin/python" ]]; then
  log "Creating venv..."
  python3 -m venv "$VENV_PATH"
fi
if [[ ! -x "$VENV_PATH/bin/python" ]]; then
  err "Failed to create venv at $VENV_PATH"
  exit 1
fi

# Check existing versions
NEED_INSTALL=0
if ! $FORCE; then
  if "$VENV_PATH/bin/python" - <<PY; then
from importlib import metadata
pinned = {"torch": "$TORCH_VER", "torchaudio": "$TORCHAUDIO_VER", "pyannote.audio": "$PYANNOTE_VER"}
for pkg, pin in pinned.items():
    try:
        ver = metadata.version(pkg)
    except metadata.PackageNotFoundError:
        raise SystemExit(1)
    if ver != pin:
        raise SystemExit(1)
print("[setup_venv] Versions already pinned.")
PY
    NEED_INSTALL=0
  else
    NEED_INSTALL=1
  fi
else
  NEED_INSTALL=1
fi

# Install pins if needed
if [[ $NEED_INSTALL -eq 1 ]]; then
  log "Installing pinned torch/torchaudio..."
  if [[ -n "$PIP_EXTRA_INDEX_URL" ]]; then
    "$VENV_PATH/bin/pip" install --no-cache-dir \
      --extra-index-url "$PIP_EXTRA_INDEX_URL" \
      "torch==$TORCH_VER" \
      "torchaudio==$TORCHAUDIO_VER"
  else
    "$VENV_PATH/bin/pip" install --no-cache-dir \
      "torch==$TORCH_VER" \
      "torchaudio==$TORCHAUDIO_VER"
  fi

  log "Installing pyannote + deps..."
  "$VENV_PATH/bin/pip" install --no-cache-dir \
    "pyannote.audio==$PYANNOTE_VER" \
    tqdm pyyaml pandas webrtcvad soundfile huggingface_hub
fi

# Ensure CLI/db helpers used by vo_cli.py are present (lightweight, no effect if already installed)
MISSING_EXTRAS=$("$VENV_PATH/bin/python" - <<'PY'
from importlib import metadata
extras = ["psycopg2-binary", "click"]
missing = []
for pkg in extras:
    try:
        metadata.version(pkg)
    except metadata.PackageNotFoundError:
        missing.append(pkg)
print(" ".join(missing))
PY
)
if [[ -n "$MISSING_EXTRAS" ]]; then
  log "Installing extra CLI deps: $MISSING_EXTRAS"
  "$VENV_PATH/bin/pip" install --no-cache-dir $MISSING_EXTRAS
fi

# Install broader app deps (includes faster-whisper, db deps, etc.) if requirements.txt is present
if [[ -f "$TOOL_ROOT/requirements.txt" ]]; then
  log "Ensuring application requirements (requirements.txt)..."
  "$VENV_PATH/bin/pip" install --no-cache-dir -r "$TOOL_ROOT/requirements.txt"
fi

# Final verification
if [[ $NEED_INSTALL -eq 0 ]]; then
  log "Pinned versions already present; skipped installs."
fi

"$VENV_PATH/bin/python" - <<PY
from importlib import metadata
pinned = {"torch": "$TORCH_VER", "torchaudio": "$TORCHAUDIO_VER", "pyannote.audio": "$PYANNOTE_VER"}
actual = {}
for pkg in pinned:
    try:
        actual[pkg] = metadata.version(pkg)
    except metadata.PackageNotFoundError:
        actual[pkg] = None
mismatch = {k: (pinned[k], actual.get(k)) for k in pinned if actual.get(k) != pinned[k]}
if mismatch:
    print("[setup_venv][WARN] Version mismatch:", mismatch)
else:
    print("[setup_venv] Versions OK:", actual)
PY

log "Done. HF_TOKEN/PYANNOTE_AUTH_TOKEN must be set before running diarization."
if [[ -z "$HF_TOKEN_SET" && -z "$PYANNOTE_TOKEN_SET" ]]; then
  log "Warning: HF_TOKEN/PYANNOTE_AUTH_TOKEN not set; required later for model downloads."
fi

if $LAUNCH_SHELL; then
  log "Dropping into a shell with venv activated..."
  # shellcheck disable=SC1090
  source "$VENV_PATH/bin/activate"
  exec "${SHELL:-/bin/bash}"
fi
