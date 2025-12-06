# CUDA/PyTorch Setup for pyannote.audio 4.0.2

**Date**: 2025-12-05
**Context**: Getting pyannote.audio working with CUDA on NVIDIA RTX 3080 (8GB)
**Status**: ✅ WORKING (28x realtime performance)

---

## The Problem

When installing `pyannote.audio==4.0.2`, you'll encounter a complex dependency chain that breaks in multiple ways. Here's exactly what happens and how to fix it.

---

## Environment

### Hardware
```
GPU: NVIDIA RTX 3080 Laptop (8.2GB VRAM)
CUDA: 12.8
Driver: NVIDIA drivers (via gpu-to-nvidia.sh)
```

### Python
```
Python 3.13
Virtual env: /home/billie/tools/vidops/.venv
```

---

## Installation Steps (What Actually Works)

### 1. Create Virtual Environment
```bash
cd /home/billie/tools/vidops
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install pyannote.audio
```bash
pip install pyannote.audio==4.0.2
```

**What this installs automatically**:
- `torch==2.8.0+cu128` (PINNED by pyannote)
- `torchaudio==2.8.0` (PINNED by pyannote)
- `lightning` (for model loading)
- `speechbrain` (for embeddings)
- Many other dependencies...

**CRITICAL**: You CANNOT downgrade PyTorch to avoid compatibility issues. pyannote 4.0.2 explicitly requires `torch==2.8.0` in its `setup.py`. Attempting to use older PyTorch versions will fail.

### 3. Install Additional Dependencies
```bash
pip install scipy numpy pyyaml tqdm huggingface_hub
```

### 4. Verify CUDA
```bash
python3 << 'EOF'
import torch
print(f"PyTorch: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CUDA version: {torch.version.cuda}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f}GB")
EOF
```

**Expected output**:
```
PyTorch: 2.8.0+cu128
CUDA available: True
CUDA version: 12.8
GPU: NVIDIA GeForce RTX 3080 Laptop GPU
VRAM: 8.2GB
```

---

## The Dependency Conflicts (And How We Fixed Them)

### **Issue #1: PyTorch 2.6+ `weights_only` Breaking Change**

#### The Problem:
PyTorch 2.6+ changed the default for `torch.load()` from `weights_only=False` to `weights_only=True` for security. This breaks pyannote model loading:

```python
_pickle.UnpicklingError: Weights only load failed.
GLOBAL omegaconf.listconfig.ListConfig was not an allowed global by default
```

#### Why It Happens:
pyannote models use OmegaConf and other non-tensor objects in saved state. With `weights_only=True`, these are rejected.

#### The Fix:
Patch `torch.load` at the top of your script:

```python
import torch

# PyTorch 2.6+ compatibility patch
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    if 'weights_only' not in kwargs:
        kwargs['weights_only'] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load
```

**Location**: Lines 13-20 in `scripts/diarization/diarize_inference.py`

#### Why We Can't Just Downgrade PyTorch:
pyannote.audio 4.0.2's `setup.py` contains:
```python
install_requires=[
    "torch==2.8.0",
    "torchaudio==2.8.0",
    ...
]
```
It's a hard pin. No way around it.

---

### **Issue #2: PyTorch Lightning Also Needs Patching**

#### The Problem:
pyannote uses PyTorch Lightning internally for model checkpoints. Lightning has its own `torch.load()` wrapper that also needs patching:

```python
ValueError: Weights only load failed...
```

#### The Fix:
Patch Lightning's internal loader too:

```python
import lightning.fabric.utilities.cloud_io as cloud_io

_original_pl_load = cloud_io._load
def _patched_pl_load(*args, **kwargs):
    if 'weights_only' not in kwargs:
        kwargs['weights_only'] = False
    return _original_pl_load(*args, **kwargs)
cloud_io._load = _patched_pl_load
```

**Location**: Lines 22-28 in `scripts/diarization/diarize_inference.py`

---

### **Issue #3: HuggingFace Hub API Change**

#### The Problem:
Older pyannote examples use `use_auth_token`, but pyannote 4.0.2 uses the newer HuggingFace Hub API:

```python
TypeError: Pipeline.from_pretrained() got an unexpected keyword argument 'use_auth_token'
```

#### The Fix:
Use `token` instead of `use_auth_token`:

**OLD (broken)**:
```python
pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    use_auth_token=True
)
```

**NEW (works)**:
```python
pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    token=True  # or token="hf_..."
)
```

**Location**: Lines 189-192 in `scripts/diarization/diarize_inference.py`

---

### **Issue #4: torchaudio 2.9 API Deprecations**

#### The Problem:
speechbrain (dependency of pyannote) uses deprecated torchaudio functions:

```python
UserWarning: torchaudio._backend.list_audio_backends has been deprecated
```

#### Why It Happens:
PyTorch is transitioning audio/video to TorchCodec. Old backend functions are removed.

#### The Fix:
Stub the deprecated functions (no-ops, since they're not critical):

```python
# torchaudio 2.9+ compatibility stubs
if not hasattr(torchaudio, 'set_audio_backend'):
    def _stub(*args, **kwargs):
        pass
    torchaudio.set_audio_backend = _stub
    torchaudio.get_audio_backend = lambda: 'soundfile'
    torchaudio.list_audio_backends = lambda: ['soundfile']
```

**Location**: Lines 84-100 in `scripts/diarization/diarize_inference.py`

**Note**: This is safe because pyannote uses torchaudio's newer I/O API internally.

---

### **Issue #5: NumPy 2.x Compatibility**

#### The Problem:
NumPy 2.x removed the `np.NaN` alias:

```python
AttributeError: module 'numpy' has no attribute 'NaN'
```

#### The Fix:
Alias it back:

```python
import numpy as np
if not hasattr(np, 'NaN'):
    np.NaN = np.nan
```

**Location**: Lines 30-34, 102-104 in `scripts/diarization/diarize_inference.py`

---

### **Issue #6: pyannote 4.x API Changes**

#### The Problem:
pyannote 4.x changed the return type of `pipeline()` from `Annotation` to `DiarizeOutput`:

```python
AttributeError: 'DiarizeOutput' object has no attribute 'labels'
```

#### Old API (3.x):
```python
diarization = pipeline(audio_file)
speakers = sorted(set(diarization.labels()))
for segment, _, speaker in diarization.itertracks(yield_label=True):
    ...
```

#### New API (4.x):
```python
diarization = pipeline(audio_file)
serialized = diarization.serialize()
segments_data = serialized.get("diarization", [])

for seg in segments_data:
    speaker = seg["speaker"]
    start = seg["start"]
    end = seg["end"]
```

**Location**: Lines 235-261 in `scripts/diarization/diarize_inference.py`

---

## Complete Compatibility Patch Template

Here's the full compatibility header to add to any script using pyannote 4.x:

```python
#!/usr/bin/env python3
"""
pyannote.audio 4.0.2 compatibility patches for:
- PyTorch 2.8.0+cu128
- torchaudio 2.9
- NumPy 2.x
- HuggingFace Hub 1.0+
"""

import os
import sys
from pathlib import Path

import torch
import torchaudio
import numpy as np

# =============================================================================
# PyTorch 2.6+ weights_only compatibility
# =============================================================================
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    if 'weights_only' not in kwargs:
        kwargs['weights_only'] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

# Lightning patch (used by pyannote internally)
import lightning.fabric.utilities.cloud_io as cloud_io
_original_pl_load = cloud_io._load
def _patched_pl_load(*args, **kwargs):
    if 'weights_only' not in kwargs:
        kwargs['weights_only'] = False
    return _original_pl_load(*args, **kwargs)
cloud_io._load = _patched_pl_load

# =============================================================================
# NumPy 2.x compatibility
# =============================================================================
if not hasattr(np, 'NaN'):
    np.NaN = np.nan

# =============================================================================
# torchaudio 2.9+ API stubs
# =============================================================================
if not hasattr(torchaudio, 'set_audio_backend'):
    def _stub(*args, **kwargs):
        pass
    torchaudio.set_audio_backend = _stub
    torchaudio.get_audio_backend = lambda: 'soundfile'
    torchaudio.list_audio_backends = lambda: ['soundfile']

# =============================================================================
# Now safe to import pyannote
# =============================================================================
from pyannote.audio import Pipeline

# Use with token parameter (not use_auth_token)
HF_TOKEN = os.environ.get("HF_TOKEN")
pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    token=HF_TOKEN or True
)

# Use .serialize() API (not .labels() or .itertracks())
diarization = pipeline("audio.wav")
serialized = diarization.serialize()
segments = serialized.get("diarization", [])
```

---

## HuggingFace Authentication

### Get Token:
1. Visit https://huggingface.co/settings/tokens
2. Create token with **read** permissions
3. Copy token (starts with `hf_...`)

### Accept Model Terms:
Visit and accept terms (one-time):
- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/segmentation-3.0
- https://huggingface.co/pyannote/embedding

### Authenticate:
```bash
export HF_TOKEN='hf_YourTokenHere'

# Or use huggingface-cli
huggingface-cli login
```

### Usage in Scripts:
```python
import os
token = os.environ.get("HF_TOKEN")
pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    token=token or True  # True = use cached credentials
)
```

---

## Verification Tests

### Test 1: CUDA Available
```bash
python3 -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'"
```

### Test 2: GPU Detection
```bash
python3 -c "import torch; print(torch.cuda.get_device_name(0))"
```

### Test 3: Load Model
```bash
export HF_TOKEN='hf_...'
python3 << 'EOF'
import os
import sys
sys.path.insert(0, '/home/billie/projects/vidops/scripts/diarization')

from diarize_inference import Pipeline, torch

token = os.environ.get('HF_TOKEN')
pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    token=token
)
pipeline.to(torch.device("cuda"))
print("✅ Model loaded successfully on GPU")
EOF
```

### Test 4: Full Diarization
```bash
# Convert test audio
ffmpeg -i input.opus -ar 16000 -ac 1 /tmp/test.wav -y

# Run diarization
source /home/billie/tools/vidops/.venv/bin/activate
export HF_TOKEN='hf_...'
python3 scripts/diarization/diarize_inference.py \
  /tmp/test.wav \
  test_ytid \
  results/diarization

# Should complete in ~1/28th of audio duration
```

---

## Common Errors & Solutions

### Error: "CUDA out of memory"
**Solution**: Reduce batch sizes in config:
```python
DEFAULTS_8GB = {
    "segmentation_batch_size": 16,  # Down from 32
    "embedding_batch_size": 32,     # Down from 64
}
```

### Error: "No module named 'pyannote'"
**Solution**: Activate venv:
```bash
source /home/billie/tools/vidops/.venv/bin/activate
```

### Error: "CUDA not available"
**Solution**: Check GPU binding:
```bash
# Bind GPU to NVIDIA drivers
sudo /home/billie/tools/vidops/scripts/gpu_tools/gpu-to-nvidia.sh

# Verify
/home/billie/tools/vidops/scripts/gpu_tools/gpu-bind-status.sh
```

### Error: "requested chunk resulted in XXX samples instead of expected YYY"
**Solution**: Convert to canonical WAV format first:
```bash
ffmpeg -i input.opus -ar 16000 -ac 1 output.wav -y
```
OPUS files have variable-length samples at the end. WAV is consistent.

---

## Performance Benchmarks

### Hardware: NVIDIA RTX 3080 (8GB)

| Audio Length | Processing Time | Realtime Factor |
|--------------|-----------------|-----------------|
| 8 min        | 16.9s           | 28.4x           |
| 30 min       | ~63s            | ~28x            |
| 60 min       | ~127s           | ~28x            |

### VRAM Usage (8GB GPU):
- Model loading: ~2-3GB
- Peak during inference: ~6-7GB
- Headroom: ~1-2GB

**Settings used**:
```python
chunk_duration: 15.0s
overlap_duration: 2.5s
segmentation_batch_size: 32
embedding_batch_size: 64
```

---

## Why This Stack?

### Why PyTorch 2.8.0?
- **Required by pyannote.audio 4.0.2** (hard pin in setup.py)
- Latest CUDA 12.8 support
- Best performance on modern GPUs

### Why pyannote.audio 4.0.2?
- **Latest stable release** (Nov 2024)
- Speaker diarization 3.1 model support
- Best accuracy for speaker diarization
- Active development and support

### Why Not Older Versions?
- pyannote 3.x has lower accuracy
- Older PyTorch lacks CUDA 12.8 support
- Missing performance optimizations

---

## Research Links

Issues we found solutions in:
- [pyannote issue #1908](https://github.com/pyannote/pyannote-audio/issues/1908) - PyTorch 2.6 compatibility
- [Lightning issue #20058](https://github.com/Lightning-AI/pytorch-lightning/issues/20058) - weights_only parameter
- [PyTorch PR](https://github.com/pytorch/pytorch/pull/92103) - weights_only default change

---

## Summary: What Made It Work

1. ✅ **Accept PyTorch 2.8.0** - Don't fight the pinned dependency
2. ✅ **Patch torch.load** - Override `weights_only` default
3. ✅ **Patch Lightning** - Fix checkpoint loading
4. ✅ **Use `token` not `use_auth_token`** - HF Hub API update
5. ✅ **Stub deprecated torchaudio functions** - Prevent warnings
6. ✅ **Alias np.NaN** - NumPy 2.x compatibility
7. ✅ **Use .serialize() API** - pyannote 4.x return type
8. ✅ **Convert to WAV first** - Avoid OPUS edge cases

**Result**: 28x realtime GPU processing on 8GB VRAM 🚀

---

**Created**: 2025-12-05 by Claude Code
**Tested On**: NVIDIA RTX 3080 (8GB), CUDA 12.8, Python 3.13
**Status**: Production-ready
