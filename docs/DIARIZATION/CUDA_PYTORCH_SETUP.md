# CUDA/PyTorch Setup (pyannote) — Redeploy Notes

This is an adapted, shorter copy of the upstream guide at `/home/billie/projects/vidops/docs/DIARIZATION/CUDA_PYTORCH_SETUP.md`. The upstream write‑up was produced while getting `pyannote.audio` working on CUDA with PyTorch 2.8.0; the compatibility notes still apply here even though the local pinned stack is `torch 2.8.0+cu128`, `torchaudio 2.8.0+cu128`, `pyannote.audio 3.1.1`.

## Hardware / environment
- Target CUDA: 12.8 (cu128 wheels).
- GPU: RTX 30‑series (tested on 3080‑class).
- Python 3.13 in a venv at `.venv`.
- HF_TOKEN / PYANNOTE_AUTH_TOKEN must be set before running diarization so models download.

## One‑shot install (preferred)
Run the helper script (skips downloads if pins already present):
```bash
# default CUDA
./scripts/setup_diarization_venv.sh

# CPU build if CUDA stack is unavailable
./scripts/setup_diarization_venv.sh --cpu

# use custom temp space if /tmp is small
TMPDIR=$PWD/tmp/pip_tmp ./scripts/setup_diarization_venv.sh --no-shell
```
- Uses PyTorch cu128 index, pins torch/torchaudio/pyannote, installs aux deps.
- Checks versions first; no re-download when pins match (unless `--force`).
- Set `--no-shell` to avoid dropping into an interactive venv shell after setup.

## Why these pins
- `pyannote.audio` depends on the PyTorch 2.8 line; downgrading breaks installs.
- The diarization pipeline carries compatibility patches (weights_only=False, HF token bridge, torchaudio stubs, NaN guards). Do **not** “upgrade to latest” without re-testing the patches.

## Sanity checks
```bash
source .venv/bin/activate
python - <<'PY'
import torch, torchaudio
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("torchaudio", torchaudio.__version__)
PY
```
- Expect torch/torchaudio `2.8.0+cu128` (or `+cpu` if built with `--cpu`).
- If mismatch, rerun the setup script with `--force` after ensuring disk space.

## Operational reminders
- Keep `TMPDIR` pointed at a spacious path to avoid quota issues while downloading the cu128 wheels.
- Do not remove or rewrite the patched diarization code from the upstream repo; the venv pins assume those patches are present.
- When deploying workers, set `TOOL_ROOT`/`PROJECT_ROOT` and point `DIAR_PYTHON_BIN` at `.venv/bin/python` so the pinned stack is used end‑to‑end.
