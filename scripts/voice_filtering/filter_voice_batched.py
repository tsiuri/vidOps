#!/usr/bin/env python3
"""
Filter clips to keep only those where Hasan is speaking.
Uses speaker verification with GPU batching for better performance.
"""

import sys
import os
from pathlib import Path
import numpy as np
import json
import warnings
import torch
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    """Worker function to preprocess audio on CPU in parallel"""
    from resemblyzer import preprocess_wav
    try:
        wav = preprocess_wav(str(clip_path))
        return (clip_path, wav, None)
    except Exception as e:
        return (clip_path, None, str(e))

def analyze_with_resemblyzer_batched(clips_dir, reference_clips, output_dir, threshold=0.7, batch_size=32, num_workers=4):
    """Use Resemblyzer with batched GPU inference and parallel CPU preprocessing"""
    from resemblyzer import VoiceEncoder, preprocess_wav
    from pathlib import Path

    # Use GPU if available
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n[SETUP] Using device: {device}")
    if device == "cuda":
        print(f"[SETUP] GPU: {torch.cuda.get_device_name(0)}")
    print(f"[SETUP] Batch size: {batch_size}, CPU workers: {num_workers}")

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
    print(f"\n[ANALYZE] Processing {len(clips)} clips with batched inference...")

    results = []
    Path(output_dir).mkdir(exist_ok=True)
    progress_file = Path(output_dir) / "progress.txt"

    # Process in batches with parallel audio preprocessing
    total_clips = len(clips)
    processed = 0

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        # Submit batches of preprocessing jobs
        for batch_start in range(0, total_clips, batch_size):
            batch_clips = clips[batch_start:batch_start + batch_size]

            # Preprocess audio on CPU in parallel
            futures = {executor.submit(preprocess_audio_worker, clip): clip for clip in batch_clips}

            batch_wavs = []
            batch_clips_valid = []
            batch_errors = []

            for future in as_completed(futures):
                clip_path, wav, error = future.result()
                if error:
                    batch_errors.append((clip_path, error))
                else:
                    batch_wavs.append(wav)
                    batch_clips_valid.append(clip_path)

            # Batch GPU inference
            if batch_wavs:
                try:
                    # Embed all clips in batch
                    embeddings = np.array([encoder.embed_utterance(wav) for wav in batch_wavs])

                    # Calculate similarities for batch
                    similarities = np.dot(embeddings, ref_embedding)

                    # Store results
                    for clip, similarity in zip(batch_clips_valid, similarities):
                        is_hasan = similarity >= threshold
                        results.append({
                            'file': str(clip),
                            'similarity': float(similarity),
                            'is_hasan': is_hasan
                        })
                except Exception as e:
                    print(f"  ✗ Batch error: {e}")
                    for clip in batch_clips_valid:
                        results.append({
                            'file': str(clip),
                            'similarity': 0.0,
                            'is_hasan': False,
                            'error': str(e)
                        })

            # Store errors
            for clip, error in batch_errors:
                results.append({
                    'file': str(clip),
                    'similarity': 0.0,
                    'is_hasan': False,
                    'error': error
                })

            processed += len(batch_clips)

            # Update progress
            with open(progress_file, 'w') as f:
                f.write(f"Processing: {processed}/{total_clips} ({processed/total_clips*100:.1f}%)\n")

            if processed % 100 == 0 or processed == total_clips:
                print(f"  [{processed}/{total_clips}] ({processed/total_clips*100:.1f}%)")

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
    print(f"  Results saved to: {results_file}")

    # Create filtered list
    hasan_list = Path(output_dir) / "hasan_clips.txt"
    with open(hasan_list, 'w') as f:
        for r in hasan_clips:
            f.write(f"{r['file']}\n")
    print(f"  Hasan clips list: {hasan_list}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Filter clips for Hasan\'s voice (batched)')
    parser.add_argument('clips_dir', help='Directory containing clips')
    parser.add_argument('--reference', nargs='+', required=True,
                       help='Reference clips known to be Hasan speaking')
    parser.add_argument('--output', default='voice_filtered',
                       help='Output directory for results')
    parser.add_argument('--threshold', type=float, default=0.7,
                       help='Similarity threshold (0-1, default 0.7)')
    parser.add_argument('--batch-size', type=int, default=32,
                       help='Number of clips to process per GPU batch (default 32)')
    parser.add_argument('--workers', type=int, default=4,
                       help='Number of CPU workers for audio preprocessing (default 4)')

    args = parser.parse_args()

    print("=" * 80)
    print("HASAN VOICE FILTER (BATCHED)")
    print("=" * 80)

    check_dependencies()

    analyze_with_resemblyzer_batched(
        args.clips_dir,
        args.reference,
        args.output,
        args.threshold,
        args.batch_size,
        args.workers
    )

if __name__ == "__main__":
    main()
