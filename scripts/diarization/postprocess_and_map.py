#!/usr/bin/env python3
"""
Post-processing and word mapping utilities for diarization.
"""
import csv
from pathlib import Path
from typing import List, Dict, Optional
from math import inf


def postprocess_diarization(
    timestamps_file: Path,
    output_file: Path,
    ytid: str,
    micro_gap: float = 0.3,
    min_turn: float = 0.45,
    source_audio: str = "enhanced.wav",
    verbose: bool = False,
) -> Dict:
    """
    Post-process diarization timestamps: merge micro-gaps and remove short segments.

    Args:
        timestamps_file: Input TSV with diarization timestamps
        output_file: Output TSV for cleaned timestamps
        ytid: Video ID
        micro_gap: Merge same-speaker turns with gaps < this (seconds)
        min_turn: Remove turns shorter than this (seconds)
        source_audio: Audio filename to record in output
        verbose: Print statistics

    Returns:
        Statistics dict
    """
    # Load turns
    turns = []
    with timestamps_file.open() as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            turns.append({
                'spk': row.get('speaker', ''),
                'start': float(row.get('start', 0)),
                'end': float(row.get('end', 0)),
            })
    turns.sort(key=lambda t: (t['start'], t['end']))

    original_count = len(turns)

    # Merge micro-gaps
    merged = []
    for t in turns:
        if merged and t['spk'] == merged[-1]['spk'] and (t['start'] - merged[-1]['end']) < micro_gap:
            merged[-1]['end'] = max(merged[-1]['end'], t['end'])
        else:
            merged.append(t)

    # Remove short segments
    cleaned = []
    for t in merged:
        if (t['end'] - t['start']) >= min_turn:
            cleaned.append(t)

    # Write cleaned timestamps
    with output_file.open('w', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(['ytid', 'speaker', 'start', 'end', 'duration', 'source_audio'])
        for t in cleaned:
            writer.writerow([
                ytid,
                t['spk'],
                f"{t['start']:.3f}",
                f"{t['end']:.3f}",
                f"{t['end']-t['start']:.3f}",
                source_audio
            ])

    stats = {
        "original": original_count,
        "after_merge": len(merged),
        "after_cleanup": len(cleaned),
        "micro_gap": micro_gap,
        "min_turn": min_turn,
    }

    if verbose:
        print(f"  Post-processing: {original_count} → {len(merged)} (merged) → {len(cleaned)} (cleaned)")

    return stats


def map_words_to_speakers(
    words_file: Path,
    timestamps_file: Path,
    output_file: Path,
    gap_tolerance: float = 0.15,
    verbose: bool = False,
) -> Dict:
    """
    Map transcript words to speakers based on temporal overlap.

    Args:
        words_file: Input words.tsv from transcript
        timestamps_file: Cleaned diarization timestamps (post-processed)
        output_file: Output speaker_words.tsv
        gap_tolerance: Tolerance for matching at segment boundaries (seconds)
        verbose: Print statistics

    Returns:
        Statistics dict
    """
    import pandas as pd

    # Load cleaned turns
    turns = []
    with timestamps_file.open() as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            turns.append({
                'spk': row['speaker'],
                'start': float(row['start']),
                'end': float(row['end']),
            })
    turns.sort(key=lambda t: (t['start'], t['end']))

    # Load words
    words_df = pd.read_csv(words_file, sep='\t')

    # Assign speakers
    assignments = []
    for _, w in words_df.iterrows():
        best_spk = 'UNKNOWN'
        best_ov = -inf
        for t in turns:
            # Skip turns that end before this word (with tolerance)
            if t['end'] + gap_tolerance < w.start:
                continue
            # Break when we reach turns that start after this word
            if t['start'] - gap_tolerance > w.end:
                break
            # Calculate overlap
            ov = min(w.end, t['end']) - max(w.start, t['start'])
            if ov > best_ov:
                best_ov = ov
                best_spk = t['spk']
        assignments.append(best_spk)

    words_df['speaker'] = assignments

    # Write speaker_words
    words_df.to_csv(output_file, sep='\t', index=False)

    unknown_count = sum(1 for s in assignments if s == 'UNKNOWN')
    stats = {
        "total_words": len(assignments),
        "assigned": len(assignments) - unknown_count,
        "unknown": unknown_count,
        "unknown_pct": 100 * unknown_count / len(assignments) if assignments else 0,
    }

    if verbose:
        print(f"  Word mapping: {stats['assigned']}/{stats['total_words']} assigned "
              f"({stats['unknown']} unknown, {stats['unknown_pct']:.1f}%)")

    return stats


def find_words_file(
    ytid: str,
    project_root: Path,
    generated_dir: str = "generated",
) -> Optional[Path]:
    """
    Find words TSV file for a given YTID.

    Searches for patterns:
    - {ytid}*.words.tsv
    - {ytid}*.turbo.words.tsv

    Args:
        ytid: Video ID
        project_root: Project root directory
        generated_dir: Generated files directory name

    Returns:
        Path to words file or None
    """
    gen_dir = project_root / generated_dir

    if not gen_dir.exists():
        return None

    # Try exact match first
    matches = list(gen_dir.glob(f"{ytid}*.words.tsv"))

    if matches:
        # Prefer .turbo.words.tsv if available
        turbo_matches = [m for m in matches if '.turbo.words.tsv' in m.name]
        if turbo_matches:
            return turbo_matches[0]
        return matches[0]

    return None
