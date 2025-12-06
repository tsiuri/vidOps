#!/usr/bin/env bash
#
# ensure_diarization_env.sh
# Create/verify the diarization venv with pinned versions and compatibility patches.
#
# Pinned stack:
#   torch==2.8.0+cu128
#   torchaudio==2.8.0+cu128
#   pyannote.audio==3.1.1
#   deps: tqdm pyyaml pandas webrtcvad soundfile huggingface_hub
# Guardrails:
#   - Do NOT upgrade/downgrade torch/torchaudio/pyannote.
#   - Patches live in scripts/diarization/diarize_inference.py (weights_only=False, HF token bridge, NumPy NaN, torchaudio stubs).
#   - Requires HF_TOKEN/PYANNOTE_AUTH_TOKEN for model downloads.

set -euo pipefail

TOOL_ROOT="${TOOL_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
VENV_PATH="${VENV_PATH:-$TOOL_ROOT/.venv}"

# Defaults (CUDA 12.8). Override via env if needed (e.g., CPU builds).
TORCH_VER="${TORCH_VER:-2.8.0+cu128}"
TORCHAUDIO_VER="${TORCHAUDIO_VER:-2.8.0+cu128}"
PYANNOTE_VER="${PYANNOTE_VER:-3.1.1}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL:-https://download.pytorch.org/whl/cu128}"

log() { echo "[ensure_env] $*"; }
err() { echo "[ensure_env][ERROR] $*" >&2; }

create_venv() {
  if [[ ! -x "$VENV_PATH/bin/python" ]]; then
    log "Creating venv at $VENV_PATH"
    "$PYTHON_BIN" -m venv "$VENV_PATH"
  fi
  if [[ ! -x "$VENV_PATH/bin/python" ]]; then
    err "Failed to create venv at $VENV_PATH"
    exit 1
  fi
}

install_stack() {
  log "Installing pinned torch/torchaudio"
  "$VENV_PATH/bin/pip" install --no-cache-dir \
    --extra-index-url "$PIP_EXTRA_INDEX_URL" \
    "torch==$TORCH_VER" \
    "torchaudio==$TORCHAUDIO_VER"

  log "Installing pyannote + deps"
  "$VENV_PATH/bin/pip" install --no-cache-dir \
    "pyannote.audio==$PYANNOTE_VER" \
    tqdm pyyaml pandas webrtcvad soundfile huggingface_hub
}

verify_versions() {
  "$VENV_PATH/bin/python" - <<'PY'
import sys
import torch, torchaudio
import pkg_resources

pinned = {
    "torch": "2.8.0+cu128",
    "torchaudio": "2.8.0+cu128",
    "pyannote.audio": "3.1.1",
}
actual = {
    "torch": torch.__version__,
    "torchaudio": torchaudio.__version__,
    "pyannote.audio": pkg_resources.get_distribution("pyannote.audio").version,
}
mismatch = {k: (v, actual.get(k)) for k, v in pinned.items() if actual.get(k) != v}
if mismatch:
    print("[ensure_env][ERROR] Version mismatch:", mismatch)
    sys.exit(1)
print("[ensure_env] Versions OK:", actual)
PY
}

main() {
  create_venv
  install_stack
  verify_versions
  log "Done. Activate with: source $VENV_PATH/bin/activate"
  log "HF_TOKEN/PYANNOTE_AUTH_TOKEN must be set before running diarization."
}

main "$@"
