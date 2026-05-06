# Diarization 2.0 - Quick Start Guide

**Status**: End-to-end wired (pre-pass + diarization + reference match + post-process + word map) from a single command.
**GPU**: Optimized for 8GB VRAM (tested on CUDA 12.8, RTX 3060/3060 Ti)
**Venv**: Use the prebuilt `/home/billie/tools/vidops/.venv` (torch/torchaudio 2.8.0+cu128 with compatibility patches). Do **not** upgrade torch/torchaudio/pyannote.
**Date**: 2025-12-07

---

## Environment & Installation (do not change versions)

- Activate the pinned venv: `source /home/billie/tools/vidops/.venv/bin/activate`
- Core versions: `torch 2.8.0+cu128`, `torchaudio 2.8.0+cu128`, `pyannote.audio 3.1.x`
- Compatibility patches are baked into `scripts/diarization/diarize_inference.py` (weights_only=False, HF token bridge, NumPy NaN, torchaudio stubs). **Do not pip-upgrade torch/torchaudio/pyannote or these patches will break.**

### 2. HuggingFace Authentication

```bash
# Get token from https://huggingface.co/settings/tokens
huggingface-cli login

# Accept model terms (visit these pages and click "Accept"):
# https://huggingface.co/pyannote/speaker-diarization-3.1
# https://huggingface.co/pyannote/segmentation-3.0
# https://huggingface.co/pyannote/embedding
```

### 3. Verify Installation

```bash
python3 -c "
from pyannote.audio import Pipeline
import torch
print('PyTorch:', torch.__version__)
print('CUDA:', torch.cuda.is_available())
print('pyannote.audio: OK')
"
```

---

## Quick Test

### One-command end-to-end batch (pre-pass → diarize → reference match → post-process → word map)

```bash
export HF_TOKEN='…'  # also set PYANNOTE_AUTH_TOKEN if needed
source /home/billie/tools/vidops/.venv/bin/activate

PROJECT_ROOT=/home/billie/tools/vidops \
TOOL_ROOT=/home/billie/tools/vidops \
/home/billie/tools/vidops/.venv/bin/python \
  /home/billie/tools/vidops/scripts/diarization/batch_diarize.py \
  /tmp/batch_ytids_no_missing.txt \
  /home/billie/tools/vidops/results/diarization \
  --device cuda \
  --config /home/billie/tools/vidops/config/diarization.yaml \
  --reference hasan_piker4 \
  --match-threshold 0.68 --match-margin 0.01 --no-match-force-best
```

- The pre-pass (canonicalize + padding + WebRTC VAD) is automatically run into `generated/diarization_inputs/<ytid>/`.
- If a reference directory is missing, the script will prompt to build it interactively.
- Post-processing (merge micro-gaps, drop short turns) and word→speaker mapping run automatically and update `diarization.json`.

---

## File Locations

### Scripts
- **Entry point**: `scripts/diarization/batch_diarize.py` (runs pre-pass + diarization + reference matching + post-process + word map)
- **Inference core**: `scripts/diarization/diarize_inference.py`
- **Reference build**: `scripts/diarization/build_reference.py` (called interactively when missing)
- **Speaker matching**: `scripts/diarization/match_reference.py`
- **Legacy wrapper**: `scripts/diarization/run_diarization.sh` (still points to legacy; prefer `batch_diarize.py` for pyannote flow)

### Configuration
- **Config file**: `config/diarization.yaml`
- **8GB GPU defaults**:
  - Chunk: 15.0s
  - Overlap: 2.5s
  - Threshold: 0.6
  - Min speakers: null (auto)
  - Max speakers: null (auto)
  - Min cluster size: 15
  - Segmentation batch: 32
  - Embedding batch: 64
  - Preprocess chunk: inherits chunk_duration; workers: cpu_count-2 by default

### Outputs
- **Diarization results**: `results/diarization/<ytid>/`
  - `diarized_timestamps.tsv` (raw)
  - `diarized_timestamps_matched.tsv` (reference labels)
  - `diarized_timestamps_clean.tsv` (post-processed)
  - `speaker_words.tsv` (words mapped to speakers)
  - `diarization.json` (metadata + post-process + word-map stats)

- **References**: `data/references/<reference_name>/`
  - `*.wav` - 50 clips, 8-12s each, mono 16kHz
  - `reference.json` - Metadata

---

## Outputs Format

### diarized_timestamps.tsv
```tsv
ytid	speaker	start	end	duration	source_audio
abc123	SPEAKER_00	0.000	5.420	5.420	enhanced.wav
abc123	SPEAKER_01	5.530	12.100	6.570	enhanced.wav
abc123	SPEAKER_00	12.250	18.900	6.650	enhanced.wav
```

### diarization.json
```json
{
  "ytid": "abc123xyz",
  "model": "pyannote/speaker-diarization-3.1",
  "device": "cuda",
  "hyperparameters": {
    "chunk_duration": 15.0,
    "overlap_duration": 2.5,
    "threshold": 0.6
  },
  "results": {
    "num_speakers": 2,
    "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
    "num_segments": 142,
    "total_speech_duration": 3456.7
  },
  "runtime": {
    "elapsed_seconds": 34.2
  },
  "reference": {
    "reference_name": "test_speaker",
    "mapping": {
      "SPEAKER_00": "test_speaker",
      "SPEAKER_01": "UNKNOWN_SPEAKER_01"
    }
  }
}
```

---

## Expected Performance (8GB GPU)

| Video Length | Processing Time | VRAM Usage |
|--------------|----------------|------------|
| 30 min | 3-6 min | 3-5 GB |
| 60 min | 6-10 min | 3-5 GB |
| 120 min | 12-20 min | 3-5 GB |

**Speed**: ~5-10x realtime on 8GB GPU
**Accuracy**: Depends on audio quality and speaker characteristics

---

## Troubleshooting

### "No module named 'pyannote'"
```bash
pip install pyannote.audio==3.1.1
```

### "HuggingFace token required"
```bash
huggingface-cli login
# Visit model pages and accept terms
```

### "Audio file not found"
```bash
# Check audio location
ls pull/${ytid}__*.mp4

# Or use preprocessed audio
mkdir -p generated/diarization_inputs/${ytid}
# Place enhanced.wav or canonical.wav there
```

### "CUDA out of memory"
```bash
# Reduce batch sizes in config/diarization.yaml
segmentation_batch_size: 16  # Down from 32
embedding_batch_size: 32      # Down from 64
```

### Low quality diarization
- Check audio quality (denoise, loudness normalize)
- Adjust threshold (0.45-0.70 range)
- Build better reference clips
- Try segmentation override: `--segmentation pyannote/segmentation-3.0`

---

## Next Steps

1. **Install dependencies** (see above)
2. **Test on single file** to verify installation
3. **Build references** for your speakers
4. **Run batch processing** on pikerbreakdown set (172 files)
5. **Calibrate threshold** based on results
6. **Implement post-processing** (Step 6) - merge gaps, cleanup
7. **Implement word mapping** (Step 7) - create speaker_words.tsv
8. **Integrate preprocessing** (Steps 2-4) - canonicalize, VAD, enhancement

---

## Documentation

- **Full implementation**: `docs/DIARIZATION/INFERENCE_IMPLEMENTATION.md`
- **Status & next steps**: `docs/DIARIZATION/PHASE_WALKTHROUGH.md`
- **Architecture**: `docs/DIARIZATION/DIARIZATION_2.0.md`
- **Task breakdown**: `docs/DIARIZATION/IMPLEMENTATION_PLAN.md`

---

## Commands Reference

```bash
# Diarize single file
./scripts/diarization/run_diarization.sh single <ytid> [--reference <name>]

# Batch process
./scripts/diarization/run_diarization.sh batch <ytids_file> [--reference <name>]

# Build reference
./scripts/diarization/run_diarization.sh build-reference <name> <video1> [video2...]

# Match existing results
./scripts/diarization/run_diarization.sh match <ytid> <reference_name>

# Direct Python usage
python3 scripts/diarization/diarize_inference.py <audio> <ytid> <output_dir>
python3 scripts/diarization/build_reference.py <ref_name> <videos...>
python3 scripts/diarization/match_reference.py <diar_dir> <ref_dir>
python3 scripts/diarization/batch_diarize.py <ytids_file> <output_dir>
```

---

**Ready to use!** Install dependencies and run your first test.
