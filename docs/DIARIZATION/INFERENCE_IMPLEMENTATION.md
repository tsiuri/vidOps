# Diarization Inference Implementation (pyannote)

**Phase**: Diarization 2.0 - Step 5 (Inference)
**Model**: `pyannote/speaker-diarization-3.1`
**Purpose**: Speaker diarization using pyannote.audio with reference-based clustering

---

## Table of Contents
1. [Installation & Setup](#installation--setup)
2. [Core Inference Implementation](#core-inference-implementation)
3. [Segmentation Override](#segmentation-override)
4. [Reference Handling](#reference-handling)
5. [Configuration](#configuration)
6. [Runtime Tips & VRAM](#runtime-tips--vram)
7. [Example Usage](#example-usage)

---

## Installation & Setup

### Dependencies
```bash
# Core pyannote stack (requires Python 3.8+)
pip install pyannote.audio==3.1.1
pip install torch torchaudio  # GPU: install CUDA-compatible versions

# Accept pyannote model terms (required once)
# Visit: https://huggingface.co/pyannote/speaker-diarization-3.1
# Visit: https://huggingface.co/pyannote/segmentation-3.0
# Accept terms, then authenticate:
huggingface-cli login
```

### Verify Installation
```python
from pyannote.audio import Pipeline
import torch

print(f"PyTorch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"CUDA device: {torch.cuda.get_device_name(0)}")
    print(f"CUDA memory: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB")
```

---

## Core Inference Implementation

### Basic Invocation

```python
#!/usr/bin/env python3
"""
Diarization inference using pyannote/speaker-diarization-3.1
"""
import os
from pathlib import Path
from pyannote.audio import Pipeline
import torch

def run_diarization(
    audio_path: str | Path,
    device: str = "auto",
    chunk_duration: float = 15.0,  # 12-20s recommended
    overlap_duration: float = 2.5,  # 2-3s recommended
    threshold: float = 0.6,         # 0.45-0.70 range
    segmentation_model: str | None = None,  # Override if needed
    use_auth_token: str | None = None,
) -> "pyannote.core.Annotation":
    """
    Run speaker diarization on preprocessed audio.

    Args:
        audio_path: Path to canonical/enhanced WAV (mono 16kHz recommended)
        device: "auto", "cuda", "cpu", or specific like "cuda:0"
        chunk_duration: Processing chunk size in seconds (12-20s)
        overlap_duration: Overlap between chunks (2-3s)
        threshold: Speaker similarity threshold (0.6 default, calibrate 0.45-0.70)
        segmentation_model: Optional override (e.g., "pyannote/segmentation-3.0")
        use_auth_token: HuggingFace token (or True to use cached)

    Returns:
        pyannote Annotation object with speaker timeline
    """
    # Auto-detect device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"🔊 Diarization device: {device}")

    # Load pipeline
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=use_auth_token or True,
    )

    # Override segmentation model if specified
    if segmentation_model:
        from pyannote.audio import Model
        print(f"🔄 Overriding segmentation: {segmentation_model}")
        seg_model = Model.from_pretrained(
            segmentation_model,
            use_auth_token=use_auth_token or True
        )
        pipeline._segmentation.model = seg_model

    # Move to device
    pipeline.to(torch.device(device))

    # Configure hyperparameters
    pipeline.clustering = "AgglomerativeClustering"  # Default
    pipeline.segmentation_batch_size = 32 if device == "cpu" else 64
    pipeline.embedding_batch_size = 32 if device == "cpu" else 128

    # Run inference
    print(f"📊 Processing: {audio_path}")
    print(f"   Chunk: {chunk_duration}s, Overlap: {overlap_duration}s, Threshold: {threshold}")

    diarization = pipeline(
        audio_path,
        min_speakers=None,  # Auto-detect
        max_speakers=None,  # Auto-detect
        clustering={
            "method": "centroid",
            "min_cluster_size": 15,  # Minimum segments per speaker
            "threshold": threshold,
        },
        segmentation={
            "min_duration_off": 0.0,  # No minimum gap (we merge later)
        },
    )

    return diarization


def save_diarization_tsv(
    diarization: "pyannote.core.Annotation",
    output_path: str | Path,
    ytid: str,
    source_audio: str,
):
    """
    Export diarization to TSV format for DB ingest.

    Format: ytid, speaker, start, end, duration, source_audio
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        f.write("ytid\tspeaker\tstart\tend\tduration\tsource_audio\n")

        for segment, _, speaker in diarization.itertracks(yield_label=True):
            start = segment.start
            end = segment.end
            duration = segment.duration

            f.write(f"{ytid}\t{speaker}\t{start:.3f}\t{end:.3f}\t{duration:.3f}\t{source_audio}\n")

    print(f"✅ Saved diarization: {output_path}")


# Example usage
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python diarize_inference.py <audio.wav> [ytid]")
        sys.exit(1)

    audio_file = Path(sys.argv[1])
    ytid = sys.argv[2] if len(sys.argv) > 2 else audio_file.stem

    # Run diarization
    result = run_diarization(
        audio_path=audio_file,
        device="auto",
        chunk_duration=15.0,
        overlap_duration=2.5,
        threshold=0.6,
    )

    # Save output
    output_dir = Path("results/diarization") / ytid
    save_diarization_tsv(
        diarization=result,
        output_path=output_dir / "diarized_timestamps.tsv",
        ytid=ytid,
        source_audio=str(audio_file),
    )

    # Print summary
    speakers = set(result.labels())
    print(f"\n📈 Summary:")
    print(f"   Speakers detected: {len(speakers)}")
    print(f"   Speaker labels: {', '.join(sorted(speakers))}")
    print(f"   Total segments: {len(list(result.itertracks()))}")
```

---

## Segmentation Override

### Why Override Segmentation?

The default `speaker-diarization-3.1` includes a segmentation model, but you may want to override for:
- **Robustness**: Newer/domain-specific segmentation models
- **Testing**: Compare segmentation quality
- **Performance**: Lighter models for speed

### Supported Segmentation Models

```python
# Recommended overrides
SEGMENTATION_MODELS = {
    "default": None,  # Use bundled model
    "robust": "pyannote/segmentation-3.0",  # More robust (2024)
    "fast": "pyannote/segmentation",  # Lighter/faster
}
```

### Implementation

```python
def override_segmentation(
    pipeline: Pipeline,
    segmentation_model: str,
    use_auth_token: str | None = None,
) -> Pipeline:
    """
    Replace pipeline's segmentation model.

    Args:
        pipeline: Loaded diarization pipeline
        segmentation_model: Model identifier (e.g., "pyannote/segmentation-3.0")
        use_auth_token: HuggingFace token

    Returns:
        Modified pipeline
    """
    from pyannote.audio import Model

    print(f"🔄 Loading segmentation override: {segmentation_model}")

    # Load custom segmentation model
    seg_model = Model.from_pretrained(
        segmentation_model,
        use_auth_token=use_auth_token or True
    )

    # Replace in pipeline
    pipeline._segmentation.model = seg_model

    # Re-apply device if already set
    if hasattr(pipeline, "device"):
        pipeline.to(pipeline.device)

    print("✅ Segmentation override applied")
    return pipeline


# Usage in main pipeline
def run_with_override(audio_path: Path, seg_override: str | None = None):
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")

    if seg_override:
        pipeline = override_segmentation(pipeline, seg_override)

    pipeline.to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))

    return pipeline(audio_path)
```

---

## Reference Handling

### Reference-Based Clustering

PyAnnote supports **reference audio** to assign consistent speaker labels across files. This is critical for:
- Multi-file consistency (same speaker = same ID)
- Domain adaptation (known speakers)
- Label stability across batches

### Building Shared References

```python
#!/usr/bin/env python3
"""
Build shared reference clips for speaker identification.
"""
import subprocess
from pathlib import Path
import random

def build_reference_clips(
    source_videos: list[Path],
    reference_name: str,
    output_dir: Path,
    num_clips: int = 50,
    clip_duration: tuple[float, float] = (8.0, 12.0),  # Min, max seconds
    spacing: float = 30.0,  # Minimum spacing between clips
) -> list[Path]:
    """
    Extract diverse reference clips from source videos.

    Args:
        source_videos: List of video/audio files to sample from
        reference_name: Name for this reference set (e.g., "speaker_alpha")
        output_dir: Where to save clips
        num_clips: Target number of clips (default: 50)
        clip_duration: (min, max) duration in seconds
        spacing: Minimum seconds between clip start times

    Returns:
        List of generated clip paths
    """
    output_dir = Path(output_dir) / reference_name
    output_dir.mkdir(parents=True, exist_ok=True)

    clips_per_source = max(1, num_clips // len(source_videos))
    generated_clips = []

    for video in source_videos:
        # Get duration
        duration_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video)
        ]
        duration = float(subprocess.check_output(duration_cmd).decode().strip())

        # Generate random start times
        safe_duration = duration - max(clip_duration)
        if safe_duration < spacing:
            continue

        start_times = []
        for _ in range(clips_per_source):
            # Find non-overlapping start time
            attempts = 0
            while attempts < 100:
                start = random.uniform(0, safe_duration)
                if all(abs(start - existing) > spacing for existing in start_times):
                    start_times.append(start)
                    break
                attempts += 1

        # Extract clips
        for i, start in enumerate(start_times):
            clip_len = random.uniform(*clip_duration)
            output_clip = output_dir / f"{video.stem}_ref{i:03d}.wav"

            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-ss", str(start),
                "-t", str(clip_len),
                "-i", str(video),
                "-ac", "1",  # Mono
                "-ar", "16000",  # 16 kHz
                "-acodec", "pcm_s16le",
                str(output_clip)
            ]

            subprocess.run(cmd, check=True)
            generated_clips.append(output_clip)

            if len(generated_clips) >= num_clips:
                break

        if len(generated_clips) >= num_clips:
            break

    print(f"✅ Built {len(generated_clips)} reference clips: {reference_name}")
    return generated_clips[:num_clips]


def get_or_build_reference(
    reference_name: str,
    reference_dir: Path,
    source_videos: list[Path] | None = None,
    force_rebuild: bool = False,
) -> Path:
    """
    Get existing reference or build new one.

    Args:
        reference_name: Reference identifier
        reference_dir: Base directory for references
        source_videos: Videos to sample (if building)
        force_rebuild: Force rebuild even if exists

    Returns:
        Path to reference directory
    """
    ref_path = reference_dir / reference_name

    if ref_path.exists() and not force_rebuild:
        clips = list(ref_path.glob("*.wav"))
        print(f"♻️  Reusing existing reference: {reference_name} ({len(clips)} clips)")
        return ref_path

    if not source_videos:
        raise ValueError(f"Reference '{reference_name}' not found and no source videos provided")

    print(f"🔨 Building new reference: {reference_name}")
    build_reference_clips(
        source_videos=source_videos,
        reference_name=reference_name,
        output_dir=reference_dir,
        num_clips=50,
        clip_duration=(8.0, 12.0),
    )

    return ref_path
```

### Using References in Diarization

**Note**: pyannote's `speaker-diarization-3.1` doesn't directly accept reference audio in the public API. For reference-based labeling, you have two options:

#### Option A: Post-process with Embeddings

```python
from pyannote.audio import Inference
from scipy.spatial.distance import cdist
import numpy as np

def match_speakers_to_reference(
    diarization: "pyannote.core.Annotation",
    audio_path: Path,
    reference_clips: list[Path],
    threshold: float = 0.6,
    device: str = "cuda",
) -> dict[str, str]:
    """
    Map anonymous speaker labels to reference identities.

    Args:
        diarization: Raw diarization output
        audio_path: Audio that was diarized
        reference_clips: List of reference audio files
        threshold: Similarity threshold (lower = more strict)

    Returns:
        Mapping: {anonymous_label: reference_label}
    """
    # Load embedding model
    embedding_model = Inference(
        "pyannote/embedding",
        window="whole",
        device=torch.device(device)
    )

    # Extract embeddings for each speaker in diarization
    speaker_embeddings = {}
    for segment, _, label in diarization.itertracks(yield_label=True):
        if label not in speaker_embeddings:
            # Extract embedding from this segment
            excerpt = {"uri": str(audio_path), "segment": segment}
            emb = embedding_model(excerpt)
            speaker_embeddings[label] = emb

    # Extract reference embeddings
    reference_embeddings = {}
    for ref_clip in reference_clips:
        ref_name = ref_clip.stem.split("_ref")[0]  # Extract base name
        emb = embedding_model({"uri": str(ref_clip)})
        if ref_name not in reference_embeddings:
            reference_embeddings[ref_name] = []
        reference_embeddings[ref_name].append(emb)

    # Average reference embeddings per speaker
    ref_avg = {
        name: np.mean(embs, axis=0)
        for name, embs in reference_embeddings.items()
    }

    # Match each speaker to closest reference
    mapping = {}
    for speaker, emb in speaker_embeddings.items():
        # Compute cosine similarity to all references
        similarities = {
            ref_name: 1 - cdist([emb], [ref_emb], metric="cosine")[0][0]
            for ref_name, ref_emb in ref_avg.items()
        }

        best_match = max(similarities, key=similarities.get)
        best_score = similarities[best_match]

        if best_score >= threshold:
            mapping[speaker] = best_match
        else:
            mapping[speaker] = f"UNKNOWN_{speaker}"

    return mapping
```

#### Option B: Use pyannote.audio.pipelines (Advanced)

For production, consider implementing a custom pipeline that accepts reference embeddings directly. See pyannote documentation: [Custom Pipelines](https://github.com/pyannote/pyannote-audio/blob/develop/tutorials/applying_a_pipeline.ipynb)

---

## Configuration

### Environment Variables

```bash
# Device selection
export DIARIZATION_DEVICE="auto"  # auto|cuda|cpu

# Model configuration
export DIARIZATION_MODEL="pyannote/speaker-diarization-3.1"
export SEGMENTATION_OVERRIDE=""  # Empty = use default

# Hyperparameters
export CHUNK_DURATION=15.0        # 12-20s recommended
export OVERLAP_DURATION=2.5       # 2-3s recommended
export SPEAKER_THRESHOLD=0.6      # 0.45-0.70 range

# Reference handling
export REFERENCE_DIR="data/references"
export REFERENCE_CLIPS=50
export REFERENCE_CLIP_MIN=8.0
export REFERENCE_CLIP_MAX=12.0

# HuggingFace authentication
export HF_TOKEN="hf_..."  # Or use `huggingface-cli login`
```

### Config File (YAML)

```yaml
# config/diarization.yaml
diarization:
  model: "pyannote/speaker-diarization-3.1"
  device: "auto"  # auto|cuda|cpu|cuda:0

  segmentation:
    override: null  # null or "pyannote/segmentation-3.0"

  hyperparameters:
    chunk_duration: 15.0      # 12-20s
    overlap_duration: 2.5     # 2-3s
    threshold: 0.6            # Speaker similarity (0.45-0.70)
    min_speakers: null        # Auto-detect
    max_speakers: null        # Auto-detect
    min_cluster_size: 15      # Minimum segments per speaker

  batching:
    segmentation_batch_size: 64
    embedding_batch_size: 128

  references:
    enabled: true
    directory: "data/references"
    num_clips: 50
    clip_duration: [8.0, 12.0]  # [min, max] seconds
    spacing: 30.0                # Minimum seconds between clips
    reuse_existing: true
    match_threshold: 0.6
```

### Loading Configuration

```python
import yaml
from pathlib import Path

def load_config(config_path: Path = Path("config/diarization.yaml")) -> dict:
    """Load diarization configuration."""
    with config_path.open() as f:
        return yaml.safe_load(f)

# Usage
config = load_config()
diarization_config = config["diarization"]

result = run_diarization(
    audio_path="audio.wav",
    device=diarization_config["device"],
    chunk_duration=diarization_config["hyperparameters"]["chunk_duration"],
    threshold=diarization_config["hyperparameters"]["threshold"],
)
```

---

## Runtime Tips & VRAM

### Device Performance

| Device | Model Load | Processing Speed | VRAM Usage |
|--------|-----------|------------------|------------|
| **CPU** | ~5s | 0.5-1x realtime | N/A (RAM: ~4GB) |
| **CUDA (8GB)** | ~10s | 5-10x realtime | ~3-5GB |
| **CUDA (12GB+)** | ~10s | 10-20x realtime | ~5-8GB |

### VRAM Optimization

```python
def optimize_for_vram(pipeline: Pipeline, vram_gb: float):
    """
    Adjust batch sizes based on available VRAM.

    Args:
        pipeline: Loaded pipeline
        vram_gb: Available VRAM in GB
    """
    if vram_gb < 6:
        # Low VRAM (4-6GB)
        pipeline.segmentation_batch_size = 16
        pipeline.embedding_batch_size = 32
        print("⚠️  Low VRAM mode (batch sizes reduced)")

    elif vram_gb < 12:
        # Medium VRAM (6-12GB)
        pipeline.segmentation_batch_size = 32
        pipeline.embedding_batch_size = 64
        print("📊 Medium VRAM mode (standard batching)")

    else:
        # High VRAM (12GB+)
        pipeline.segmentation_batch_size = 64
        pipeline.embedding_batch_size = 128
        print("🚀 High VRAM mode (large batches)")

    return pipeline


# Usage
if torch.cuda.is_available():
    vram = torch.cuda.get_device_properties(0).total_memory / 1e9
    pipeline = optimize_for_vram(pipeline, vram)
```

### Monitoring

```python
def print_gpu_stats():
    """Print GPU memory usage."""
    if not torch.cuda.is_available():
        return

    allocated = torch.cuda.memory_allocated() / 1e9
    reserved = torch.cuda.memory_reserved() / 1e9
    total = torch.cuda.get_device_properties(0).total_memory / 1e9

    print(f"🎮 GPU Memory: {allocated:.2f}GB allocated, {reserved:.2f}GB reserved, {total:.2f}GB total")


# Call periodically
import time

def run_with_monitoring(audio_path: Path):
    print_gpu_stats()
    start = time.time()

    result = pipeline(audio_path)

    elapsed = time.time() - start
    print(f"⏱️  Processing time: {elapsed:.2f}s")
    print_gpu_stats()

    return result
```

### Chunking Long Files

For files >30 minutes on low-memory systems:

```python
def diarize_long_file(
    audio_path: Path,
    chunk_size: int = 600,  # 10 minutes
    overlap: int = 60,      # 1 minute overlap
) -> "pyannote.core.Annotation":
    """
    Process long files in chunks to manage memory.
    """
    from pydub import AudioSegment
    from pyannote.core import Annotation, Segment

    # Load audio
    audio = AudioSegment.from_wav(audio_path)
    duration = len(audio) / 1000.0  # milliseconds to seconds

    full_diarization = Annotation()

    # Process in chunks
    for start_sec in range(0, int(duration), chunk_size - overlap):
        end_sec = min(start_sec + chunk_size, duration)

        # Extract chunk
        chunk_audio = audio[start_sec * 1000:end_sec * 1000]
        chunk_path = f"/tmp/chunk_{start_sec}.wav"
        chunk_audio.export(chunk_path, format="wav")

        # Diarize chunk
        chunk_result = pipeline(chunk_path)

        # Offset timestamps
        for segment, _, label in chunk_result.itertracks(yield_label=True):
            full_diarization[Segment(
                segment.start + start_sec,
                segment.end + start_sec
            ), label] = label

        os.unlink(chunk_path)

    return full_diarization
```

---

## Example Usage

### Standalone Script

```python
#!/usr/bin/env python3
"""
Complete diarization workflow with reference handling.
"""
import sys
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: diarize.py <audio.wav> [reference_name]")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    reference_name = sys.argv[2] if len(sys.argv) > 2 else None

    ytid = audio_path.stem

    # 1. Run diarization
    print("🎯 Running diarization...")
    diarization = run_diarization(
        audio_path=audio_path,
        device="auto",
        chunk_duration=15.0,
        overlap_duration=2.5,
        threshold=0.6,
    )

    # 2. Match to reference (if provided)
    if reference_name:
        reference_dir = Path("data/references")
        ref_path = reference_dir / reference_name

        if ref_path.exists():
            print(f"🔗 Matching to reference: {reference_name}")
            reference_clips = list(ref_path.glob("*.wav"))

            speaker_mapping = match_speakers_to_reference(
                diarization=diarization,
                audio_path=audio_path,
                reference_clips=reference_clips,
                threshold=0.6,
            )

            print(f"   Speaker mapping: {speaker_mapping}")

            # Relabel
            diarization = diarization.rename_labels(mapping=speaker_mapping)

    # 3. Save outputs
    output_dir = Path("results/diarization") / ytid
    output_dir.mkdir(parents=True, exist_ok=True)

    save_diarization_tsv(
        diarization=diarization,
        output_path=output_dir / "diarized_timestamps.tsv",
        ytid=ytid,
        source_audio=str(audio_path),
    )

    # 4. Summary
    speakers = set(diarization.labels())
    segments = list(diarization.itertracks())

    print(f"\n✅ Diarization complete!")
    print(f"   Speakers: {len(speakers)} ({', '.join(sorted(speakers))})")
    print(f"   Segments: {len(segments)}")
    print(f"   Output: {output_dir}")


if __name__ == "__main__":
    main()
```

### Integration with Pipeline

```bash
#!/usr/bin/env bash
# scripts/diarization/run_inference.sh

set -euo pipefail

YTID="$1"
REFERENCE_NAME="${2:-}"

TOOL_ROOT="${TOOL_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"

# Paths
ENHANCED_AUDIO="${PROJECT_ROOT}/generated/diarization_inputs/${YTID}/enhanced.wav"
OUTPUT_DIR="${PROJECT_ROOT}/results/diarization/${YTID}"

# Run inference
python3 "${TOOL_ROOT}/scripts/diarization/diarize_inference.py" \
    "$ENHANCED_AUDIO" \
    "$YTID" \
    ${REFERENCE_NAME:+--reference "$REFERENCE_NAME"}

echo "✅ Diarization complete: ${OUTPUT_DIR}/diarized_timestamps.tsv"
```

### Batch Processing

```python
#!/usr/bin/env python3
"""
Batch diarization with progress tracking.
"""
from pathlib import Path
from tqdm import tqdm

def batch_diarize(
    audio_files: list[Path],
    output_dir: Path,
    reference_name: str | None = None,
    **kwargs
):
    """Process multiple files with progress bar."""

    # Load pipeline once
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
    pipeline.to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))

    for audio_file in tqdm(audio_files, desc="Diarizing"):
        ytid = audio_file.stem

        try:
            # Run inference
            result = pipeline(audio_file, **kwargs)

            # Save
            save_diarization_tsv(
                diarization=result,
                output_path=output_dir / ytid / "diarized_timestamps.tsv",
                ytid=ytid,
                source_audio=str(audio_file),
            )

        except Exception as e:
            print(f"❌ Failed: {audio_file.name} - {e}")
            continue


# Usage
if __name__ == "__main__":
    audio_files = list(Path("generated/diarization_inputs").glob("*/enhanced.wav"))
    batch_diarize(audio_files, Path("results/diarization"))
```

---

## Next Steps

1. **Test on sample set**: Run on pikerbreakdown YTIDs (172 files)
2. **Calibrate threshold**: Grid search 0.45-0.70 on labeled slice
3. **Build references**: Create shared reference sets per domain
4. **Integrate post-processing**: Add merge/cleanup (Step 6)
5. **Performance profiling**: Measure VRAM and speed per stage

---

**Created**: 2025-12-04
**Author**: Claude Code
**Related**: DIARIZATION_2.0.md, IMPLEMENTATION_PLAN.md
