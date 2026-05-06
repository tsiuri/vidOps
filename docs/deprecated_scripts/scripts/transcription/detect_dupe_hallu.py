#!/usr/bin/env python3
"""
detect_dupe_hallu.py

Scan words.tsv files for highly repeated phrases in a tight time window
(e.g., "He needs this." over and over), and emit a retry manifest of
"hallucinated" segments suitable for use with batch_retry_worker.py.

Usage:
    detect_dupe_hallu.py [options] MANIFEST.tsv [MANIFEST2.tsv ...]

Options:
    --project-root PATH       Project root (defaults to $PROJECT_ROOT or ".")
    --output PATH             Output manifest path. If not given, defaults to:
                                <dir_of_first_manifest>/dupe_hallu.retry_manifest.tsv
    --ngram-thresholds SPEC   Comma-separated length:count rules, e.g.
                                "1:10,2:4,3:4"
                              means:
                                1-word phrase -> repeated >=10 times
                                2-word phrase -> repeated >=4 times
                                3+ word phrase -> repeated >=4 times (default)
                              If a phrase has length L and there's no exact rule
                              for L, the rule with the largest length <= L is used.
    --max-window SECONDS      Max time window (seconds) across repeats
                              to count as hallucination (default: 20.0)
    --verbose                 Extra logging.

The output manifest columns are:

    media_file  segment_idx  start_time  end_time  confidence  zero_length  text

You can pass this manifest (in addition to your existing ones) to batch_retry_worker.py.
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Set


def log(msg: str, verbose: bool = True) -> None:
    if verbose:
        print(f"[DUPE] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[DUPE][WARN] {msg}", file=sys.stderr, flush=True)


def parse_manifest_media(manifest_paths: List[Path]) -> Set[str]:
    """
    Read the given manifest TSV files and return the set of media_file paths referenced.
    """
    media_files: Set[str] = set()
    for mpath in manifest_paths:
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter="\t")
                if "media_file" not in reader.fieldnames:
                    warn(f"{mpath} missing 'media_file' column; skipping")
                    continue
                for row in reader:
                    mf = row.get("media_file", "").strip()
                    if mf:
                        media_files.add(mf)
        except FileNotFoundError:
            warn(f"Manifest not found: {mpath}")
        except Exception as e:
            warn(f"Failed to parse manifest {mpath}: {e}")
    return media_files


def parse_ngram_thresholds(spec: str) -> Dict[int, int]:
    """
    Parse a spec like "1:10,2:4,3:4" into {1: 10, 2: 4, 3: 4}.
    """
    mapping: Dict[int, int] = {}
    if not spec:
        return mapping
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"Invalid ngram threshold chunk (missing ':'): {chunk}")
        length_str, count_str = chunk.split(":", 1)
        try:
            length = int(length_str)
            count = int(count_str)
        except ValueError:
            raise ValueError(f"Invalid ngram threshold chunk (need ints): {chunk}")
        if length <= 0 or count <= 0:
            raise ValueError(f"Invalid ngram threshold values: {chunk}")
        mapping[length] = count
    return mapping


def get_required_repeats(ngram_thresholds: Dict[int, int], phrase_len: int) -> int:
    """
    For a phrase of length phrase_len, return the required repeat count
    based on ngram_thresholds.

    If there is an exact key for phrase_len, use it.
    Otherwise, use the rule with the largest key <= phrase_len.
    If no key <= phrase_len exists, return -1 to indicate "no rule".
    """
    if phrase_len in ngram_thresholds:
        return ngram_thresholds[phrase_len]
    if not ngram_thresholds:
        return -1
    keys = sorted(ngram_thresholds.keys())
    candidates = [k for k in keys if k <= phrase_len]
    if not candidates:
        return -1
    return ngram_thresholds[max(candidates)]


def detect_repeated_phrase_segments(
    words_tsv_path: Path,
    media_file: Path,
    ngram_thresholds: Dict[int, int],
    max_window: float,
    verbose: bool = True,
) -> List[Dict]:
    """
    Scan words.tsv at segment granularity for phrases whose entire text
    is repeated many times in a tight time window.

    We treat the entire segment's text as the "phrase". The number of
    words in that phrase is used to look up the repeat threshold.

    Returns a list of segment dicts compatible with retry manifests:
        {
            'media_file': str,
            'segment_idx': int,
            'start_time': float,
            'end_time': float,
            'confidence': float,
            'text': str,
        }
    """
    if not words_tsv_path.exists():
        warn(f"words.tsv not found: {words_tsv_path}")
        return []

    try:
        with open(words_tsv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        warn(f"Failed to read {words_tsv_path}: {e}")
        return []

    if not lines:
        return []

    header = lines[0].strip().split("\t")
    idx = {name: i for i, name in enumerate(header)}

    required = {"start", "end", "word", "seg", "confidence", "retried"}
    if not required.issubset(idx.keys()):
        warn(
            f"words.tsv {words_tsv_path} missing required columns "
            f"{required - set(idx.keys())}"
        )
        return []

    # Collect per-segment data
    seg_data: Dict[int, Dict[str, List]] = defaultdict(
        lambda: {"words": [], "starts": [], "ends": [], "confs": []}
    )

    for line in lines[1:]:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < len(header):
            continue

        try:
            seg_id = int(parts[idx["seg"]])
        except ValueError:
            continue

        # Skip already retried segments so we don't chase our tails
        retried_flag = parts[idx["retried"]].strip()
        if retried_flag == "1":
            continue

        word = parts[idx["word"]].strip()
        if not word:
            continue

        try:
            start = float(parts[idx["start"]])
            end = float(parts[idx["end"]])
            conf = float(parts[idx["confidence"]])
        except ValueError:
            continue

        seg_data[seg_id]["words"].append(word)
        seg_data[seg_id]["starts"].append(start)
        seg_data[seg_id]["ends"].append(end)
        seg_data[seg_id]["confs"].append(conf)

    # (phrase_norm, phrase_len) -> occurrences
    phrase_map: Dict[Tuple[str, int], List[Dict]] = defaultdict(list)

    for seg_id, rec in seg_data.items():
        if not rec["words"]:
            continue

        # Normalize for phrase identity
        norm_words: List[str] = []
        for w in rec["words"]:
            # keep basic contractions, strip weird punctuation
            norm = re.sub(r"[^\w'-]+", "", w.lower())
            if norm:
                norm_words.append(norm)

        if not norm_words:
            continue

        phrase_len = len(norm_words)
        required_repeats = get_required_repeats(ngram_thresholds, phrase_len)
        if required_repeats < 0:
            # No rule for this phrase length -> ignore
            continue

        phrase_norm = " ".join(norm_words)
        phrase_text = " ".join(rec["words"])  # original casing

        seg_start = min(rec["starts"])
        seg_end = max(rec["ends"]) if rec["ends"] else seg_start
        avg_conf = (
            sum(rec["confs"]) / len(rec["confs"]) if rec["confs"] else -0.5
        )

        phrase_map[(phrase_norm, phrase_len)].append(
            {
                "seg_idx": seg_id,
                "start": seg_start,
                "end": seg_end,
                "confidence": avg_conf,
                "text": phrase_text,
                "phrase_len": phrase_len,
                "required_repeats": required_repeats,
            }
        )

    suspicious_segments: List[Dict] = []

    for (phrase, phrase_len), occs in phrase_map.items():
        if not occs:
            continue

        required_repeats = occs[0]["required_repeats"]
        if len(occs) < required_repeats:
            continue

        occs.sort(key=lambda r: r["start"])
        window_start = occs[0]["start"]
        window_end = occs[-1]["end"]
        span = window_end - window_start

        if span > max_window:
            # Repeated, but spread out – probably not hallucination
            continue

        # This phrase is "too repeated" in a tight window – mark all its occurrences
        for r in occs:
            suspicious_segments.append(
                {
                    "media_file": str(media_file),
                    "segment_idx": int(r["seg_idx"]),
                    "start_time": float(r["start"]),
                    "end_time": float(r["end"]),
                    "confidence": float(r["confidence"]),
                    "text": r["text"],
                }
            )

        log(
            f"Phrase (len={phrase_len}) '{phrase}' repeated {len(occs)}x "
            f"in {span:.2f}s in {media_file.name} "
            f"(threshold: {required_repeats})",
            verbose=verbose,
        )

    if suspicious_segments:
        log(
            f"Detected {len(suspicious_segments)} repeated-phrase segments in {media_file.name}",
            verbose=verbose,
        )

    return suspicious_segments


def write_manifest(
    output_path: Path,
    segments: List[Dict],
    verbose: bool = True,
) -> None:
    """
    Write a retry manifest TSV with the given segments.

    Columns:
        media_file  segment_idx  start_time  end_time  confidence  zero_length  text
    """
    if not segments:
        log("No segments to write; not creating manifest", verbose=verbose)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(
                "media_file\tsegment_idx\tstart_time\tend_time\tconfidence\tzero_length\ttext\n"
            )
            for seg in segments:
                media_file = seg["media_file"]
                seg_idx = int(seg["segment_idx"])
                start = float(seg["start_time"])
                end = float(seg["end_time"])
                conf = float(seg.get("confidence", -0.5))
                text = (seg.get("text", "") or "").replace("\t", " ").replace(
                    "\n", " "
                )

                zero_length = 1 if end <= start else 0
                if zero_length:
                    end = start + 1.0  # fudge to keep ffmpeg happy

                f.write(
                    f"{media_file}\t{seg_idx}\t{start:.3f}\t{end:.3f}\t{conf:.3f}\t{zero_length}\t{text}\n"
                )
    except Exception as e:
        warn(f"Failed to write manifest {output_path}: {e}")
        return

    log(f"Wrote {len(segments)} segments to {output_path}", verbose=verbose)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Detect repeated-phrase hallucinations from words.tsv at segment level "
            "and emit a retry manifest."
        )
    )
    parser.add_argument(
        "manifests",
        nargs="+",
        help="Retry manifest TSV files to use as media-file sources.",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root (defaults to $PROJECT_ROOT or '.')",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output manifest path. "
            "Default: <dir_of_first_manifest>/dupe_hallu.retry_manifest.tsv"
        ),
    )
    parser.add_argument(
        "--ngram-thresholds",
        default="1:10,2:4,3:4",
        help=(
            "Comma-separated length:count rules, e.g. '1:10,2:4,3:4'. "
            "1-word phrase repeated >=10 times, 2-word >=4 times, "
            "3+ word phrases >=4 times, etc."
        ),
    )
    parser.add_argument(
        "--max-window",
        type=float,
        default=20.0,
        help="Maximum time span (seconds) for repeats (default: 20.0)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args(argv)

    manifest_paths = [Path(m) for m in args.manifests]
    project_root = (
        Path(args.project_root)
        if args.project_root is not None
        else Path(os.environ.get("PROJECT_ROOT", "."))
    )

    verbose = args.verbose

    # Decide output path
    if args.output:
        output_path = Path(args.output)
    else:
        first_dir = manifest_paths[0].parent if manifest_paths else Path(".")
        output_path = first_dir / "dupe_hallu.retry_manifest.tsv"

    try:
        ngram_thresholds = parse_ngram_thresholds(args.ngram_thresholds)
    except ValueError as e:
        warn(f"Error in --ngram-thresholds: {e}")
        return 1

    if not ngram_thresholds:
        warn("No valid ngram thresholds provided; nothing to do")
        return 1

    media_files = parse_manifest_media(manifest_paths)
    if not media_files:
        warn("No media_file entries found in given manifests; nothing to do")
        return 1

    log(
        f"Found {len(media_files)} distinct media files in manifests",
        verbose=verbose,
    )

    all_segments: List[Dict] = []
    seen_keys: Set[Tuple[str, int, float, float]] = set()

    for mf in sorted(media_files):
        media_path = Path(mf)
        base = media_path.stem
        words_tsv = project_root / "generated" / f"{base}.words.tsv"
        log(
            f"Scanning {words_tsv} for repeated-phrase hallucinations",
            verbose=verbose,
        )

        segs = detect_repeated_phrase_segments(
            words_tsv_path=words_tsv,
            media_file=media_path,
            ngram_thresholds=ngram_thresholds,
            max_window=args.max_window,
            verbose=verbose,
        )

        for seg in segs:
            key = (
                seg["media_file"],
                int(seg["segment_idx"]),
                float(seg["start_time"]),
                float(seg["end_time"]),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_segments.append(seg)

    if not all_segments:
        log("No repeated-phrase hallucinations detected.", verbose=verbose)
        return 0

    write_manifest(output_path, all_segments, verbose=verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
