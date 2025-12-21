#!/usr/bin/env python3
"""
Chunk long audio files into smaller segments for memory-efficient diarization.
Splits files into ~1 hour chunks with 30s overlap to preserve speaker continuity.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional
import argparse


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    duration = float(result.stdout.strip())
    return duration


def chunk_audio(
    audio_path: Path,
    output_dir: Path,
    chunk_duration: float = 3600.0,  # 1 hour
    overlap: float = 30.0,  # 30 seconds overlap
    verbose: bool = True
) -> List[dict]:
    """
    Split audio file into chunks with overlap.

    Args:
        audio_path: Path to source audio file
        output_dir: Directory for chunk outputs
        chunk_duration: Target chunk duration in seconds (default: 3600 = 1 hour)
        overlap: Overlap between chunks in seconds (default: 30s)
        verbose: Print progress

    Returns:
        List of chunk metadata dicts with keys:
            - chunk_index: Chunk number (0-based)
            - chunk_path: Path to chunk file
            - start_time: Start time in original file (seconds)
            - end_time: End time in original file (seconds)
            - duration: Actual chunk duration (seconds)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get total duration
    total_duration = get_audio_duration(audio_path)

    if verbose:
        print(f"[*] Chunking audio: {audio_path.name}")
        print(f"    Total duration: {total_duration:.1f}s ({total_duration/3600:.2f} hours)")
        print(f"    Chunk size: {chunk_duration:.1f}s, Overlap: {overlap:.1f}s")

    # If file is shorter than chunk size, no chunking needed
    if total_duration <= chunk_duration:
        if verbose:
            print(f"    File is shorter than chunk size, no chunking needed")
        return [{
            "chunk_index": 0,
            "chunk_path": str(audio_path),
            "start_time": 0.0,
            "end_time": total_duration,
            "duration": total_duration,
            "is_original": True,
        }]

    # Calculate chunk positions
    chunks = []
    chunk_index = 0
    current_start = 0.0

    while current_start < total_duration:
        # Calculate chunk end (with overlap for all but last chunk)
        current_end = min(current_start + chunk_duration, total_duration)

        # Determine actual chunk duration
        chunk_dur = current_end - current_start

        # Generate chunk filename
        chunk_filename = f"chunk_{chunk_index:03d}.wav"
        chunk_path = output_dir / chunk_filename

        # Extract chunk using ffmpeg
        # Use -ss before -i for fast seek, -t for duration
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(current_start),
            "-t", str(chunk_dur),
            "-i", str(audio_path),
            "-c", "copy",  # Copy codec if WAV, otherwise re-encode
            str(chunk_path)
        ]

        if verbose:
            print(f"    Extracting chunk {chunk_index}: {current_start:.1f}s - {current_end:.1f}s ({chunk_dur:.1f}s)")

        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            # If copy fails (codec mismatch), retry with re-encode
            if verbose:
                print(f"    Copy failed, re-encoding chunk {chunk_index}...")
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(current_start),
                "-t", str(chunk_dur),
                "-i", str(audio_path),
                "-ac", "1",  # Mono
                "-ar", "16000",  # 16kHz
                str(chunk_path)
            ]
            subprocess.run(cmd, check=True, capture_output=True)

        chunks.append({
            "chunk_index": chunk_index,
            "chunk_path": str(chunk_path),
            "start_time": current_start,
            "end_time": current_end,
            "duration": chunk_dur,
            "is_original": False,
        })

        chunk_index += 1

        # Move to next chunk start (with overlap)
        # For next chunk, start (chunk_duration - overlap) seconds from current start
        current_start += chunk_duration - overlap

        # Ensure we don't create tiny chunks at the end
        remaining = total_duration - current_start
        if 0 < remaining < overlap * 2:
            # Extend previous chunk to cover remaining time
            if chunks:
                last_chunk = chunks[-1]
                last_chunk["end_time"] = total_duration
                last_chunk["duration"] = last_chunk["end_time"] - last_chunk["start_time"]
            break

    if verbose:
        print(f"    Created {len(chunks)} chunk(s)")

    # Save chunk metadata
    metadata_path = output_dir / "chunks_metadata.json"
    metadata = {
        "source_audio": str(audio_path),
        "total_duration": total_duration,
        "chunk_duration": chunk_duration,
        "overlap": overlap,
        "num_chunks": len(chunks),
        "chunks": chunks,
    }

    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    if verbose:
        print(f"    Saved metadata: {metadata_path}")

    return chunks


def main():
    parser = argparse.ArgumentParser(
        description="Chunk long audio files for memory-efficient diarization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Chunk 4-hour file into 1-hour segments
  %(prog)s long_audio.wav output_dir/

  # Custom chunk size (2 hours)
  %(prog)s long_audio.wav output_dir/ --chunk-duration 7200

  # Custom overlap (60 seconds)
  %(prog)s long_audio.wav output_dir/ --overlap 60
        """
    )

    parser.add_argument("audio_path", type=Path, help="Input audio file")
    parser.add_argument("output_dir", type=Path, help="Output directory for chunks")
    parser.add_argument(
        "--chunk-duration",
        type=float,
        default=3600.0,
        help="Chunk duration in seconds (default: 3600 = 1 hour)"
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=30.0,
        help="Overlap between chunks in seconds (default: 30)"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress output")

    args = parser.parse_args()

    if not args.audio_path.exists():
        print(f"Error: Audio file not found: {args.audio_path}", file=sys.stderr)
        sys.exit(1)

    try:
        chunks = chunk_audio(
            audio_path=args.audio_path,
            output_dir=args.output_dir,
            chunk_duration=args.chunk_duration,
            overlap=args.overlap,
            verbose=not args.quiet
        )

        # Print chunk paths for scripting
        for chunk in chunks:
            print(chunk["chunk_path"])

        sys.exit(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
