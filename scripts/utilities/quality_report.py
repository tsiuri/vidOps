#!/usr/bin/env python3
"""
Quality Metrics Dashboard for Transcription Batch Analysis

Analyzes all .words.tsv files to provide quality insights:
- Confidence score distributions
- Retry statistics
- Per-file quality metrics
- Identify problematic files for manual review

Usage:
    python3 quality_report.py [directory]

    directory: Path to search for .words.tsv files (default: generated/)
"""

import sys
import glob
from pathlib import Path
from collections import defaultdict
import json

def load_words_tsv(filepath):
    """Load a words.tsv file and return list of word dicts"""
    words = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            header = f.readline().strip().split('\t')
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) >= len(header):
                    word_dict = dict(zip(header, parts))
                    # Convert numeric fields
                    try:
                        word_dict['start'] = float(word_dict.get('start', 0))
                        word_dict['end'] = float(word_dict.get('end', 0))
                        word_dict['confidence'] = float(word_dict.get('confidence', 0))
                        word_dict['retried'] = int(word_dict.get('retried', 0))
                        word_dict['seg'] = int(word_dict.get('seg', 0))
                    except (ValueError, KeyError):
                        pass
                    words.append(word_dict)
    except Exception as e:
        print(f"Warning: Failed to load {filepath}: {e}", file=sys.stderr)
    return words

def analyze_file(filepath):
    """Analyze a single .words.tsv file"""
    words = load_words_tsv(filepath)
    if not words:
        return None

    confidences = [w['confidence'] for w in words if 'confidence' in w]
    retries = sum(w.get('retried', 0) for w in words)

    # Count unique segments
    unique_segs = len(set(w.get('seg', 0) for w in words))
    retried_segs = len(set(w['seg'] for w in words if w.get('retried', 0) == 1))

    return {
        'file': filepath.name.replace('.words.tsv', ''),
        'total_words': len(words),
        'total_segments': unique_segs,
        'retried_segments': retried_segs,
        'avg_confidence': sum(confidences) / len(confidences) if confidences else 0,
        'min_confidence': min(confidences) if confidences else 0,
        'max_confidence': max(confidences) if confidences else 0,
        'low_conf_words': sum(1 for c in confidences if c < -1.0),
        'very_low_conf_words': sum(1 for c in confidences if c < -1.5),
    }

def print_histogram(values, bins=10, width=50):
    """Print ASCII histogram of confidence values"""
    if not values:
        return

    min_val, max_val = min(values), max(values)
    if min_val == max_val:
        print(f"  All values: {min_val:.2f}")
        return

    bin_size = (max_val - min_val) / bins
    counts = defaultdict(int)

    for v in values:
        bin_idx = min(int((v - min_val) / bin_size), bins - 1)
        counts[bin_idx] += 1

    max_count = max(counts.values()) if counts else 1

    for i in range(bins):
        bin_start = min_val + i * bin_size
        bin_end = bin_start + bin_size
        count = counts.get(i, 0)
        bar_len = int((count / max_count) * width)
        bar = '█' * bar_len
        print(f"  {bin_start:6.2f} to {bin_end:6.2f}: {bar} {count}")

def main():
    search_dir = sys.argv[1] if len(sys.argv) > 1 else "generated"
    pattern = f"{search_dir}/**/*.words.tsv"

    files = list(Path(search_dir).glob("**/*.words.tsv"))
    if not files:
        print(f"No .words.tsv files found in {search_dir}/")
        return 1

    print(f"{'='*70}")
    print(f"  TRANSCRIPTION QUALITY REPORT")
    print(f"{'='*70}")
    print(f"Analyzing {len(files)} files from: {search_dir}/\n")

    # Analyze all files
    analyses = []
    all_confidences = []

    for filepath in files:
        result = analyze_file(filepath)
        if result:
            analyses.append(result)
            # Collect all confidence scores for global histogram
            words = load_words_tsv(filepath)
            all_confidences.extend([w['confidence'] for w in words if 'confidence' in w])

    if not analyses:
        print("No valid data found.")
        return 1

    # Global statistics
    total_words = sum(a['total_words'] for a in analyses)
    total_segments = sum(a['total_segments'] for a in analyses)
    total_retried = sum(a['retried_segments'] for a in analyses)
    avg_conf = sum(all_confidences) / len(all_confidences) if all_confidences else 0
    low_conf = sum(1 for c in all_confidences if c < -1.0)
    very_low_conf = sum(1 for c in all_confidences if c < -1.5)

    print(f"{'─'*70}")
    print(f"  OVERALL STATISTICS")
    print(f"{'─'*70}")
    print(f"  Total files:               {len(analyses)}")
    print(f"  Total words:               {total_words:,}")
    print(f"  Total segments:            {total_segments:,}")
    print(f"  Retried segments:          {total_retried:,} ({100*total_retried/total_segments if total_segments > 0 else 0:.1f}%)")
    print(f"  Average confidence:        {avg_conf:.3f}")
    print(f"  Low confidence (<-1.0):    {low_conf:,} words ({100*low_conf/total_words if total_words > 0 else 0:.1f}%)")
    print(f"  Very low conf (<-1.5):     {very_low_conf:,} words ({100*very_low_conf/total_words if total_words > 0 else 0:.1f}%)")

    print(f"\n{'─'*70}")
    print(f"  CONFIDENCE DISTRIBUTION (all words)")
    print(f"{'─'*70}")
    print_histogram(all_confidences, bins=15)

    # Find problematic files
    problematic = sorted(analyses, key=lambda x: x['avg_confidence'])[:10]

    print(f"\n{'─'*70}")
    print(f"  TOP 10 LOWEST QUALITY FILES (manual review recommended)")
    print(f"{'─'*70}")
    print(f"  {'File':<50} {'Avg Conf':>10} {'Retried':>8}")
    print(f"  {'-'*50} {'-'*10} {'-'*8}")
    for item in problematic:
        fname = item['file'][:48]
        print(f"  {fname:<50} {item['avg_confidence']:>10.3f} {item['retried_segments']:>8}")

    # Best quality files
    best = sorted(analyses, key=lambda x: x['avg_confidence'], reverse=True)[:5]

    print(f"\n{'─'*70}")
    print(f"  TOP 5 HIGHEST QUALITY FILES")
    print(f"{'─'*70}")
    print(f"  {'File':<50} {'Avg Conf':>10} {'Retried':>8}")
    print(f"  {'-'*50} {'-'*10} {'-'*8}")
    for item in best:
        fname = item['file'][:48]
        print(f"  {fname:<50} {item['avg_confidence']:>10.3f} {item['retried_segments']:>8}")

    # Retry statistics
    print(f"\n{'─'*70}")
    print(f"  RETRY STATISTICS BY FILE")
    print(f"{'─'*70}")
    retry_counts = defaultdict(int)
    for a in analyses:
        retry_pct = int((a['retried_segments'] / a['total_segments'] * 100) if a['total_segments'] > 0 else 0)
        retry_counts[retry_pct // 10 * 10] += 1  # Bucket by 10%

    for pct in sorted(retry_counts.keys()):
        count = retry_counts[pct]
        bar = '█' * (count * 50 // len(analyses))
        print(f"  {pct:3d}-{pct+9:3d}% retried: {bar} {count} files")

    print(f"\n{'='*70}")
    print(f"  Report complete. Review low-quality files above for potential issues.")
    print(f"{'='*70}\n")

    # Export detailed data as JSON for further analysis
    report_file = Path(search_dir) / "quality_report.json"
    with open(report_file, 'w') as f:
        json.dump({
            'summary': {
                'total_files': len(analyses),
                'total_words': total_words,
                'total_segments': total_segments,
                'retried_segments': total_retried,
                'avg_confidence': avg_conf,
                'low_confidence_count': low_conf,
            },
            'files': analyses,
        }, f, indent=2)
    print(f"Detailed report saved to: {report_file}")

    return 0

if __name__ == '__main__':
    sys.exit(main())
