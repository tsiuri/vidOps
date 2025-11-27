#!/usr/bin/env python3
"""
Utility to create reference clips for speaker identification.

Generates short audio clips sampled from the full audio, then prompts the user
to mark which clips belong to a target speaker.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import List, Tuple


SUPPORTED_EXTS = [
    ".opus",
    ".m4a",
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".webm",
    ".mp4",
    ".mkv",
]


def find_audio(root: Path, ytid: str) -> Path | None:
    audio_dir = root / "pull"
    if not audio_dir.exists():
        return None
    candidates: List[Path] = []
    for ext in SUPPORTED_EXTS:
        candidates.extend(sorted(audio_dir.glob(f"{ytid}__*{ext}")))
        candidates.extend(sorted(audio_dir.glob(f"{ytid}{ext}")))
    return candidates[0] if candidates else None


def probe_duration(audio_path: Path) -> float:
    """Return duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def sample_windows(duration: float, n: int, clip_len: float) -> List[Tuple[float, float]]:
    if duration <= clip_len:
        return [(0.0, duration)]
    windows = []
    for _ in range(n):
        start = random.uniform(0, max(0.0, duration - clip_len))
        end = start + clip_len
        windows.append((start, min(end, duration)))
    return windows


def cut_clips(audio_path: Path, windows: List[Tuple[float, float]], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_paths: List[Path] = []
    for idx, (start, end) in enumerate(windows, 1):
        clip_path = out_dir / f"{idx}.wav"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start}",
            "-to",
            f"{end}",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            "-y",
            str(clip_path),
        ]
        subprocess.run(cmd, check=True)
        out_paths.append(clip_path)
    return out_paths


def prompt_selection(clip_paths: List[Path], speaker_name: str) -> List[Path]:
    print("\nReference clips generated:")
    for p in clip_paths:
        print(f"  [{p.stem}] {p}")
    chosen = input(f"\nEnter clip numbers (comma or space separated) that belong to '{speaker_name}' (blank for none): ").strip()
    if not chosen:
        return []
    import re
    numbers = {c.strip() for c in re.split(r"[\\s,]+", chosen) if c.strip()}
    selected = [p for p in clip_paths if p.stem in numbers]
    return selected


def build_reference(project_root: Path, ytid: str, audio_path: Path, n_clips: int = 10, clip_duration: float = 6.0):
    """Create reference clips and return paths + speaker name + chosen clips."""
    duration = probe_duration(audio_path)
    windows = sample_windows(duration, n_clips, clip_duration)
    ref_dir = project_root / "generated" / "diary_reference" / ytid
    clips_dir = ref_dir / "clips"
    clips = cut_clips(audio_path, windows, clips_dir)
    speaker_name = input("Enter speaker name for this reference: ").strip() or "speaker"
    chosen = prompt_selection(clips, speaker_name)
    meta = {
        "ytid": ytid,
        "speaker_name": speaker_name,
        "audio_source": str(audio_path),
        "clips_generated": [str(p) for p in clips],
        "clips_selected": [str(p) for p in chosen],
    }
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "reference.json").write_text(
        __import__("json").dumps(meta, indent=2),
        encoding="utf-8",
    )
    print(f"\nReference saved to {ref_dir}")
    return meta


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build reference clips for a speaker")
    parser.add_argument("--ytid", required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--audio", type=Path, help="Optional audio override")
    parser.add_argument("--clips", type=int, default=10, help="Number of random clips")
    parser.add_argument("--clip-duration", type=float, default=6.0, help="Clip length in seconds")
    args = parser.parse_args()

    root = args.project_root
    audio_path = args.audio or find_audio(root, args.ytid)
    if not audio_path:
        print("Audio not found; provide --audio.", file=__import__("sys").stderr)
        raise SystemExit(1)
    build_reference(root, args.ytid, audio_path, n_clips=args.clips, clip_duration=args.clip_duration)
