#!/usr/bin/env python3
"""
Filter clips to keep only those where Hasan is speaking.
Uses speaker verification to compare against reference Hasan clips.
"""

import sys
import os
from pathlib import Path
import numpy as np
from collections import defaultdict
import json
import warnings

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
        print("[WARN] resemblyzer not installed - will use pyannote instead")
        return False

def analyze_with_resemblyzer(clips_dir, reference_clips, output_dir, threshold=0.7):
    """Use Resemblyzer for voice verification (simpler, faster)"""
    from resemblyzer import VoiceEncoder, preprocess_wav
    from pathlib import Path
    import torch

    # Use GPU if available
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n[SETUP] Using device: {device}")
    if device == "cuda":
        print(f"[SETUP] GPU: {torch.cuda.get_device_name(0)}")

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
    print(f"\n[ANALYZE] Processing {len(clips)} clips...")

    results = []
    Path(output_dir).mkdir(exist_ok=True)

    # Progress file
    progress_file = Path(output_dir) / "progress.txt"

    for i, clip in enumerate(clips, 1):
        # Write progress to file (unbuffered)
        with open(progress_file, 'w') as f:
            f.write(f"Processing: {i}/{len(clips)} ({i/len(clips)*100:.1f}%)\n")
            f.write(f"Current: {clip.name}\n")

        if i % 100 == 0:
            print(f"  [{i}/{len(clips)}] {clip.name}")
        
        try:
            wav = preprocess_wav(str(clip))
            clip_embedding = encoder.embed_utterance(wav)
            
            # Cosine similarity
            similarity = np.dot(ref_embedding, clip_embedding)
            
            is_hasan = similarity >= threshold
            results.append({
                'file': str(clip),
                'similarity': float(similarity),
                'is_hasan': is_hasan
            })
            
        except Exception as e:
            print(f"  ✗ {clip.name}: {e}")
            results.append({
                'file': str(clip),
                'similarity': 0.0,
                'is_hasan': False,
                'error': str(e)
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
    print(f"  Results saved to: {results_file}")
    
    # Create filtered list
    hasan_list = Path(output_dir) / "hasan_clips.txt"
    with open(hasan_list, 'w') as f:
        for r in hasan_clips:
            f.write(f"{r['file']}\n")
    print(f"  Hasan clips list: {hasan_list}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Filter clips for Hasan\'s voice')
    parser.add_argument('clips_dir', help='Directory containing clips')
    parser.add_argument('--reference', nargs='+', required=True, 
                       help='Reference clips known to be Hasan speaking')
    parser.add_argument('--output', default='voice_filtered',
                       help='Output directory for results')
    parser.add_argument('--threshold', type=float, default=0.7,
                       help='Similarity threshold (0-1, default 0.7)')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("HASAN VOICE FILTER")
    print("=" * 80)
    
    has_resemblyzer = check_dependencies()
    
    if has_resemblyzer:
        analyze_with_resemblyzer(
            args.clips_dir, 
            args.reference,
            args.output,
            args.threshold
        )
    else:
        print("\n[ERROR] Please install resemblyzer:")
        print("  pip install resemblyzer")

if __name__ == "__main__":
    main()
