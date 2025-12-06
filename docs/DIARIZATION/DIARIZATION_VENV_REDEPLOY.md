# Diarization Venv Redeploy Guide

Reference: `docs/DIARIZATION/CUDA_PYTORCH_SETUP.md` (patch details from the upstream repo).

## What we need
- Pinned stack: `torch 2.8.0+cu128`, `torchaudio 2.8.0+cu128`, `pyannote.audio 4.0.2` (CPU fallback available via `--cpu`; still uses the same PyTorch pins).
- Keep compatibility patches from the upstream repo (`scripts/diarization/diarize_inference.py` there contains weights_only=False, HF token bridge, NumPy NaN, torchaudio stubs). If you copy that pipeline here, keep those patches intact. Do **not** upgrade/downgrade these packages.
- HF_TOKEN/PYANNOTE_AUTH_TOKEN must be set for model downloads.
- Defaults assume CUDA 12.8. CPU fallback available.

## One-shot setup
Use `scripts/setup_diarization_venv.sh` to create/refresh the venv and avoid re-downloading if pins are already present.

- CUDA (default):  
  `./scripts/setup_diarization_venv.sh`

- CPU fallback:  
  `./scripts/setup_diarization_venv.sh --cpu`

- Force reinstall (ignore existing pins):  
  `./scripts/setup_diarization_venv.sh --force`

- Build only, no shell:  
  `./scripts/setup_diarization_venv.sh --no-shell`

What it does:
1. Creates `.venv` under the current TOOL_ROOT (default: repo root).
2. Checks installed torch/torchaudio/pyannote against the pins; skips install if matching (unless `--force`).
3. Installs the pinned wheels from the PyTorch cu128 index (or CPU index with `--cpu`) plus deps (tqdm, pyyaml, pandas, webrtcvad, soundfile, huggingface_hub).
4. Verifies versions. By default, drops you into an activated shell when done (skip with `--no-shell`).
5. Honors `TMPDIR` if you need to put build/download scratch space somewhere with more room than `/tmp`.

## GPU/CUDA notes
- Target CUDA: 12.8 (matching the cu128 wheels).
- Ensure NVIDIA drivers match; otherwise, use `--cpu` or install cu128 wheels on a machine with the correct driver stack.

## Path/env expectations
- TOOL_ROOT should point to the repo with `scripts/diarization/diarize_inference.py` (patches). Default is the parent of `scripts/`; if unset the worker now falls back to PROJECT_ROOT/workspace_root to avoid hardcoded paths.
- PROJECT_ROOT should point to the runtime workspace (e.g., `/home/billie/tools/vidops`) when running workers; set `DIAR_PYTHON_BIN` to the venv python for workers if needed.
- Do not upgrade torch/torchaudio/pyannote; rerun the setup script if the venv drifts.

## Quick sanity checks
```bash
source .venv/bin/activate
python - <<'PY'
import torch, torchaudio
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("torchaudio", torchaudio.__version__)
PY
python scripts/diarization/batch_diarize.py --help >/dev/null
```

If versions mismatch, rerun with `--force` (ensure enough disk space for cu128 wheels).
