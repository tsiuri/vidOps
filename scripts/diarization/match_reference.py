#!/usr/bin/env python3
"""
Match diarized speakers to reference speaker identities using embeddings.
Maps anonymous speaker labels (SPEAKER_00, SPEAKER_01) to known speaker names.
"""
import os
import sys
import json
import argparse
from pathlib import Path
import numpy as np
import torch
from typing import Optional
# PyTorch 2.6+ compatibility: patch torch.load to use weights_only=False
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs['weights_only'] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

from pyannote.audio import Inference
from scipy.spatial.distance import cdist


def load_diarization(tsv_path: Path) -> dict:
    """Load diarized_timestamps.tsv into a dict structure."""
    speakers_data = {}

    with tsv_path.open(encoding="utf-8") as f:
        header = f.readline()  # Skip header

        for line in f:
            if not line.strip():
                continue

            parts = line.strip().split("\t")
            if len(parts) < 6:
                continue

            ytid, speaker, start, end, duration, source_audio = parts
            start, end, duration = float(start), float(end), float(duration)

            if speaker not in speakers_data:
                speakers_data[speaker] = []

            speakers_data[speaker].append({
                "start": start,
                "end": end,
                "duration": duration,
            })

    return speakers_data


def extract_speaker_embeddings(
    audio_path: Path,
    speakers_data: dict,
    embedding_model: Inference,
    max_segments_per_speaker: int = 10,
    verbose: bool = True,
) -> dict:
    """
    Extract averaged embeddings for each speaker from their segments.

    Args:
        audio_path: Path to audio file
        speakers_data: Dict mapping speaker labels to segment lists
        embedding_model: Pyannote Inference model for embeddings
        max_segments_per_speaker: Max segments to average (default: 10)
        verbose: Print progress

    Returns:
        Dict mapping speaker labels to averaged embedding vectors
    """
    speaker_embeddings = {}

    for speaker, segments in speakers_data.items():
        if verbose:
            print(f"   Extracting embeddings for {speaker} ({len(segments)} segments)...")

        file_spec = {"audio": str(audio_path)}
        # Sample segments if too many
        sampled_segments = segments
        if len(segments) > max_segments_per_speaker:
            # Sample evenly across timeline
            indices = np.linspace(0, len(segments) - 1, max_segments_per_speaker, dtype=int)
            sampled_segments = [segments[i] for i in indices]

        # Extract embeddings for each segment
        embeddings = []
        for seg in sampled_segments:
            try:
                from pyannote.core import Segment
                segment = Segment(seg["start"], seg["end"])

                # Use crop to get segment-specific embedding; fallback to __call__ if unavailable
                if hasattr(embedding_model, "crop"):
                    emb = embedding_model.crop(file_spec, segment)
                else:
                    emb = embedding_model({"audio": str(audio_path), "segment": segment})
                if np.isnan(emb).any():
                    if verbose:
                        print(f"      Warning: NaN embedding for segment {seg['start']:.1f}-{seg['end']:.1f}; skipping")
                    continue
                embeddings.append(emb)

            except Exception as e:
                if verbose:
                    print(f"      Warning: Failed to extract embedding for segment {seg['start']:.1f}-{seg['end']:.1f}: {e}")
                continue

        if not embeddings:
            if verbose:
                print(f"      Warning: No valid embeddings for {speaker}")
            continue

        # Average embeddings
        avg_embedding = np.mean(embeddings, axis=0)
        speaker_embeddings[speaker] = avg_embedding

        if verbose:
            print(f"       Averaged {len(embeddings)} embeddings")

    return speaker_embeddings


def load_reference_embeddings(
    reference_dir: Path,
    embedding_model: Inference,
    reference_name: Optional[str] = None,
    verbose: bool = True,
) -> dict:
    """
    Load and compute embeddings for all reference clips.

    Args:
        reference_dir: Directory containing reference WAV files
        embedding_model: Pyannote Inference model
        verbose: Print progress

    Returns:
        Dict mapping reference names to averaged embedding vectors
    """
    reference_clips = list(reference_dir.glob("*.wav"))

    if not reference_clips:
        raise ValueError(f"No WAV files found in reference directory: {reference_dir}")

    if verbose:
        print(f"   Loading {len(reference_clips)} reference clips...")

    # Extract base reference name from clips (e.g., "speaker_alpha_ref001.wav" -> "speaker_alpha")
    reference_embeddings = {}

    for clip in reference_clips:
        # Use provided reference_name if set; otherwise derive from filename
        ref_name = reference_name or (clip.stem.rsplit("_ref", 1)[0] if "_ref" in clip.stem else clip.stem)

        try:
            emb = embedding_model({"audio": str(clip)})

            if ref_name not in reference_embeddings:
                reference_embeddings[ref_name] = []

            reference_embeddings[ref_name].append(emb)

        except Exception as e:
            if verbose:
                print(f"      Warning: Failed to load {clip.name}: {e}")
            continue

    # Average embeddings per reference
    reference_avg = {}
    for ref_name, embs in reference_embeddings.items():
        reference_avg[ref_name] = np.mean(embs, axis=0)

        if verbose:
            print(f"       {ref_name}: {len(embs)} clips")

    return reference_avg


def match_speakers(
    speaker_embeddings: dict,
    reference_embeddings: dict,
    threshold: float = 0.85,
    margin: float = 0.05,
    force_best: bool = True,
    collect_details: bool = False,
    verbose: bool = True,
) -> dict:
    """
    Match anonymous speakers to reference identities using cosine similarity.
    Assign at most one diarized speaker per reference by requiring a margin
    between the best and second-best candidates.

    Args:
        speaker_embeddings: Dict of {speaker_label: embedding}
        reference_embeddings: Dict of {reference_name: embedding}
        threshold: Minimum similarity for match (0.85 default, higher = stricter)
        margin: Minimum gap between best and second-best scores for a reference
                (default: 0.05)
        force_best: If True, always assign the top speaker per reference even if
                    threshold/margin are not met (best-effort mapping). Default: True
        collect_details: If True, return per-speaker match details alongside mapping
        verbose: Print progress

    Returns:
        Dict mapping matched speaker labels to reference names; unmapped
        speakers remain unchanged. If collect_details is True, also returns
        a dict of per-speaker match stats.
    """
    if not reference_embeddings:
        raise ValueError("No reference embeddings provided")

    if verbose:
        print(f"   Matching speakers (threshold: {threshold}, margin: {margin}, force_best: {force_best})...")

    mapping = {}
    details = {}
    similarity_matrix = {}

    # Pre-compute similarities for all speaker/reference pairs
    for speaker, emb in speaker_embeddings.items():
        similarity_matrix[speaker] = {}
        for ref_name, ref_emb in reference_embeddings.items():
            similarity = 1 - cdist([emb], [ref_emb], metric="cosine")[0][0]
            similarity_matrix[speaker][ref_name] = similarity

    # For each reference, find the best diarized speaker (one-to-one mapping)
    assigned_speakers = set()
    for ref_name in reference_embeddings.keys():
        candidates = [
            (speaker, scores[ref_name])
            for speaker, scores in similarity_matrix.items()
        ]

        if not candidates:
            continue

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_candidate = next(((sp, sc) for sp, sc in candidates if sp not in assigned_speakers), None)

        if not best_candidate:
            if verbose:
                print(f"      Warning: All speakers already assigned; skipping {ref_name}")
            continue

        best_speaker, best_score = best_candidate
        # Second-best score (regardless of assignment) for margin check
        remaining_candidates = [(sp, sc) for sp, sc in candidates if sp != best_speaker]
        second_best_score = remaining_candidates[0][1] if remaining_candidates else -1.0
        score_gap = best_score - second_best_score

        if best_score >= threshold and score_gap >= margin:
            mapping[best_speaker] = ref_name
            assigned_speakers.add(best_speaker)
            details[best_speaker] = {
                "reference": ref_name,
                "score": float(best_score),
                "second_best": float(second_best_score),
                "gap": float(score_gap),
                "forced": False,
            }
            if verbose:
                print(f"      {best_speaker} -> {ref_name} (similarity: {best_score:.3f}, gap: {score_gap:.3f})")
        elif force_best:
            mapping[best_speaker] = ref_name
            assigned_speakers.add(best_speaker)
            details[best_speaker] = {
                "reference": ref_name,
                "score": float(best_score),
                "second_best": float(second_best_score),
                "gap": float(score_gap),
                "forced": True,
            }
            if verbose:
                print(
                    f"      {best_speaker} -> {ref_name} (forced best; similarity: {best_score:.3f}, "
                    f"gap: {score_gap:.3f}, threshold: {threshold}, margin: {margin})"
                )
        else:
            if verbose:
                print(
                    f"      Skipped mapping for {ref_name}: "
                    f"best {best_speaker} @ {best_score:.3f}, "
                    f"second {second_best_score:.3f}, "
                    f"threshold {threshold}, margin {margin}"
                )

    if collect_details:
        return mapping, details
    return mapping


def apply_mapping(
    input_tsv: Path,
    output_tsv: Path,
    mapping: dict,
    verbose: bool = True,
) -> None:
    """
    Rewrite diarization TSV with mapped speaker labels.

    Args:
        input_tsv: Input diarized_timestamps.tsv
        output_tsv: Output TSV with matched speakers
        mapping: Dict mapping old labels to new labels
        verbose: Print progress
    """
    lines_written = 0

    with input_tsv.open(encoding="utf-8") as f_in:
        with output_tsv.open("w", encoding="utf-8") as f_out:
            # Copy header
            header = f_in.readline()
            f_out.write(header)

            # Remap speaker labels
            for line in f_in:
                if not line.strip():
                    continue

                parts = line.strip().split("\t")
                if len(parts) < 6:
                    continue

                ytid, speaker, start, end, duration, source_audio = parts

                # Apply mapping
                new_speaker = mapping.get(speaker, speaker)
                parts[1] = new_speaker

                f_out.write("\t".join(parts) + "\n")
                lines_written += 1

    if verbose:
        print(f"    Wrote {lines_written} segments to {output_tsv.name}")


def main():
    parser = argparse.ArgumentParser(
        description="Match diarized speakers to reference identities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Match speakers to reference
  %(prog)s results/diarization/ytid123 data/references/speaker_alpha

  # With custom threshold/margin
  %(prog)s results/diarization/ytid123 data/references/speaker_alpha --threshold 0.80 --margin 0.05

  # Force CPU
  %(prog)s results/diarization/ytid123 data/references/speaker_alpha --device cpu
        """
    )

    parser.add_argument(
        "diarization_dir",
        type=Path,
        help="Directory with diarization outputs (contains diarized_timestamps.tsv)"
    )
    parser.add_argument(
        "reference_dir",
        type=Path,
        help="Reference directory (contains WAV files)"
    )
    parser.add_argument(
        "--audio",
        type=Path,
        help="Audio file (auto-detected from diarization.json if not provided)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Similarity threshold for matching (default: 0.85, recommended range 0.75-0.95)"
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=0.05,
        help="Minimum gap between best and second-best match for a reference (default: 0.05)"
    )
    parser.add_argument(
        "--force-best",
        dest="force_best",
        action="store_true",
        help="Always assign the top-scoring diarized speaker to the reference even if below threshold/margin (default: enabled)"
    )
    parser.add_argument(
        "--no-force-best",
        dest="force_best",
        action="store_false",
        help="Disable forced mapping; require threshold and margin"
    )
    parser.set_defaults(force_best=True)
    parser.add_argument(
        "--device",
        default="auto",
        help="Device: auto (default), cuda, cpu"
    )
    parser.add_argument(
        "--max-segments",
        type=int,
        default=10,
        help="Max segments per speaker to average (default: 10)"
    )
    parser.add_argument(
        "--output-suffix",
        default="_matched",
        help="Suffix for output TSV (default: _matched)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress progress messages"
    )

    args = parser.parse_args()

    verbose = not args.quiet

    # Validate inputs
    if not args.diarization_dir.exists():
        print(f"L Error: Diarization directory not found: {args.diarization_dir}", file=sys.stderr)
        sys.exit(1)

    if not args.reference_dir.exists():
        print(f"L Error: Reference directory not found: {args.reference_dir}", file=sys.stderr)
        sys.exit(1)

    tsv_path = args.diarization_dir / "diarized_timestamps.tsv"
    if not tsv_path.exists():
        print(f"L Error: Diarization TSV not found: {tsv_path}", file=sys.stderr)
        sys.exit(1)

    # Auto-detect audio if not provided
    audio_path = args.audio
    if not audio_path:
        metadata_file = args.diarization_dir / "diarization.json"
        if metadata_file.exists():
            with metadata_file.open() as f:
                metadata = json.load(f)
                audio_path = Path(metadata.get("source_audio", ""))

    if not audio_path or not audio_path.exists():
        print(f"L Error: Audio file not found. Provide --audio or ensure diarization.json has source_audio", file=sys.stderr)
        sys.exit(1)

    # Auto-detect device
    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if verbose:
        print(f"= Matching speakers to reference")
        print(f"   Diarization: {args.diarization_dir}")
        print(f"   Reference: {args.reference_dir.name}")
        print(f"   Audio: {audio_path.name}")
        print(f"   Device: {device}")

    try:
        # Load embedding model
        if verbose:
            print(f"\n[*] Loading embedding model...")

        embedding_model = Inference(
            "pyannote/embedding",
            window="whole",  # Process whole segments
            device=torch.device(device)
        )

        # Load diarization data
        if verbose:
            print(f"\n[*] Loading diarization data...")

        speakers_data = load_diarization(tsv_path)

        if verbose:
            print(f"   Speakers found: {', '.join(sorted(speakers_data.keys()))}")

        # Extract speaker embeddings
        if verbose:
            print(f"\n<� Extracting speaker embeddings...")

        speaker_embeddings = extract_speaker_embeddings(
            audio_path=audio_path,
            speakers_data=speakers_data,
            embedding_model=embedding_model,
            max_segments_per_speaker=args.max_segments,
            verbose=verbose,
        )

        # Load reference embeddings
        if verbose:
            print(f"\n[*] Loading reference embeddings...")

        reference_embeddings = load_reference_embeddings(
            reference_dir=args.reference_dir,
            embedding_model=embedding_model,
            reference_name=args.reference_dir.name,
            verbose=verbose,
        )

        # Match speakers
        if verbose:
            print(f"\n<� Matching speakers...")

        mapping, mapping_details = match_speakers(
            speaker_embeddings=speaker_embeddings,
            reference_embeddings=reference_embeddings,
            threshold=args.threshold,
            margin=args.margin,
            force_best=args.force_best,
            collect_details=True,
            verbose=verbose,
        )

        # Apply mapping and save
        output_tsv = args.diarization_dir / f"diarized_timestamps{args.output_suffix}.tsv"

        if verbose:
            print(f"\n[*] Saving matched results...")

        apply_mapping(
            input_tsv=tsv_path,
            output_tsv=output_tsv,
            mapping=mapping,
            verbose=verbose,
        )

        # Warn on low-confidence forced matches
        low_conf_forced = [
            (spk, info) for spk, info in mapping_details.items()
            if info.get("forced") and info.get("score", 0.0) < args.threshold
        ]
        if low_conf_forced and verbose:
            print("\n[!] Low-confidence forced matches:")
            for spk, info in low_conf_forced:
                print(
                    f"    {spk} -> {info['reference']} "
                    f"(score {info['score']:.3f}, second {info['second_best']:.3f}, gap {info['gap']:.3f})"
                )

        # Update metadata
        metadata_file = args.diarization_dir / "diarization.json"
        if metadata_file.exists():
            with metadata_file.open() as f:
                metadata = json.load(f)

            metadata["reference"] = {
                "reference_dir": str(args.reference_dir),
                "reference_name": args.reference_dir.name,
                "threshold": args.threshold,
                "margin": args.margin,
                "force_best": args.force_best,
                "mapping": mapping,
                "mapping_details": mapping_details,
                "matched_output": str(output_tsv),
            }

            with metadata_file.open("w") as f:
                json.dump(metadata, f, indent=2)

            if verbose:
                print(f"    Updated metadata")

        if verbose:
            print(f"\n Matching complete!")
            print(f"   Output: {output_tsv}")

    except Exception as e:
        print(f"L Error during matching: {e}", file=sys.stderr)
        if not args.quiet:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
