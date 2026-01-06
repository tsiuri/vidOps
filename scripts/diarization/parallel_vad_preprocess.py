#!/usr/bin/env python3
"""
Parallel VAD preprocessing for batch diarization.
Runs canonicalization + VAD on multiple CPU cores in parallel.
"""

import os
import sys
import json
import subprocess
import signal
from pathlib import Path
from multiprocessing import Pool, cpu_count
from datetime import datetime
from typing import Optional, List, Tuple
import argparse
import time

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


ACTIVE_PIDS: set[int] = set()


def _collect_descendants(pid: int) -> list[int]:
    """Collect descendant PIDs via /proc."""
    descendants: list[int] = []
    try:
        children_path = f"/proc/{pid}/task/{pid}/children"
        with open(children_path, "r") as f:
            data = f.read().strip()
        for child_str in data.split():
            try:
                child_pid = int(child_str)
            except ValueError:
                continue
            descendants.append(child_pid)
            descendants.extend(_collect_descendants(child_pid))
    except FileNotFoundError:
        return descendants
    except Exception:
        return descendants
    return descendants


def _kill_descendants(pid: int):
    """Best-effort SIGTERM->SIGKILL for descendant processes."""
    to_kill = _collect_descendants(pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for child in to_kill:
            try:
                os.kill(child, sig)
            except ProcessLookupError:
                continue
        time.sleep(0.25)


def _kill_active():
    """Kill any tracked subprocess PIDs (ffmpeg, ffprobe, etc.)."""
    global ACTIVE_PIDS
    pids = list(ACTIVE_PIDS)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                continue
            except PermissionError:
                try:
                    os.kill(pid, sig)
                except Exception:
                    continue
        time.sleep(0.2)
    ACTIVE_PIDS = set()


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

    # Normalize chunk duration to a sane default if config passed None/0.
    try:
        chunk_duration = float(chunk_duration) if chunk_duration is not None else 15.0
    except Exception:
        chunk_duration = 15.0
    if chunk_duration <= 0:
        chunk_duration = 15.0

    start_time = datetime.now()
    result = {
        "ytid": ytid,
        "success": False,
        "error": None,
        "elapsed": 0,
    }

    def run_cmd(cmd: list, timeout: Optional[float] = None, **kwargs):
        """
        Run a subprocess and ensure it is terminated on interrupts.
        Start it in its own process group and track the PID for cleanup.
        
        Args:
            cmd: Command to run
            timeout: Optional timeout in seconds (default: None = no timeout)
            **kwargs: Additional arguments to pass to Popen
        """
        # Set reasonable defaults for common commands
        if timeout is None:
            if 'ffmpeg' in str(cmd):
                timeout = 3600  # 1 hour for ffmpeg (should be much faster)
            elif 'ffprobe' in str(cmd):
                timeout = 60  # 1 minute for ffprobe
            else:
                timeout = 1800  # 30 minutes default
        
        # Log command start for debugging
        if verbose:
            cmd_str = ' '.join(str(c) for c in cmd[:5])
            if len(cmd) > 5:
                cmd_str += '...'
            print(f"[{ytid}] Executing: {cmd_str}")
        
        try:
            # Check if command exists
            import shutil
            cmd_path = shutil.which(cmd[0])
            if not cmd_path:
                raise FileNotFoundError(f"Command not found: {cmd[0]}")
            
            if verbose:
                print(f"[{ytid}] Starting process: {cmd_path}")
                import sys
                sys.stdout.flush()
            
            proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
            ACTIVE_PIDS.add(proc.pid)
            
            if verbose:
                print(f"[{ytid}] Process started: PID {proc.pid}")
                import sys
                sys.stdout.flush()
            
            try:
                # Use poll() to check if process is still running before communicate
                # This helps detect if process exits immediately
                import time
                time.sleep(0.1)  # Brief pause to let process start
                if proc.poll() is not None:
                    # Process already finished
                    stdout, stderr = proc.communicate()
                else:
                    stdout, stderr = proc.communicate(timeout=timeout)
                
                if verbose:
                    print(f"[{ytid}] Process completed: returncode={proc.returncode}")
                
            except subprocess.TimeoutExpired:
                if verbose:
                    print(f"[{ytid}] Process timed out, killing...")
                proc.kill()
                proc.wait(timeout=5)
                raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(cmd[:3])}...")
            except KeyboardInterrupt:
                _kill_active()
                raise
            finally:
                ACTIVE_PIDS.discard(proc.pid)
                
            if proc.returncode != 0:
                error_preview = (stderr[:200] if stderr else stdout[:200] if stdout else "No error output")
                if verbose:
                    print(f"[{ytid}] Process failed with returncode {proc.returncode}: {error_preview}")
                raise subprocess.CalledProcessError(proc.returncode, cmd, output=stdout, stderr=stderr)
            return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        except FileNotFoundError as e:
            raise RuntimeError(f"Command not found: {cmd[0]} - {e}") from e
        except Exception as e:
            if verbose:
                print(f"[{ytid}] Error executing command: {e}")
            raise

    try:
        # Find audio file
        if verbose:
            print(f"[{ytid}] Looking for audio file in {pull_dir}...")
        audio_path = find_audio_file(ytid, pull_dir)
        if not audio_path:
            result["error"] = f"Audio file not found in {pull_dir}"
            if verbose:
                print(f"[{ytid}] ERROR: Audio file not found")
            return result
        
        if verbose:
            print(f"[{ytid}] Found audio: {audio_path} ({audio_path.stat().st_size / (1024*1024):.1f} MB)")

        # Create output directory
        output_dir = output_base / ytid
        output_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            print(f"[{ytid}] Output directory: {output_dir}")

        canonical_path = output_dir / "canonical.wav"
        vad_segments = output_dir / "vad_segments.json"

        # Skip if already processed (vad_mask.wav not required - segments-only mode)
        if canonical_path.exists() and vad_segments.exists():
            result["success"] = True
            result["skipped"] = True
            result["elapsed"] = (datetime.now() - start_time).total_seconds()
            return result

        # Step 1: Canonicalization (OPUS → 16kHz mono WAV)
        if not canonical_path.exists():
            temp_path = output_dir / "canonical_temp.wav"

            # First: Convert and process audio
            # NOTE: Removed areverse filters to prevent memory bloat on long files.
            # areverse requires loading the entire file into memory, which causes 12GB+ usage
            # on 3-4 hour files. Using silenceremove only from start (not end) to avoid this.
            # If trailing silence removal is critical, it should be done in a separate pass
            # or using a streaming approach.
            # Use simpler filters to avoid memory issues:
            # - Removed loudnorm (requires full file analysis, can hang/crash on large files)
            # - Use volume normalization instead (streaming-compatible, no memory bloat)
            # - Removed areverse (requires full file in memory)
            # - Limit ffmpeg memory with thread_queue_size to prevent buffering too much
            ffmpeg_cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-thread_queue_size", "512",  # Limit input buffer to prevent memory bloat
                "-i", str(audio_path),
                "-ac", "1",  # mono
                "-ar", "16000",  # 16kHz
                "-sample_fmt", "s16",
                # Simple streaming filters only (all process in real-time, no full-file buffering):
                # highpass: streaming filter
                # silenceremove: streaming filter (leading only)
                # volume: streaming normalization (replaces loudnorm, no analysis pass needed)
                "-af", "highpass=f=70,silenceremove=start_periods=1:start_silence=0.2:start_threshold=-50dB,volume=-23dB",
                "-threads", "2",  # Limit threads to reduce memory usage
                str(temp_path)
            ]
            if verbose:
                print(f"[{ytid}] Running ffmpeg canonicalization...")
                import sys
                sys.stdout.flush()  # Force output before blocking call
            
            # Use DEVNULL for stderr to avoid potential deadlocks
            # ffmpeg with -loglevel error shouldn't output much anyway
            run_cmd(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3600)
            
            if verbose:
                print(f"[{ytid}] ffmpeg canonicalization completed")
                import sys
                sys.stdout.flush()

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
            probe_result = run_cmd(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
                    run_cmd(pad_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    temp_path.unlink()  # Remove temp file
                else:
                    # No padding needed, just rename
                    temp_path.rename(canonical_path)
            else:
                # Fallback: just use the temp file
                temp_path.rename(canonical_path)

        # Step 2: VAD (WebRTC) - streaming chunks to minimize memory
        if not vad_segments.exists():
            if verbose:
                print(f"[{ytid}] Starting VAD processing...")
            canonical_literal = json.dumps(canonical_path.as_posix())
            vad_literal = json.dumps(vad_segments.as_posix())
            vad_script = f"""
import json, numpy as np, webrtcvad
from pathlib import Path
import sys

# Streaming VAD: process audio in chunks to avoid loading entire file into RAM
# For a 3-4 hour file, this prevents 32GB+ memory usage

try:
    import soundfile as sf  # prefer int16, mono
    HAS_SOUNDFILE = True
except ImportError:
    HAS_SOUNDFILE = False
    import torchaudio

# Open file and get metadata without loading all data
if HAS_SOUNDFILE:
    with sf.SoundFile({canonical_literal}) as f:
        sr = f.samplerate
        total_frames = len(f)
        channels = f.channels
        
        # Process in chunks (1MB chunks = ~500k samples at 16kHz = ~31 seconds)
        chunk_samples = 500000  # ~31 seconds at 16kHz
        vad = webrtcvad.Vad(3)
        frame_duration_ms = 30
        frame_length = int(sr * frame_duration_ms / 1000)
        frame_bytes = frame_length * 2  # int16 = 2 bytes per sample
        
        segments = []
        in_segment = False
        seg_start = 0.0
        current_time = 0.0
        
        # Stream through file in chunks - read sequentially (soundfile reads from current position)
        while True:
            chunk = f.read(chunk_samples, dtype='int16', always_2d=True)
            
            # Check if we got any data (EOF)
            if chunk.size == 0:
                break
            
            # Force mono (take first channel)
            if chunk.ndim == 2 and chunk.shape[1] > 1:
                chunk = chunk[:, 0:1]
            elif chunk.ndim == 2:
                chunk = chunk[:, 0]
            
            # Get actual number of samples read
            num_samples = len(chunk) if chunk.ndim == 1 else chunk.shape[0]
            
            # Convert to PCM bytes for VAD
            if chunk.ndim == 2:
                pcm = chunk.flatten().tobytes()
            else:
                pcm = chunk.tobytes()
            
            # Process frames in this chunk
            for idx in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
                frame = pcm[idx:idx + frame_bytes]
                if len(frame) < frame_bytes:
                    break
                is_speech = vad.is_speech(frame, sr)
                t = current_time + (idx // 2) / sr
                
                if is_speech and not in_segment:
                    seg_start = t
                    in_segment = True
                elif not is_speech and in_segment:
                    segments.append({{"start": seg_start, "end": t}})
                    in_segment = False
            
            # Update time based on actual samples read
            current_time += num_samples / sr
            
            # Free chunk memory immediately
            del chunk, pcm
        
        # Close any open segment
        if in_segment:
            segments.append({{"start": seg_start, "end": current_time}})
else:
    # Fallback: torchaudio (less memory efficient but still chunked)
    import torchaudio
    waveform_t, sr = torchaudio.load({canonical_literal})
    waveform = waveform_t.numpy().T  # [time, channel]
    if waveform.dtype != np.int16:
        waveform = (waveform * 32767.0).astype(np.int16)
    
    # Force mono
    if waveform.ndim == 2 and waveform.shape[1] > 1:
        waveform = waveform[:, 0:1]
    
    vad = webrtcvad.Vad(3)
    frame_duration_ms = 30
    frame_length = int(sr * frame_duration_ms / 1000)
    
    segments = []
    in_segment = False
    seg_start = 0.0
    total_frames = waveform.shape[0]
    
    # Process frames
    pcm = waveform.tobytes()
    for idx in range(0, len(pcm) - frame_length * 2 + 1, frame_length * 2):
        frame = pcm[idx:idx + frame_length * 2]
        is_speech = vad.is_speech(frame, sr)
        t = (idx // 2) / sr
        if is_speech and not in_segment:
            seg_start = t
            in_segment = True
        elif not is_speech and in_segment:
            segments.append({{"start": seg_start, "end": t}})
            in_segment = False
    if in_segment:
        segments.append({{"start": seg_start, "end": total_frames / sr}})

Path({vad_literal}).write_text(json.dumps(segments, indent=2))
"""

            # Run VAD script with timeout to prevent hangs
            # For a 4-hour file at 16kHz, processing should take < 5 minutes
            # Set timeout to 30 minutes to be safe
            try:
                if verbose:
                    print(f"[{ytid}] Running VAD script...")
                proc = subprocess.Popen(
                    [str(venv_python), "-c", vad_script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                ACTIVE_PIDS.add(proc.pid)
                try:
                    stdout, stderr = proc.communicate(timeout=1800)  # 30 minute timeout
                    if proc.returncode != 0:
                        error_msg = stderr[:500] if stderr else "Unknown error"
                        raise RuntimeError(f"VAD script failed: {error_msg}")
                    if verbose:
                        print(f"[{ytid}] VAD script completed")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise RuntimeError("VAD script timed out after 30 minutes")
                finally:
                    ACTIVE_PIDS.discard(proc.pid)
            except Exception as e:
                raise RuntimeError(f"VAD script failed: {e}") from e

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

    # If caller explicitly passed null/None (e.g., from YAML), fall back to the diarization default.
    if chunk_duration is None:
        chunk_duration = 15.0

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

    # If only one worker, run serially to simplify cleanup
    if num_workers == 1:
        results = []
        for i, task in enumerate(tasks):
            if verbose:
                print(f"Processing task {i+1}/{len(tasks)}: {task[0]}")
            try:
                res = preprocess_video(task)
                results.append(res)
                if verbose:
                    status = "✓" if res.get("success") else "✗"
                    print(f"{status} Task {i+1} completed: {task[0]}")
            except KeyboardInterrupt:
                _kill_active()
                raise
            except Exception as e:
                if verbose:
                    print(f"✗ Task {i+1} failed: {task[0]} - {e}")
                results.append({"ytid": task[0], "success": False, "error": str(e), "elapsed": 0})
        # mimic pool summary path
        stats = {
            "total": len(ytids),
            "processed": sum(1 for r in results if r.get("success") and not r.get("skipped")),
            "skipped": sum(1 for r in results if r.get("skipped")),
            "failed": sum(1 for r in results if not r.get("success")),
            "errors": [{"ytid": r.get("ytid"), "error": r.get("error")} for r in results if not r.get("success")],
            "timings": [r.get("elapsed") for r in results if r.get("elapsed")],
        }
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
        try:
            if HAS_TQDM and verbose:
                results = list(tqdm(
                    pool.imap(preprocess_video, tasks),
                    total=len(tasks),
                    desc="Preprocessing"
                ))
            else:
                results = pool.map(preprocess_video, tasks)
        except KeyboardInterrupt:
            pool.terminate()
            pool.join()
            _kill_descendants(os.getpid())
            _kill_active()
            raise
        except Exception:
            pool.terminate()
            pool.join()
            _kill_descendants(os.getpid())
            _kill_active()
            raise
        finally:
            _kill_descendants(os.getpid())
            _kill_active()

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
