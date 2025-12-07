#!/usr/bin/env python3
"""
Combine diarization results from multiple audio chunks into a single unified output.
Adjusts timestamps and optionally maps speaker labels for continuity.
"""

import os
import sys
import json
import csv
from pathlib import Path
from typing import List, Dict, Optional
import argparse


def load_chunk_metadata(chunks_dir: Path) -> dict:
    """Load chunk metadata from chunks_metadata.json."""
    metadata_path = chunks_dir / "chunks_metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Chunk metadata not found: {metadata_path}")

    with metadata_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_diarization_tsv(tsv_path: Path) -> List[dict]:
    """Load diarization TSV file."""
    rows = []
    with tsv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            rows.append(row)
    return rows


def combine_chunk_results(
    chunks_metadata: dict,
    chunk_results_dir: Path,
    output_dir: Path,
    ytid: str,
    map_speakers: bool = False,
    verbose: bool = True
) -> dict:
    """
    Combine diarization results from multiple chunks.

    Args:
        chunks_metadata: Metadata dict from chunk_audio.py
        chunk_results_dir: Directory containing chunk diarization results
        output_dir: Output directory for combined results
        ytid: Video ID
        map_speakers: Attempt to map speaker labels across chunks (not implemented yet)
        verbose: Print progress

    Returns:
        Combined metadata dict
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    chunks = chunks_metadata["chunks"]
    num_chunks = len(chunks)

    if verbose:
        print(f"[*] Combining {num_chunks} chunk results for {ytid}")

    # If only one chunk, just copy results
    if num_chunks == 1 and chunks[0].get("is_original", False):
        if verbose:
            print(f"    Single chunk (original file), no combination needed")

        # Just copy the single chunk's results
        chunk = chunks[0]
        chunk_dir = chunk_results_dir / ytid

        # Copy TSV files
        for tsv_name in ["diarized_timestamps.tsv", "diarized_timestamps_matched.tsv", "diarized_timestamps_clean.tsv"]:
            src = chunk_dir / tsv_name
            if src.exists():
                dst = output_dir / tsv_name
                import shutil
                shutil.copy2(src, dst)
                if verbose:
                    print(f"    Copied: {tsv_name}")

        # Copy metadata
        src_meta = chunk_dir / "diarization.json"
        if src_meta.exists():
            dst_meta = output_dir / "diarization.json"
            import shutil
            shutil.copy2(src_meta, dst_meta)

        return json.loads(src_meta.read_text(encoding="utf-8"))

    # Combine multiple chunks
    all_segments = []
    chunk_speaker_counts = []
    total_segments = 0
    total_speech_duration = 0.0
    all_speakers = set()

    for chunk_idx, chunk in enumerate(chunks):
        chunk_start_offset = chunk["start_time"]
        chunk_ytid = f"{ytid}_chunk_{chunk_idx:03d}"

        # Find chunk results directory
        chunk_result_dir = chunk_results_dir / chunk_ytid

        if not chunk_result_dir.exists():
            if verbose:
                print(f"    Warning: Chunk {chunk_idx} results not found: {chunk_result_dir}")
            continue

        # Load diarization results (prefer clean > matched > raw)
        tsv_candidates = [
            chunk_result_dir / "diarized_timestamps_clean.tsv",
            chunk_result_dir / "diarized_timestamps_matched.tsv",
            chunk_result_dir / "diarized_timestamps.tsv",
        ]

        chunk_tsv = None
        for candidate in tsv_candidates:
            if candidate.exists():
                chunk_tsv = candidate
                break

        if not chunk_tsv:
            if verbose:
                print(f"    Warning: No TSV found for chunk {chunk_idx}")
            continue

        if verbose:
            print(f"    Processing chunk {chunk_idx}: offset={chunk_start_offset:.1f}s")

        # Load and adjust timestamps
        chunk_rows = load_diarization_tsv(chunk_tsv)
        chunk_speakers = set()

        for row in chunk_rows:
            # Adjust timestamps with chunk offset
            start_sec = float(row.get("start") or row.get("start_sec", 0))
            end_sec = float(row.get("end") or row.get("end_sec", 0))

            adjusted_start = start_sec + chunk_start_offset
            adjusted_end = end_sec + chunk_start_offset

            # Optionally map speaker labels across chunks
            # For now, we'll keep original labels but prefix with chunk index to avoid confusion
            speaker = row.get("speaker") or row.get("speaker_name", "SPEAKER_00")

            if map_speakers:
                # TODO: Implement speaker mapping using embedding similarity
                # For now, just prefix with chunk index
                speaker = f"C{chunk_idx}_{speaker}"
            else:
                # Keep original speaker label but prefix chunk index for uniqueness
                speaker = f"C{chunk_idx}_{speaker}"

            chunk_speakers.add(speaker)
            all_speakers.add(speaker)

            segment = {
                "ytid": ytid,
                "speaker_name": speaker,
                "start_sec": adjusted_start,
                "end_sec": adjusted_end,
                "duration_sec": adjusted_end - adjusted_start,
                "source_audio": chunks_metadata["source_audio"],
                "chunk_index": chunk_idx,
                "chunk_start_offset": chunk_start_offset,
            }

            all_segments.append(segment)
            total_speech_duration += segment["duration_sec"]
            total_segments += 1

        chunk_speaker_counts.append({
            "chunk_index": chunk_idx,
            "speakers": len(chunk_speakers),
            "segments": len(chunk_rows),
        })

        if verbose:
            print(f"      Speakers: {len(chunk_speakers)}, Segments: {len(chunk_rows)}")

    # Sort segments by start time
    all_segments.sort(key=lambda x: x["start_sec"])

    # Write combined TSV
    combined_tsv = output_dir / "diarized_timestamps.tsv"
    with combined_tsv.open("w", encoding="utf-8") as f:
        f.write("ytid\tspeaker_name\tstart_sec\tend_sec\tduration_sec\tsource_audio\n")
        for seg in all_segments:
            f.write(
                f"{seg['ytid']}\t{seg['speaker_name']}\t"
                f"{seg['start_sec']:.3f}\t{seg['end_sec']:.3f}\t"
                f"{seg['duration_sec']:.3f}\t{seg['source_audio']}\n"
            )

    if verbose:
        print(f"    Combined TSV written: {combined_tsv}")
        print(f"    Total speakers: {len(all_speakers)}")
        print(f"    Total segments: {total_segments}")
        print(f"    Total speech: {total_speech_duration:.1f}s ({total_speech_duration/3600:.2f}h)")

    # Create combined metadata
    combined_metadata = {
        "ytid": ytid,
        "source_audio": chunks_metadata["source_audio"],
        "chunked_processing": True,
        "chunks": {
            "num_chunks": num_chunks,
            "chunk_duration": chunks_metadata["chunk_duration"],
            "overlap": chunks_metadata["overlap"],
            "chunk_results": chunk_speaker_counts,
        },
        "results": {
            "num_speakers": len(all_speakers),
            "speaker_labels": sorted(all_speakers),
            "num_segments": total_segments,
            "total_speech_duration": total_speech_duration,
        },
        "note": "Speaker labels prefixed with chunk index (C0_, C1_, etc.) for uniqueness. True speaker mapping not yet implemented.",
    }

    metadata_path = output_dir / "diarization.json"
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(combined_metadata, f, indent=2)

    if verbose:
        print(f"    Metadata written: {metadata_path}")

    return combined_metadata


def main():
    parser = argparse.ArgumentParser(
        description="Combine diarization results from audio chunks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Combine chunks for a video
  %(prog)s chunks/ chunk_results/ output/ video123

  # With speaker mapping (experimental)
  %(prog)s chunks/ chunk_results/ output/ video123 --map-speakers
        """
    )

    parser.add_argument(
        "chunks_dir",
        type=Path,
        help="Directory containing chunks_metadata.json"
    )
    parser.add_argument(
        "chunk_results_dir",
        type=Path,
        help="Directory containing chunk diarization results"
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Output directory for combined results"
    )
    parser.add_argument(
        "ytid",
        help="Video ID"
    )
    parser.add_argument(
        "--map-speakers",
        action="store_true",
        help="Attempt to map speaker labels across chunks (experimental)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress output"
    )

    args = parser.parse_args()

    if not args.chunks_dir.exists():
        print(f"Error: Chunks directory not found: {args.chunks_dir}", file=sys.stderr)
        sys.exit(1)

    if not args.chunk_results_dir.exists():
        print(f"Error: Chunk results directory not found: {args.chunk_results_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        chunks_metadata = load_chunk_metadata(args.chunks_dir)

        combined_metadata = combine_chunk_results(
            chunks_metadata=chunks_metadata,
            chunk_results_dir=args.chunk_results_dir,
            output_dir=args.output_dir,
            ytid=args.ytid,
            map_speakers=args.map_speakers,
            verbose=not args.quiet
        )

        print(f"Combined output: {args.output_dir}")
        sys.exit(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
