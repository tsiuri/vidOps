#!/usr/bin/env python3
"""
Diarization inference using pyannote/speaker-diarization-3.1
Optimized for 8GB GPU with sane defaults.
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
import torch
import torchaudio
import numpy as np
import huggingface_hub
import torch.serialization as torch_serial
# Delay importing pyannote modules that expect np.NaN until after we alias it.

# Prefer env HF_TOKEN/HUGGINGFACE_TOKEN if present
HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")

# PyTorch 2.6+ compatibility: patch torch.load to use weights_only=False by default
# This is needed because pyannote.audio loads models with classes not on the safe list
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs['weights_only'] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load
# NumPy 2.x removed np.NaN; pyannote imports it. Provide alias.
if not hasattr(np, "NaN"):
    np.NaN = np.nan
if not hasattr(np, "NAN"):
    np.NAN = np.nan

# Allowlist TorchVersion (and pyannote Specifications if available) for weights_only safelist
safe_globals = [torch.torch_version.TorchVersion]
try:
    from pyannote.audio.core.task import Specifications
    safe_globals.append(Specifications)
except Exception:
    Specifications = None
try:
    from pyannote.audio.core.task import Problem
    safe_globals.append(Problem)
except Exception:
    Problem = None
try:
    from pyannote.audio.core.task import Resolution
    safe_globals.append(Resolution)
except Exception:
    Resolution = None
try:
    torch_serial.add_safe_globals(safe_globals)
except Exception:
    pass

# Patch lightning cloud_io loader to force weights_only=False
try:
    from lightning_fabric.utilities import cloud_io as _cloud_io
    _orig_pl_load = _cloud_io._load

    def _patched_pl_load(*args, **kwargs):
        if 'weights_only' not in kwargs:
            kwargs['weights_only'] = False
        return _orig_pl_load(*args, **kwargs)

    _cloud_io._load = _patched_pl_load
except Exception:
    pass

# Also patch Lightning's _load function (used by pyannote internally)
try:
    import lightning.fabric.utilities.cloud_io as cloud_io
    _original_pl_load = cloud_io._load
    def _patched_pl_load(*args, **kwargs):
        if 'weights_only' not in kwargs:
            kwargs['weights_only'] = False
        return _original_pl_load(*args, **kwargs)
    cloud_io._load = _patched_pl_load
except (ImportError, AttributeError):
    pass  # Lightning not yet imported or different version

# Torchaudio 2.9 dropped set_audio_backend; pyannote still calls it. Provide a stub if missing.
if not hasattr(torchaudio, "set_audio_backend"):
    def _set_audio_backend(backend: str):
        return None
    torchaudio.set_audio_backend = _set_audio_backend

# Torchaudio 2.9 also dropped get_audio_backend; provide a stub to satisfy pyannote imports.
if not hasattr(torchaudio, "get_audio_backend"):
    def _get_audio_backend():
        return "soundfile"
    torchaudio.get_audio_backend = _get_audio_backend

# torchaudio.list_audio_backends was also removed; speechbrain checks it.
if not hasattr(torchaudio, "list_audio_backends"):
    def _list_audio_backends():
        return ["soundfile"]
    torchaudio.list_audio_backends = _list_audio_backends

# NumPy 2.x removed np.NaN; pyannote imports it. Provide alias.
if not hasattr(np, "NaN"):
    np.NaN = np.nan

# huggingface_hub >=1.0 uses 'token'; pyannote 3.1.x passes 'use_auth_token'. Patch to bridge.
_orig_hf_download = huggingface_hub.hf_hub_download
def _patched_hf_download(*args, **kwargs):
    if "use_auth_token" in kwargs and "token" not in kwargs:
        kwargs["token"] = kwargs.pop("use_auth_token")
    return _orig_hf_download(*args, **kwargs)
huggingface_hub.hf_hub_download = _patched_hf_download
try:
    import huggingface_hub.file_download as _fd
    _orig_fd_download = _fd.hf_hub_download
    def _patched_fd_download(*args, **kwargs):
        if "use_auth_token" in kwargs and "token" not in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        return _orig_fd_download(*args, **kwargs)
    _fd.hf_hub_download = _patched_fd_download
except Exception:
    pass

from pyannote.audio import Pipeline

# Default hyperparameters optimized for 8GB GPU
DEFAULTS_8GB = {
    "chunk_duration": 15.0,           # 12-20s range, 15s balanced
    "overlap_duration": 2.5,          # 2-3s range, 2.5s balanced
    "threshold": 0.6,                 # 0.45-0.70 range, start at 0.6
    "min_cluster_size": 15,           # Minimum segments per speaker
    "segmentation_batch_size": 32,    # 8GB VRAM optimized
    "embedding_batch_size": 64,       # 8GB VRAM optimized
}


def run_diarization(
    audio_path: str | Path,
    ytid: str,
    output_dir: str | Path,
    device: str = "auto",
    chunk_duration: float = DEFAULTS_8GB["chunk_duration"],
    overlap_duration: float = DEFAULTS_8GB["overlap_duration"],
    threshold: float = DEFAULTS_8GB["threshold"],
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    min_cluster_size: int | None = None,
    segmentation_batch_size: int | None = None,
    embedding_batch_size: int | None = None,
    segmentation_model: str | None = None,
    model: str = "pyannote/speaker-diarization-3.1",
    use_auth_token: str | bool = True,
    verbose: bool = True,
) -> dict:
    """
    Run speaker diarization on preprocessed audio.

    Args:
        audio_path: Path to canonical/enhanced WAV (mono 16kHz recommended)
        ytid: Video/YouTube ID for output naming
        output_dir: Directory for outputs (will create subdirectory per ytid)
        device: "auto", "cuda", "cpu", or specific like "cuda:0"
        chunk_duration: Processing chunk size in seconds (12-20s)
        overlap_duration: Overlap between chunks (2-3s)
        threshold: Speaker similarity threshold (0.45-0.70)
        segmentation_model: Optional override (e.g., "pyannote/segmentation-3.0")
        use_auth_token: HuggingFace token (True = use cached)
        verbose: Print progress messages

    Returns:
        Dict with metadata about the run
    """
    audio_path = Path(audio_path)
    output_dir = Path(output_dir) / ytid
    output_dir.mkdir(parents=True, exist_ok=True)

    # Auto-detect device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if verbose:
        print(f"🔊 Diarization for: {ytid}")
        print(f"   Audio: {audio_path}")
        print(f"   Device: {device}")
        if device == "cuda":
            gpu_name = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
            print(f"   GPU: {gpu_name} ({vram_gb:.1f}GB)")

    # Load pipeline
    if verbose:
        print(f"📦 Loading model: pyannote/speaker-diarization-3.1")

    token_val = HF_TOKEN if HF_TOKEN else (use_auth_token if isinstance(use_auth_token, str) else None)
    pipeline = Pipeline.from_pretrained(
        model,
        token=token_val,
    )

    # Override segmentation model if specified
    if segmentation_model:
        if verbose:
            print(f"🔄 Overriding segmentation: {segmentation_model}")
        from pyannote.audio import Model
        seg_model = Model.from_pretrained(
            segmentation_model,
            token=token_val,
        )
        pipeline._segmentation.model = seg_model

    # Move to device
    pipeline.to(torch.device(device))

    # Configure hyperparameters (8GB GPU optimized)
    pipeline.segmentation_batch_size = (
        segmentation_batch_size if segmentation_batch_size is not None else DEFAULTS_8GB["segmentation_batch_size"]
    )
    pipeline.embedding_batch_size = (
        embedding_batch_size if embedding_batch_size is not None else DEFAULTS_8GB["embedding_batch_size"]
    )

    # Optionally set clustering minimum size if supported
    if min_cluster_size is not None:
        for attr in ("_clustering", "clustering"):
            clustering_obj = getattr(pipeline, attr, None)
            if clustering_obj and hasattr(clustering_obj, "min_cluster_size"):
                try:
                    clustering_obj.min_cluster_size = min_cluster_size
                except Exception:
                    pass
                break

    if verbose:
        print(f"📊 Hyperparameters:")
        print(f"   Chunk: {chunk_duration}s, Overlap: {overlap_duration}s")
        print(f"   Threshold: {threshold}")
        print(f"   Batch sizes: seg={pipeline.segmentation_batch_size}, emb={pipeline.embedding_batch_size}")

    # Run inference
    start_time = datetime.now()

    if verbose:
        print(f"🎯 Running diarization...")

    try:
        diarization = pipeline(
            str(audio_path),
            min_speakers=min_speakers,  # Auto-detect if None
            max_speakers=max_speakers,  # Auto-detect if None
        )
    except Exception as e:
        error_msg = str(e)
        # Check if it's a sample count mismatch error
        if "samples instead of the expected" in error_msg or "resulted in" in error_msg:
            if verbose:
                print(f"⚠️  Sample alignment error detected, padding audio and retrying...")

            # Pad audio to ensure proper chunk alignment using torchaudio
            waveform_t, sr = torchaudio.load(str(audio_path))

            # Ensure mono (take first channel if stereo)
            if waveform_t.shape[0] > 1:
                waveform_t = waveform_t[0:1]

            # Calculate padding needed for chunk_duration alignment
            chunk_samples = int(chunk_duration * sr)
            current_samples = waveform_t.shape[1]
            padding_needed = chunk_samples - (current_samples % chunk_samples)

            if padding_needed > 0 and padding_needed < chunk_samples:
                # Pad on the right (end) with zeros
                padded = torch.nn.functional.pad(waveform_t, (0, padding_needed), mode='constant', value=0)

                # Save padded version
                padded_path = audio_path.parent / f"{audio_path.stem}_padded.wav"
                torchaudio.save(str(padded_path), padded, sr)

                if verbose:
                    print(f"   Padded {padding_needed} samples ({padding_needed/sr:.2f}s), retrying diarization...")

                # Retry with padded audio
                diarization = pipeline(
                    str(padded_path),
                    min_speakers=None,
                    max_speakers=None,
                )

                # Clean up padded file
                padded_path.unlink()
            else:
                raise
        else:
            raise

    elapsed = (datetime.now() - start_time).total_seconds()

    if verbose:
        print(f"⏱️  Processing time: {elapsed:.2f}s")

    # Extract speaker statistics (pyannote 4.x API)
    serialized = diarization.serialize()
    segments_data = serialized.get("diarization", [])

    speakers = sorted(set(seg["speaker"] for seg in segments_data))
    total_speech_duration = sum(seg["end"] - seg["start"] for seg in segments_data)

    if verbose:
        print(f"📈 Results:")
        print(f"   Speakers: {len(speakers)} ({', '.join(speakers)})")
        print(f"   Segments: {len(segments_data)}")
        print(f"   Total speech: {total_speech_duration:.1f}s")

    # Save TSV output
    tsv_path = output_dir / "diarized_timestamps.tsv"
    with tsv_path.open("w", encoding="utf-8") as f:
        f.write("ytid\tspeaker\tstart\tend\tduration\tsource_audio\n")

        for seg in segments_data:
            start = seg["start"]
            end = seg["end"]
            duration = end - start
            speaker = seg["speaker"]
            f.write(
                f"{ytid}\t{speaker}\t{start:.3f}\t"
                f"{end:.3f}\t{duration:.3f}\t{audio_path.name}\n"
            )

    if verbose:
        print(f"💾 Saved: {tsv_path}")

    # Save metadata JSON
    metadata = {
        "ytid": ytid,
        "source_audio": str(audio_path),
        "model": model,
        "segmentation_override": segmentation_model,
        "device": device,
        "hyperparameters": {
            "chunk_duration": chunk_duration,
            "overlap_duration": overlap_duration,
            "threshold": threshold,
            "min_cluster_size": min_cluster_size if min_cluster_size is not None else DEFAULTS_8GB["min_cluster_size"],
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
            "segmentation_batch_size": pipeline.segmentation_batch_size,
            "embedding_batch_size": pipeline.embedding_batch_size,
        },
        "results": {
            "num_speakers": len(speakers),
            "speaker_labels": speakers,
            "num_segments": len(segments_data),
            "total_speech_duration": total_speech_duration,
        },
        "runtime": {
            "elapsed_seconds": elapsed,
            "timestamp": start_time.isoformat(),
        },
        "reference": None,  # Will be populated by reference matching
    }

    json_path = output_dir / "diarization.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    if verbose:
        print(f"💾 Saved: {json_path}")
        print(f"✅ Diarization complete!\n")

    return metadata


def main():
    # Load config if present (env DIARIZATION_CONFIG or config/diarization_pipeline.json)
    def load_config():
        cfg_env = os.environ.get("DIARIZATION_CONFIG")
        default_path = Path(__file__).resolve().parents[2] / "config" / "diarization_pipeline.json"
        cfg_path = Path(cfg_env) if cfg_env else default_path
        if cfg_path.exists():
            try:
                return json.loads(cfg_path.read_text())
            except Exception:
                return None
        return None

    cfg = load_config() or {}
    inf_cfg = cfg.get("inference", {})
    defaults = {
        "chunk_duration": inf_cfg.get("chunk_duration", DEFAULTS_8GB["chunk_duration"]),
        "overlap_duration": inf_cfg.get("overlap_duration", DEFAULTS_8GB["overlap_duration"]),
        "threshold": inf_cfg.get("threshold", DEFAULTS_8GB["threshold"]),
        "min_speakers": inf_cfg.get("min_speakers"),
        "max_speakers": inf_cfg.get("max_speakers"),
        "min_cluster_size": inf_cfg.get("min_cluster_size", DEFAULTS_8GB["min_cluster_size"]),
        "segmentation_batch_size": inf_cfg.get("segmentation_batch_size", DEFAULTS_8GB["segmentation_batch_size"]),
        "embedding_batch_size": inf_cfg.get("embedding_batch_size", DEFAULTS_8GB["embedding_batch_size"]),
        "device": inf_cfg.get("device", "auto"),
        "segmentation_model": inf_cfg.get("segmentation_model"),
        "model": inf_cfg.get("model", "pyannote/speaker-diarization-3.1"),
        "hf_token_env": inf_cfg.get("hf_token_env", "HF_TOKEN"),
    }

    parser = argparse.ArgumentParser(
        description="Speaker diarization using pyannote (8GB GPU optimized)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  %(prog)s enhanced.wav video123 results/diarization

  # With custom threshold
  %(prog)s enhanced.wav video123 results/diarization --threshold 0.55

  # Force CPU
  %(prog)s enhanced.wav video123 results/diarization --device cpu

  # Override segmentation model
  %(prog)s enhanced.wav video123 results/diarization \\
    --segmentation pyannote/segmentation-3.0
        """
    )

    parser.add_argument(
        "audio_path",
        type=Path,
        help="Path to preprocessed audio (mono 16kHz WAV recommended)"
    )
    parser.add_argument(
        "ytid",
        help="Video/YouTube ID for output naming"
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Output directory (will create <output_dir>/<ytid>/)"
    )

    parser.add_argument(
        "--device",
        default=defaults["device"],
        help="Device: auto (default), cuda, cpu, or cuda:0"
    )
    parser.add_argument(
        "--chunk-duration",
        type=float,
        default=defaults["chunk_duration"],
        help=f"Chunk size in seconds (default: {defaults['chunk_duration']})"
    )
    parser.add_argument(
        "--overlap-duration",
        type=float,
        default=defaults["overlap_duration"],
        help=f"Overlap in seconds (default: {defaults['overlap_duration']})"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=defaults["threshold"],
        help=f"Speaker similarity threshold (default: {defaults['threshold']})"
    )
    parser.add_argument(
        "--min-speakers",
        type=int,
        default=defaults["min_speakers"],
        help="Minimum number of speakers (default: auto)"
    )
    parser.add_argument(
        "--max-speakers",
        type=int,
        default=defaults["max_speakers"],
        help="Maximum number of speakers (default: auto)"
    )
    parser.add_argument(
        "--min-cluster-size",
        type=int,
        default=defaults["min_cluster_size"],
        help=f"Minimum segments per speaker for clustering (default: {defaults['min_cluster_size']})"
    )
    parser.add_argument(
        "--segmentation",
        dest="segmentation_model",
        default=defaults["segmentation_model"],
        help="Override segmentation model (e.g., pyannote/segmentation-3.0)"
    )
    parser.add_argument(
        "--model",
        dest="model",
        default=defaults["model"],
        help="Diarization model ID (default: pyannote/speaker-diarization-3.1)"
    )
    parser.add_argument(
        "--segmentation-batch-size",
        type=int,
        default=defaults["segmentation_batch_size"],
        help=f"Segmentation batch size (default: {defaults['segmentation_batch_size']})"
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=defaults["embedding_batch_size"],
        help=f"Embedding batch size (default: {defaults['embedding_batch_size']})"
    )
    parser.add_argument(
        "--hf-token",
        dest="use_auth_token",
        default=True,
        help="HuggingFace token (default: use cached)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress progress messages"
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.audio_path.exists():
        print(f"❌ Error: Audio file not found: {args.audio_path}", file=sys.stderr)
        sys.exit(1)

    # Run diarization
    try:
        metadata = run_diarization(
            audio_path=args.audio_path,
            ytid=args.ytid,
            output_dir=args.output_dir,
            device=args.device,
            chunk_duration=args.chunk_duration,
            overlap_duration=args.overlap_duration,
            threshold=args.threshold,
            min_speakers=args.min_speakers,
            max_speakers=args.max_speakers,
            min_cluster_size=args.min_cluster_size,
            segmentation_batch_size=args.segmentation_batch_size,
            embedding_batch_size=args.embedding_batch_size,
            segmentation_model=args.segmentation_model,
            model=args.model,
            use_auth_token=args.use_auth_token,
            verbose=not args.quiet,
        )

        # Print output paths for scripting
        print(f"TSV: {args.output_dir / args.ytid / 'diarized_timestamps.tsv'}")
        print(f"JSON: {args.output_dir / args.ytid / 'diarization.json'}")

    except Exception as e:
        print(f"❌ Error during diarization: {e}", file=sys.stderr)
        if not args.quiet:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
