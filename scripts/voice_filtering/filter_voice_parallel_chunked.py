#!/usr/bin/env python3
"""
Filter clips to keep only those where Hasan is speaking.
Parallelized with chunked processing to avoid memory issues.
"""

import sys
import os
from pathlib import Path
import numpy as np
import json
import warnings
import torch
from multiprocessing import Pool

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
        sys.exit(1)

    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
        print(f"[OK] resemblyzer installed")
    except ImportError:
        print("[ERROR] resemblyzer not installed")
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

def analyze_with_resemblyzer_chunked(clips_dir, reference_clips, output_dir, threshold=0.7, num_workers=23, chunk_size=200):
    """Process in chunks to avoid memory issues"""
    from resemblyzer import VoiceEncoder, preprocess_wav
    from pathlib import Path

    # Use GPU if available
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n[SETUP] Using device: {device}")
    if device == "cuda":
        print(f"[SETUP] GPU: {torch.cuda.get_device_name(0)}")
    print(f"[SETUP] CPU workers: {num_workers}, Chunk size: {chunk_size}")

    print("[SETUP] Loading voice encoder...")
    encoder = VoiceEncoder(device=device)

    # Build reference embedding
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

    ref_embedding = np.mean(ref_embeddings, axis=0)
    print(f"[REFERENCE] Created voice profile from {len(ref_embeddings)} clips")

    # Get all clips (support both mp4 and wav)
    clips = sorted(list(Path(clips_dir).glob("*.mp4")) + list(Path(clips_dir).glob("*.wav")))
    total_clips = len(clips)
    print(f"\n[ANALYZE] Processing {total_clips} clips in chunks of {chunk_size}...")

    # Ensure output directory (mkdir -p behavior)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    progress_file = Path(output_dir) / "progress.txt"

    all_results = []
    processed_count = 0

    # Process in chunks
    for chunk_start in range(0, total_clips, chunk_size):
        chunk_clips = clips[chunk_start:chunk_start + chunk_size]
        chunk_num = chunk_start // chunk_size + 1
        total_chunks = (total_clips + chunk_size - 1) // chunk_size

        print(f"\n[CHUNK {chunk_num}/{total_chunks}] Processing {len(chunk_clips)} clips...")

        # Parallel preprocessing for this chunk
        with Pool(processes=num_workers) as pool:
            chunk_results = list(pool.imap_unordered(preprocess_audio_worker, chunk_clips, chunksize=10))

        # Separate valid from errors
        valid_items = [(path, wav) for path, wav, err in chunk_results if err is None]
        error_items = [(path, err) for path, wav, err in chunk_results if err is not None]

        print(f"  Preprocessed: {len(valid_items)} valid, {len(error_items)} errors")

        # GPU inference on valid clips
        for i, (path, wav) in enumerate(valid_items):
            try:
                embedding = encoder.embed_utterance(wav)
                similarity = float(np.dot(ref_embedding, embedding))
                is_hasan = bool(similarity >= threshold)

                all_results.append({
                    'file': path,
                    'similarity': similarity,
                    'is_hasan': is_hasan
                })
            except Exception as e:
                all_results.append({
                    'file': path,
                    'similarity': 0.0,
                    'is_hasan': False,
                    'error': f"GPU error: {e}"
                })

            if (i + 1) % 50 == 0:
                print(f"    GPU: {i+1}/{len(valid_items)}")

        # Add errors
        for path, error in error_items:
            all_results.append({
                'file': path,
                'similarity': 0.0,
                'is_hasan': False,
                'error': error
            })

        processed_count += len(chunk_clips)
        print(f"  Progress: {processed_count}/{total_clips} ({processed_count/total_clips*100:.1f}%)")

        # Update progress file
        with open(progress_file, 'w') as f:
            f.write(f"Processing: {processed_count}/{total_clips} ({processed_count/total_clips*100:.1f}%)\n")

        # Clear GPU cache
        if device == "cuda":
            torch.cuda.empty_cache()

    # Save results
    results_file = Path(output_dir) / "voice_analysis.json"
    with open(results_file, 'w') as f:
        json.dump(all_results, f, indent=2)

    # Stats
    hasan_clips = [r for r in all_results if r.get('is_hasan', False)]
    print(f"\n[RESULTS]")
    print(f"  Total clips: {len(all_results)}")
    if len(all_results) > 0:
        print(f"  Hasan speaking: {len(hasan_clips)} ({len(hasan_clips)/len(all_results)*100:.1f}%)")
        print(f"  Other speakers: {len(all_results) - len(hasan_clips)}")
    else:
        print(f"  No clips were processed!")
    print(f"  Results saved to: {results_file}")

    # Create filtered list
    hasan_list = Path(output_dir) / "hasan_clips.txt"
    with open(hasan_list, 'w') as f:
        for r in hasan_clips:
            f.write(f"{r['file']}\n")
    print(f"  Hasan clips list: {hasan_list}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Filter clips for Hasan\'s voice (chunked)')
    parser.add_argument('clips_dir', help='Directory containing clips')
    parser.add_argument('--reference', nargs='+', required=True,
                       help='Reference clips known to be Hasan speaking')
    parser.add_argument('--output', default='voice_filtered',
                       help='Output directory for results')
    parser.add_argument('--threshold', type=float, default=0.7,
                       help='Similarity threshold (0-1, default 0.7)')
    parser.add_argument('--workers', type=int, default=23,
                       help='Number of CPU workers (default 23)')
    parser.add_argument('--chunk-size', type=int, default=200,
                       help='Clips per chunk (default 200)')

    args = parser.parse_args()

    print("=" * 80)
    print("HASAN VOICE FILTER (CHUNKED)")
    print("=" * 80)

    check_dependencies()

    analyze_with_resemblyzer_chunked(
        args.clips_dir,
        args.reference,
        args.output,
        args.threshold,
        args.workers,
        args.chunk_size
    )

if __name__ == "__main__":
    main()
