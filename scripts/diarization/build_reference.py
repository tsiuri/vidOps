#!/usr/bin/env python3
"""
Build shared reference clips for speaker identification.
Extracts diverse audio clips from source videos for consistent speaker labeling.
"""
import os
import sys
import json
import random
import argparse
import subprocess
import re
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple


def interactive_clip_selector(clips: List[Tuple[Path, str, float, float]], preselected: Optional[List[Path]] = None):
    """
    Minimal TUI to audition and select clips.
    Controls:
      Up/Down or j/k: navigate
      Space/Enter: play current clip (from start)
      a: toggle selection
      s: stop playback
      q: finish
    Returns a set of selected Paths or None on failure.
    """
    try:
        import curses
    except Exception:
        return None

    selected = set(preselected or [])
    idx = 0
    proc: Optional[subprocess.Popen] = None

    def stop_playback():
        nonlocal proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except Exception:
                proc.kill()
        proc = None

    def play_clip(p: Path):
        nonlocal proc
        stop_playback()
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(p)]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            proc = None

    def draw(stdscr):
        nonlocal idx
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            header = "Arrows/jk: move | Space/Enter: play | a: toggle | s: stop | q: done"
            stdscr.addnstr(0, 0, header, w - 1)
            for i, (clip_path, ytid, s, e) in enumerate(clips):
                prefix = ">" if i == idx else " "
                mark = "[x]" if clip_path in selected else "[ ]"
                line = f"{prefix} {mark} {i+1}. {clip_path.name} ({s:.1f}-{e:.1f}s) {ytid}"
                if i + 1 < h:
                    stdscr.addnstr(i + 1, 0, line, w - 1)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                stop_playback()
                idx = (idx - 1) % len(clips)
            elif ch in (curses.KEY_DOWN, ord("j")):
                stop_playback()
                idx = (idx + 1) % len(clips)
            elif ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
                play_clip(clips[idx][0])
            elif ch == ord("s"):
                stop_playback()
            elif ch == ord("a"):
                clip_path = clips[idx][0]
                if clip_path in selected:
                    selected.remove(clip_path)
                else:
                    selected.add(clip_path)
            elif ch == ord("q"):
                stop_playback()
                break
        return selected

    try:
        import curses
        curses.wrapper(draw)
    except KeyboardInterrupt:
        stop_playback()
        return set(selected)
    except Exception:
        try:
            stop_playback()
        except Exception:
            pass
        return None
    stop_playback()
    return selected


def choose_clips(generated: List[Tuple[Path, str, float, float]]) -> List[Path]:
    """Pick clips interactively (curses) or via manual numbers."""
    chosen: List[Path] = []
    skip_prompts = os.environ.get("BATCH_DIARIZE_SKIP_PROMPT") or not sys.stdin.isatty()
    if not skip_prompts and sys.stdin.isatty():
        try:
            use_ui = input("Use interactive clip selector? [Y/n]: ").strip().lower()
        except EOFError:
            use_ui = "y"
        if use_ui in ("", "y", "yes"):
            ui_selected = interactive_clip_selector(generated)
            if ui_selected:
                chosen = [p for p, _y, _s, _e in generated if p in ui_selected]
    if not chosen:
        if skip_prompts:
            chosen_str = ""
        else:
            try:
                chosen_str = input("Enter clip numbers (comma or space separated) to keep (blank = keep all): ").strip()
            except EOFError:
                chosen_str = ""
        if chosen_str:
            numbers = {c.strip() for c in re.split(r"[\\s,]+", chosen_str) if c.strip()}
            chosen = [p for idx, (p, _y, _s, _e) in enumerate(generated, start=1) if str(idx) in numbers or p.stem in numbers]
    if not chosen:
        # keep all
        chosen = [p for p, _y, _s, _e in generated]
    return chosen


def enhance_clip_inplace(
    clip_path: Path,
    highpass_hz: float = 80.0,
    lowpass_hz: float = 8000.0,
):
    """Apply the same denoise/band-limit chain as the pipeline to a reference clip."""
    try:
        import torch
        import torchaudio
        from denoiser import pretrained
    except Exception as exc:
        print(f"⚠️  Skipping enhancement for {clip_path.name} (missing deps): {exc}", file=sys.stderr)
        return

    wav, sr = torchaudio.load(str(clip_path))
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = pretrained.dns64().to(device)
    model.eval()
    with torch.no_grad():
        out_wav = model(wav.to(device))
    out_wav = out_wav.squeeze(0)

    try:
        out_wav = torchaudio.functional.highpass_biquad(out_wav, sr, cutoff_freq=highpass_hz)
        out_wav = torchaudio.functional.lowpass_biquad(out_wav, sr, cutoff_freq=lowpass_hz)
    except Exception as exc:
        print(f"⚠️  Band-limit failed for {clip_path.name}: {exc}", file=sys.stderr)

    torchaudio.save(str(clip_path), out_wav.cpu(), sr)


def get_audio_duration(file_path: Path) -> float:
    """Get duration of audio/video file using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        print(f"⚠️  Warning: Could not get duration for {file_path.name}: {e}", file=sys.stderr)
        return 0.0


def extract_clip(
    source_path: Path,
    output_path: Path,
    start_time: float,
    duration: float,
    sample_rate: int = 16000,
) -> bool:
    """
    Extract audio clip using ffmpeg.

    Args:
        source_path: Source video/audio file
        output_path: Output WAV file
        start_time: Start time in seconds
        duration: Clip duration in seconds
        sample_rate: Output sample rate (default: 16kHz for pyannote)

    Returns:
        True if successful, False otherwise
    """
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", str(start_time),
        "-t", str(duration),
        "-i", str(source_path),
        "-ac", "1",  # Mono
        "-ar", str(sample_rate),  # 16 kHz
        "-acodec", "pcm_s16le",  # PCM 16-bit
        str(output_path)
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Warning: Failed to extract clip: {e.stderr.decode()}", file=sys.stderr)
        return False


def build_reference_clips(
    source_videos: list,
    reference_name: str,
    output_dir: Path,
    num_clips: int = 50,
    clip_duration: tuple = (8.0, 12.0),
    spacing: float = 30.0,
    sample_rate: int = 16000,
    verbose: bool = True,
) -> dict:
    """
    Extract diverse reference clips from source videos.

    Args:
        source_videos: List of video/audio files to sample from
        reference_name: Name for this reference set
        output_dir: Base directory for references
        num_clips: Target number of clips (default: 50)
        clip_duration: (min, max) duration in seconds (default: 8-12s)
        spacing: Minimum seconds between clip start times (default: 30s)
        sample_rate: Output sample rate (default: 16kHz)
        verbose: Print progress messages

    Returns:
        Metadata dict about the reference set
    """
    ref_dir = output_dir / reference_name
    ref_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"🔨 Building reference: {reference_name}")
        print(f"   Target clips: {num_clips}")
        print(f"   Clip duration: {clip_duration[0]}-{clip_duration[1]}s")
        print(f"   Spacing: {spacing}s")
        print(f"   Sources: {len(source_videos)} files")

    clips_per_source = max(1, (num_clips + len(source_videos) - 1) // len(source_videos))
    generated_clips = []
    clip_metadata = []

    for video_idx, video in enumerate(source_videos, 1):
        if verbose:
            print(f"\n📹 [{video_idx}/{len(source_videos)}] {video.name}")

        # Get duration
        duration = get_audio_duration(video)
        if duration <= 0:
            if verbose:
                print(f"   ⏭️  Skipping (invalid duration)")
            continue

        max_clip_dur = clip_duration[1]
        safe_duration = duration - max_clip_dur

        if safe_duration < spacing:
            if verbose:
                print(f"   ⏭️  Skipping (too short: {duration:.1f}s)")
            continue

        # Generate random start times with spacing constraint
        start_times = []
        for attempt in range(clips_per_source * 10):  # Try harder to get enough clips
            if len(start_times) >= clips_per_source:
                break

            start = random.uniform(0, safe_duration)

            # Check spacing from existing clips
            if all(abs(start - existing) > spacing for existing in start_times):
                start_times.append(start)

        if verbose:
            print(f"   🎯 Extracting {len(start_times)} clips...")

        # Extract clips
        for i, start in enumerate(start_times):
            clip_len = random.uniform(*clip_duration)
            output_clip = ref_dir / f"{video.stem}_ref{len(generated_clips):03d}.wav"

            if extract_clip(video, output_clip, start, clip_len, sample_rate):
                generated_clips.append(output_clip)
                clip_metadata.append({
                    "clip_path": str(output_clip.relative_to(output_dir)),
                    "source": str(video),
                    "start_time": start,
                    "duration": clip_len,
                })

                if len(generated_clips) >= num_clips:
                    break

        if len(generated_clips) >= num_clips:
            if verbose:
                print(f"   ✅ Reached target of {num_clips} clips")
            break

    # Interactive clip selection (reuse TUI from resemblyzer flow)
    if generated_clips:
        selections = [
            (p, meta["source"], meta["start_time"], meta["duration"])
            for p, meta in zip(generated_clips, clip_metadata)
        ]
        chosen_paths = choose_clips(selections)
        keep_set = set(chosen_paths)
        if keep_set and len(keep_set) < len(generated_clips):
            for p in generated_clips:
                if p not in keep_set:
                    p.unlink(missing_ok=True)
            # Filter metadata to keep only chosen clips
            filtered_clips = []
            filtered_meta = []
            for p, meta in zip(generated_clips, clip_metadata):
                if p in keep_set:
                    filtered_clips.append(p)
                    filtered_meta.append(meta)
            generated_clips = filtered_clips
            clip_metadata = filtered_meta

        # Enhance chosen clips to match pipeline preprocessing
        for p in generated_clips:
            enhance_clip_inplace(p)

    # Save metadata
    metadata = {
        "reference_name": reference_name,
        "created": datetime.now().isoformat(),
        "num_clips": len(generated_clips),
        "target_clips": num_clips,
        "clip_duration_range": list(clip_duration),
        "spacing": spacing,
        "sample_rate": sample_rate,
        "source_videos": [str(v) for v in source_videos],
        "clips": clip_metadata,
    }

    metadata_path = ref_dir / "reference.json"
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    if verbose:
        print(f"\n✅ Built {len(generated_clips)} reference clips: {reference_name}")
        print(f"   Output: {ref_dir}")
        print(f"   Metadata: {metadata_path}")

    return metadata


def get_or_build_reference(
    reference_name: str,
    reference_dir: Path,
    source_videos: list = None,
    force_rebuild: bool = False,
    **build_kwargs
) -> Path:
    """
    Get existing reference or build new one.

    Args:
        reference_name: Reference identifier
        reference_dir: Base directory for references
        source_videos: Videos to sample (required if building)
        force_rebuild: Force rebuild even if exists
        **build_kwargs: Additional args for build_reference_clips()

    Returns:
        Path to reference directory
    """
    ref_path = reference_dir / reference_name

    # Check if exists and not forcing rebuild
    if ref_path.exists() and not force_rebuild:
        clips = list(ref_path.glob("*.wav"))
        metadata_file = ref_path / "reference.json"

        print(f"♻️  Reusing existing reference: {reference_name}")
        print(f"   Clips: {len(clips)}")

        if metadata_file.exists():
            with metadata_file.open() as f:
                metadata = json.load(f)
                print(f"   Created: {metadata.get('created', 'unknown')}")

        return ref_path

    # Need to build
    if not source_videos:
        raise ValueError(
            f"Reference '{reference_name}' not found and no source videos provided. "
            "Either provide source_videos or use an existing reference."
        )

    print(f"🔨 Building new reference: {reference_name}")

    build_reference_clips(
        source_videos=source_videos,
        reference_name=reference_name,
        output_dir=reference_dir,
        **build_kwargs
    )

    return ref_path


def main():
    parser = argparse.ArgumentParser(
        description="Build speaker reference clips from source videos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Build from video files
  %(prog)s my_speaker video1.mp4 video2.mp4 video3.mp4

  # Build with custom parameters
  %(prog)s speaker_alpha *.mp4 --num-clips 100 --duration 10 15

  # Check if reference exists
  %(prog)s existing_ref --check-only

  # Force rebuild
  %(prog)s my_speaker *.mp4 --force
        """
    )

    parser.add_argument(
        "reference_name",
        help="Name for this reference set"
    )
    parser.add_argument(
        "source_videos",
        nargs="*",
        type=Path,
        help="Source video/audio files to sample from"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("PROJECT_ROOT", ".")) / "data" / "references",
        help="Base directory for references (default: $PROJECT_ROOT/data/references)"
    )
    parser.add_argument(
        "--num-clips",
        type=int,
        default=50,
        help="Number of clips to extract (default: 50)"
    )
    parser.add_argument(
        "--duration",
        nargs=2,
        type=float,
        default=[8.0, 12.0],
        metavar=("MIN", "MAX"),
        help="Clip duration range in seconds (default: 8 12)"
    )
    parser.add_argument(
        "--spacing",
        type=float,
        default=30.0,
        help="Minimum seconds between clips (default: 30)"
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=16000,
        help="Output sample rate in Hz (default: 16000)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rebuild even if reference exists"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Review/play clips as they are extracted (ffplay required for audio playback)"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check if reference exists, don't build"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress progress messages"
    )

    args = parser.parse_args()

    # Check-only mode
    if args.check_only:
        ref_path = args.output_dir / args.reference_name
        if ref_path.exists():
            clips = list(ref_path.glob("*.wav"))
            print(f"✅ Reference exists: {args.reference_name} ({len(clips)} clips)")
            sys.exit(0)
        else:
            print(f"❌ Reference not found: {args.reference_name}")
            sys.exit(1)

    # Validate source videos
    if not args.source_videos and not (args.output_dir / args.reference_name).exists():
        parser.error("source_videos required when building new reference")

    source_videos = [v for v in args.source_videos if v.exists()]
    if args.source_videos and len(source_videos) != len(args.source_videos):
        missing = set(args.source_videos) - set(source_videos)
        print(f"⚠️  Warning: {len(missing)} source files not found", file=sys.stderr)

    if not source_videos and not args.force:
        # Try to reuse existing
        try:
            ref_path = get_or_build_reference(
                reference_name=args.reference_name,
                reference_dir=args.output_dir,
                source_videos=None,
                force_rebuild=False,
            )
            sys.exit(0)
        except ValueError as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Build reference
    try:
        get_or_build_reference(
            reference_name=args.reference_name,
            reference_dir=args.output_dir,
            source_videos=source_videos,
            force_rebuild=args.force,
            num_clips=args.num_clips,
            clip_duration=tuple(args.duration),
            spacing=args.spacing,
            sample_rate=args.sample_rate,
            verbose=not args.quiet,
        )

        print(f"\n✅ Reference ready: {args.output_dir / args.reference_name}")

    except Exception as e:
        print(f"❌ Error building reference: {e}", file=sys.stderr)
        if not args.quiet:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
