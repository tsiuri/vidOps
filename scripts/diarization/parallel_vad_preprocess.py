#!/usr/bin/env python3
"""
Parallel VAD preprocessing for batch diarization.
Runs canonicalization + VAD on multiple CPU cores in parallel.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from multiprocessing import Pool, cpu_count
from datetime import datetime
from typing import Optional, List, Tuple
import argparse

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


def find_audio_file(ytid: str, pull_dir: Path) -> Optional[Path]:
    """Find audio file for YTID in pull directory."""
    exts = ["opus", "mp4", "webm", "mkv", "m4a", "mp3", "wav"]
    for ext in exts:
        # Try with full filename pattern
        matches = list(pull_dir.glob(f"{ytid}__*.{ext}"))
        if matches:
            return matches[0]

        # Try without __date pattern
        matches = list(pull_dir.glob(f"{ytid}.{ext}"))
        if matches:
            return matches[0]

    return None


def preprocess_video(args: Tuple[str, Path, Path, Path, bool, float]) -> dict:
    """
    Preprocess a single video: canonicalization + VAD.

    Args:
        args: (ytid, pull_dir, output_base, venv_python, verbose, chunk_duration)

    Returns:
        Result dictionary
    """
    ytid, pull_dir, output_base, venv_python, verbose, chunk_duration = args

    start_time = datetime.now()
    result = {
        "ytid": ytid,
        "success": False,
        "error": None,
        "elapsed": 0,
    }

    try:
        # Find audio file
        audio_path = find_audio_file(ytid, pull_dir)
        if not audio_path:
            result["error"] = "Audio file not found"
            return result

        # Create output directory
        output_dir = output_base / ytid
        output_dir.mkdir(parents=True, exist_ok=True)

        canonical_path = output_dir / "canonical.wav"
        vad_segments = output_dir / "vad_segments.json"
        vad_mask = output_dir / "vad_mask.wav"

        # Skip if already processed
        if canonical_path.exists() and vad_segments.exists() and vad_mask.exists():
            result["success"] = True
            result["skipped"] = True
            result["elapsed"] = (datetime.now() - start_time).total_seconds()
            return result

        # Step 1: Canonicalization (OPUS → 16kHz mono WAV)
        if not canonical_path.exists():
            temp_path = output_dir / "canonical_temp.wav"

            # First: Convert and process audio
            ffmpeg_cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(audio_path),
                "-ac", "1",  # mono
                "-ar", "16000",  # 16kHz
                "-sample_fmt", "s16",
                "-af", "highpass=f=70,areverse,silenceremove=start_periods=1:start_silence=0.2:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_silence=0.2:start_threshold=-50dB,loudnorm=I=-23:TP=-2.0:LRA=7",
                str(temp_path)
            ]
            subprocess.run(ffmpeg_cmd, check=True, capture_output=True)

            # Second: Pad to align with chunk boundaries (for PyAnnote compatibility)
            # This prevents "X samples instead of Y" errors during diarization
            sample_rate = 16000
            chunk_samples = int(chunk_duration * sample_rate)  # 240,000 samples

            # Use ffprobe to get exact sample count
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=duration_ts",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(temp_path)
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
            current_samples = int(probe_result.stdout.strip()) if probe_result.stdout.strip().isdigit() else 0

            if current_samples > 0:
                # Calculate padding needed
                remainder = current_samples % chunk_samples
                if remainder != 0:
                    padding_samples = chunk_samples - remainder
                    padding_duration = padding_samples / sample_rate

                    # Pad with silence using apad filter
                    pad_cmd = [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(temp_path),
                        "-af", f"apad=pad_dur={padding_duration}",
                        str(canonical_path)
                    ]
                    subprocess.run(pad_cmd, check=True, capture_output=True)
                    temp_path.unlink()  # Remove temp file
                else:
                    # No padding needed, just rename
                    temp_path.rename(canonical_path)
            else:
                # Fallback: just use the temp file
                temp_path.rename(canonical_path)

        # Step 2: VAD (WebRTC)
        if not (vad_segments.exists() and vad_mask.exists()):
            vad_script = f"""
import json, numpy as np, webrtcvad
from pathlib import Path

try:
    import soundfile as sf
    # Read as int16 to keep memory low and avoid float64 blow-up on long files
    waveform, sr = sf.read('{canonical_path}', dtype="int16")
except ImportError:
    import torchaudio
    waveform_t, sr = torchaudio.load('{canonical_path}')
    waveform_np = waveform_t.numpy()
    waveform = waveform_np[0] if waveform_np.ndim > 1 else waveform_np
    # Ensure int16 for VAD PCM; torchaudio returns float32 in [-1,1]
    if waveform.dtype != np.int16:
        waveform = (waveform * 32767.0).astype(np.int16)

vad = webrtcvad.Vad(3)
frame_duration_ms = 30
frame_length = int(sr * frame_duration_ms / 1000)
if waveform.dtype == np.int16:
    pcm_data = waveform.tobytes()
else:
    pcm_data = (waveform * 32767).astype(np.int16).tobytes()

is_speech_frame = []
for offset in range(0, len(pcm_data) - frame_length * 2, frame_length * 2):
    frame = pcm_data[offset:offset + frame_length * 2]
    if len(frame) < frame_length * 2:
        break
    is_speech_frame.append(vad.is_speech(frame, sr))

segments = []
in_segment = False
seg_start = 0
for i, is_speech in enumerate(is_speech_frame):
    time_sec = i * frame_duration_ms / 1000.0
    if is_speech and not in_segment:
        seg_start = time_sec
        in_segment = True
    elif not is_speech and in_segment:
        segments.append({{"start": seg_start, "end": time_sec}})
        in_segment = False
if in_segment:
    segments.append({{"start": seg_start, "end": len(waveform) / sr}})

Path('{vad_segments}').write_text(json.dumps(segments, indent=2))

# Create VAD mask
mask = np.zeros_like(waveform)
for seg in segments:
    s, e = int(seg["start"] * sr), int(seg["end"] * sr)
    mask[s:e] = waveform[s:e]

try:
    import soundfile as sf
    sf.write('{vad_mask}', mask, sr)
except ImportError:
    import torchaudio, torch
    torchaudio.save('{vad_mask}', torch.from_numpy(mask).unsqueeze(0), sr)

# Create speech-only audio
speech = []
offsets = []
cursor = 0
for seg in segments:
    s, e = int(seg["start"]*sr), int(seg["end"]*sr)
    chunk = waveform[s:e]
    offsets.append({{
        "src_start": seg["start"],
        "src_end": seg["end"],
        "dst_start": cursor/sr,
        "dst_end": (cursor+len(chunk))/sr
    }})
    speech.append(chunk)
    cursor += len(chunk)

if speech:
    speech_wav = np.concatenate(speech)
    try:
        import soundfile as sf
        sf.write('{output_dir}/vad_speech.wav', speech_wav, sr)
    except ImportError:
        import torchaudio, torch
        torchaudio.save('{output_dir}/vad_speech.wav', torch.from_numpy(speech_wav).unsqueeze(0), sr)

    Path('{output_dir}/vad_offset_map.tsv').write_text(
        "\\n".join(f"{{o['src_start']}}\\t{{o['src_end']}}\\t{{o['dst_start']}}\\t{{o['dst_end']}}" for o in offsets)
    )
"""

            subprocess.run(
                [str(venv_python), "-c", vad_script],
                check=True,
                capture_output=True,
                text=True
            )

        result["success"] = True
        result["elapsed"] = (datetime.now() - start_time).total_seconds()

    except Exception as e:
        result["error"] = str(e)
        result["elapsed"] = (datetime.now() - start_time).total_seconds()

    return result


def parallel_vad_preprocess(
    ytids_file: Path,
    pull_dir: Path,
    output_base: Path,
    venv_python: Path,
    num_workers: int = None,
    chunk_duration: float = 15.0,
    verbose: bool = True,
) -> dict:
    """
    Run VAD preprocessing in parallel across multiple CPU cores.

    Args:
        ytids_file: File with YTIDs (one per line or TSV)
        pull_dir: Directory with audio files
        output_base: Base output directory (generated/diarization_inputs)
        venv_python: Path to venv Python binary
        num_workers: Number of parallel workers (default: cpu_count - 2)
        chunk_duration: Chunk duration in seconds for padding alignment
        verbose: Print progress

    Returns:
        Summary statistics
    """
    # Load YTIDs
    ytids = []
    with ytids_file.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ytid = line.split("\t")[0]
            ytids.append(ytid)

    if num_workers is None:
        num_workers = max(1, cpu_count() - 2)

    if verbose:
        print(f"[*] Parallel VAD Preprocessing")
        print(f"   YTIDs file: {ytids_file}")
        print(f"   Total videos: {len(ytids)}")
        print(f"   Pull directory: {pull_dir}")
        print(f"   Output: {output_base}")
        print(f"   Workers: {num_workers} parallel processes")
        print()

    # Prepare arguments for workers
    tasks = [(ytid, pull_dir, output_base, venv_python, verbose, chunk_duration) for ytid in ytids]

    # Process in parallel
    stats = {
        "total": len(ytids),
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "timings": [],
    }

    with Pool(processes=num_workers) as pool:
        if HAS_TQDM and verbose:
            results = list(tqdm(
                pool.imap(preprocess_video, tasks),
                total=len(tasks),
                desc="Preprocessing"
            ))
        else:
            results = pool.map(preprocess_video, tasks)

    # Collect statistics
    for result in results:
        if result.get("skipped"):
            stats["skipped"] += 1
        elif result["success"]:
            stats["processed"] += 1
            stats["timings"].append(result["elapsed"])
        else:
            stats["failed"] += 1
            stats["errors"].append({
                "ytid": result["ytid"],
                "error": result["error"]
            })

    # Print summary
    if verbose:
        print(f"\n[✓] Preprocessing Complete!")
        print(f"   Total: {stats['total']}")
        print(f"   Processed: {stats['processed']}")
        print(f"   Skipped (cached): {stats['skipped']}")
        print(f"   Failed: {stats['failed']}")

        if stats["timings"]:
            import statistics
            avg_time = statistics.mean(stats["timings"])
            med_time = statistics.median(stats["timings"])
            print(f"\n   Average time: {avg_time:.1f}s per file")
            print(f"   Median time: {med_time:.1f}s per file")

        if stats["errors"]:
            print(f"\n[!] Errors ({len(stats['errors'])}):")
            for err in stats["errors"][:10]:
                print(f"   {err['ytid']}: {err['error']}")
            if len(stats["errors"]) > 10:
                print(f"   ... and {len(stats['errors']) - 10} more")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Parallel VAD preprocessing for batch diarization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preprocess with 10 workers
  %(prog)s ytids.txt --workers 10

  # Custom directories
  %(prog)s ytids.txt --pull-dir pull --output generated/diarization_inputs
        """
    )

    parser.add_argument("ytids_file", type=Path, help="File with YTIDs")
    parser.add_argument(
        "--pull-dir",
        type=Path,
        default=Path("pull"),
        help="Directory with audio files (default: pull)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated/diarization_inputs"),
        help="Output base directory (default: generated/diarization_inputs)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        help=f"Number of parallel workers (default: {cpu_count() - 2})"
    )
    parser.add_argument(
        "--chunk-duration",
        type=float,
        default=15.0,
        help="Chunk duration in seconds for padding alignment (default: 15.0; keep in sync with diarization)"
    )
    parser.add_argument(
        "--venv",
        type=Path,
        default=Path(os.environ.get("VIRTUAL_ENV", ".venv")) / "bin" / "python",
        help="Path to venv Python binary"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress")

    args = parser.parse_args()

    # Validate
    if not args.ytids_file.exists():
        print(f"Error: YTIDs file not found: {args.ytids_file}", file=sys.stderr)
        sys.exit(1)

    if not args.pull_dir.exists():
        print(f"Error: Pull directory not found: {args.pull_dir}", file=sys.stderr)
        sys.exit(1)

    if not args.venv.exists():
        print(f"Error: Python venv not found: {args.venv}", file=sys.stderr)
        sys.exit(1)

    # Run preprocessing
    try:
        stats = parallel_vad_preprocess(
            ytids_file=args.ytids_file,
            pull_dir=args.pull_dir,
            output_base=args.output,
            venv_python=args.venv,
            num_workers=args.workers,
            chunk_duration=args.chunk_duration,
            verbose=not args.quiet,
        )

        sys.exit(0 if stats["failed"] == 0 else 1)

    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
