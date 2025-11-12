#!/usr/bin/env python3
"""
Filter clips to keep only those where Hasan is speaking.
Parallelized across multiple CPU cores with GPU inference.
"""

import sys
import os
from pathlib import Path
import numpy as np
import json
import warnings
import torch
from multiprocessing import Pool, Manager
from functools import partial

# Suppress noisy warnings
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning)

def check_dependencies():
    """Check if required packages are installed"""
    try:
        import torch
        import torchaudio
        print(f"[OK] torch: {torch.__version__}")
        print(f"[OK] torchaudio: {torchaudio.__version__}")
    except ImportError as e:
        print(f"[ERROR] Missing dependency: {e}")
        print("\nInstall with:")
        print("  pip install torch torchaudio")
        sys.exit(1)

    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
        print(f"[OK] resemblyzer installed")
        return True
    except ImportError:
        print("[WARN] resemblyzer not installed")
        print("  pip install resemblyzer")
        sys.exit(1)

def preprocess_audio_worker(clip_path):
    """Worker function to preprocess audio on CPU"""
    from resemblyzer import preprocess_wav

    try:
        wav = preprocess_wav(str(clip_path))
        return (str(clip_path), wav, None)
    except Exception as e:
        return (str(clip_path), None, str(e))

def analyze_with_resemblyzer_parallel(clips_dir, reference_clips, output_dir, threshold=0.7, num_workers=23, batch_size=8):
    """Use Resemblyzer with parallel CPU preprocessing and batched GPU inference"""
    from resemblyzer import VoiceEncoder, preprocess_wav
    from pathlib import Path

    # Use GPU if available
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n[SETUP] Using device: {device}")
    if device == "cuda":
        print(f"[SETUP] GPU: {torch.cuda.get_device_name(0)}")
    print(f"[SETUP] CPU workers: {num_workers}, GPU batch size: {batch_size}")

    print("[SETUP] Loading voice encoder...")
    encoder = VoiceEncoder(device=device)

    # Build reference embedding from known Hasan clips
    print(f"\n[REFERENCE] Processing {len(reference_clips)} reference clips...")
    ref_embeddings = []

    for ref_clip in reference_clips:
        try:
            wav = preprocess_wav(ref_clip)
            embedding = encoder.embed_utterance(wav)
            ref_embeddings.append(embedding)
            print(f"  ✓ {Path(ref_clip).name}")
        except Exception as e:
            print(f"  ✗ {Path(ref_clip).name}: {e}")

    if not ref_embeddings:
        print("[ERROR] No valid reference clips!")
        return

    # Average reference embeddings
    ref_embedding = np.mean(ref_embeddings, axis=0)
    print(f"[REFERENCE] Created voice profile from {len(ref_embeddings)} clips")

    # Analyze all clips
    clips = sorted(Path(clips_dir).glob("*.mp4"))
    total_clips = len(clips)
    print(f"\n[ANALYZE] Preprocessing {total_clips} clips on {num_workers} CPU cores...")

    Path(output_dir).mkdir(exist_ok=True)
    progress_file = Path(output_dir) / "progress.txt"

    # Parallel audio preprocessing
    with Pool(processes=num_workers) as pool:
        # Use imap_unordered for progress tracking
        preprocessed_results = []
        for i, result in enumerate(pool.imap_unordered(preprocess_audio_worker, clips, chunksize=10), 1):
            preprocessed_results.append(result)
            if i % 100 == 0 or i == total_clips:
                print(f"  Preprocessed: {i}/{total_clips} ({i/total_clips*100:.1f}%)")
                with open(progress_file, 'w') as f:
                    f.write(f"Preprocessing: {i}/{total_clips} ({i/total_clips*100:.1f}%)\n")

    print(f"[ANALYZE] Preprocessing complete! Running GPU inference...")

    # Separate valid wavs from errors
    valid_items = [(path, wav) for path, wav, err in preprocessed_results if err is None]
    error_items = [(path, err) for path, wav, err in preprocessed_results if err is not None]

    print(f"[ANALYZE] Valid clips: {len(valid_items)}, Errors: {len(error_items)}")

    # GPU inference in batches
    results = []

    for i in range(0, len(valid_items), batch_size):
        batch = valid_items[i:i + batch_size]
        batch_paths = [path for path, wav in batch]
        batch_wavs = [wav for path, wav in batch]

        try:
            # Batch embed
            embeddings = np.array([encoder.embed_utterance(wav) for wav in batch_wavs])

            # Calculate similarities
            similarities = np.dot(embeddings, ref_embedding)

            # Store results
            for path, similarity in zip(batch_paths, similarities):
                is_hasan = bool(similarity >= threshold)
                results.append({
                    'file': path,
                    'similarity': float(similarity),
                    'is_hasan': is_hasan
                })

        except Exception as e:
            print(f"  ✗ Batch GPU inference error: {e}")
            for path in batch_paths:
                results.append({
                    'file': path,
                    'similarity': 0.0,
                    'is_hasan': False,
                    'error': f"GPU inference error: {e}"
                })

        processed = len(results)
        if processed % 500 == 0 or processed == len(valid_items):
            print(f"  GPU inference: {processed}/{len(valid_items)} ({processed/len(valid_items)*100:.1f}%)")

        # Update progress file
        with open(progress_file, 'w') as f:
            f.write(f"GPU inference: {processed}/{total_clips} ({processed/total_clips*100:.1f}%)\n")

    # Add errors
    for path, error in error_items:
        results.append({
            'file': path,
            'similarity': 0.0,
            'is_hasan': False,
            'error': error
        })

    # Save results
    results_file = Path(output_dir) / "voice_analysis.json"
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)

    # Stats
    hasan_clips = [r for r in results if r['is_hasan']]
    print(f"\n[RESULTS]")
    print(f"  Total clips: {len(results)}")
    print(f"  Hasan speaking: {len(hasan_clips)} ({len(hasan_clips)/len(results)*100:.1f}%)")
    print(f"  Other speakers: {len(results) - len(hasan_clips)}")
    print(f"  Errors: {len(error_items)}")
    print(f"  Results saved to: {results_file}")

    # Create filtered list
    hasan_list = Path(output_dir) / "hasan_clips.txt"
    with open(hasan_list, 'w') as f:
        for r in hasan_clips:
            f.write(f"{r['file']}\n")
    print(f"  Hasan clips list: {hasan_list}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Filter clips for Hasan\'s voice (parallel)')
    parser.add_argument('clips_dir', help='Directory containing clips')
    parser.add_argument('--reference', nargs='+', required=True,
                       help='Reference clips known to be Hasan speaking')
    parser.add_argument('--output', default='voice_filtered',
                       help='Output directory for results')
    parser.add_argument('--threshold', type=float, default=0.7,
                       help='Similarity threshold (0-1, default 0.7)')
    parser.add_argument('--workers', type=int, default=23,
                       help='Number of CPU workers for audio preprocessing (default 23)')
    parser.add_argument('--batch-size', type=int, default=8,
                       help='GPU batch size (default 8, reduce if OOM)')

    args = parser.parse_args()

    print("=" * 80)
    print("HASAN VOICE FILTER (PARALLEL)")
    print("=" * 80)

    check_dependencies()

    analyze_with_resemblyzer_parallel(
        args.clips_dir,
        args.reference,
        args.output,
        args.threshold,
        args.workers,
        args.batch_size
    )

if __name__ == "__main__":
    main()
