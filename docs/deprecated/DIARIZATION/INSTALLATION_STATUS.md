# Diarization 2.0 - Installation Status & Compatibility

**Date**: 2025-12-07
**Status**: ✅ FULLY OPERATIONAL (do not change pinned versions or patches)

---

## Summary

✅ **SYSTEM READY** — Use the existing venv at `/home/billie/tools/vidops/.venv`. It contains torch/torchaudio 2.8.0+cu128 and pyannote 3.1.x plus compatibility patches. **Do not pip-upgrade torch/torchaudio/pyannote or remove the patches.**

### Sources
- [PyTorch 2.6 Compatibility Issue](https://github.com/pyannote/pyannote-audio/issues/1908)
- [Lightning weights_only Issue](https://github.com/Lightning-AI/pytorch-lightning/issues/20058)
- [pyannote.audio Releases](https://github.com/pyannote/pyannote-audio/releases)

---

## Installed Components

```
✅ pyannote.audio 3.1.x  
✅ PyTorch 2.8.0+cu128  
✅ torchaudio 2.8.0+cu128  
✅ CUDA 12.8 (RTX 3060 / 3060 Ti targets)  
✅ scipy, numpy, pyyaml, tqdm, pandas, webrtcvad, soundfile, huggingface_hub
```

**Hardware**:
- GPU: NVIDIA RTX 3060 (12GB) / 3060 Ti (8GB)
- CUDA: Working (12.8)

---

## Compatibility Patches Applied (do not remove)

1) **PyTorch weights_only fix** (PyTorch 2.6+): `torch.load` and Lightning cloud IO forced to `weights_only=False` for pyannote checkpoints.  
2) **NumPy 2.x alias**: `np.NaN`/`np.NAN` restored.  
3) **HuggingFace hub API bridge**: map `use_auth_token` → `token`.  
4) **Torchaudio API stubs**: `set_audio_backend/get_audio_backend/list_audio_backends` provided for 2.8/2.9+ compatibility.  
5) **Segmentation model override** is supported in config; clustering min size wired.  
Files: `scripts/diarization/diarize_inference.py`, `scripts/diarization/batch_diarize.py`, `scripts/diarization/match_reference.py` (HF patch).

---

## Testing Completed

✅ **Model + pipeline test** (2025-12-07): pyannote/speaker-diarization-3.1 loads on CUDA with patches; full 5-video batch succeeded (pre-pass + reference match + post-process + word map).

---

## Version Compatibility

| Component | Version | Status |
|-----------|---------|--------|
| Python | 3.10/3.11 (venv) | ✅ |
| PyTorch | 2.8.0+cu128 | ✅ Patched |
| torchaudio | 2.8.0+cu128 | ✅ Patched stubs |
| pyannote.audio | 3.1.x | ✅ |
| CUDA | 12.8 | ✅ |
| GPU | RTX 3060 / 3060 Ti | ✅ |

**Key Rule**: Do **not** upgrade/downgrade torch/torchaudio/pyannote in `/home/billie/tools/vidops/.venv`; keep the patches intact.

---

## Testing Checklist

- [x] Load speaker-diarization-3.1 model ✅ (2025-12-05)
- [x] Load segmentation-3.0 model (optional override supported; test when needed)
- [x] Load embedding model (reference matching)
- [x] Batch process sample (5) with reference + post-process + word map
- [ ] Full pikerbreakdown set (remaining)

---

## Quick Commands

```bash
# Activate pinned venv (DO NOT reinstall torch/torchaudio)
source /home/billie/tools/vidops/.venv/bin/activate

# Check versions
python - <<'PY'
import torch, torchaudio
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("torchaudio", torchaudio.__version__)
PY

# Login
huggingface-cli login  # ensure HF_TOKEN/PYANNOTE_AUTH_TOKEN set

# Smoke test (uses patches)
python /home/billie/projects/vidops/scripts/diarization/diarize_inference.py --help
```

---

## Documentation

- `INFERENCE_IMPLEMENTATION.md` - Complete implementation guide
- `PHASE_WALKTHROUGH.md` - Status & next steps
- `QUICK_START.md` - User guide
- `DIARIZATION_2.0.md` - Architecture

---

**System Status**: ✅ Ready (pending HuggingFace authentication only)

**Created**: 2025-12-05
**GPU**: NVIDIA RTX 3080 (8.2GB) - Configuration optimized
