#!/usr/bin/env python3
"""
Compute diarization error rate (DER) over a set of ytids.

Inputs:
  - YTID list (TSV/plain, first column = ytid)
  - Reference RTTM directory (one <ytid>.rttm per file)
  - Hypothesis dir containing diarized_timestamps.tsv under results/diarization/<ytid>/

Outputs:
  - Prints per-ytid DER and an aggregate DER.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Tuple


def parse_args():
    p = argparse.ArgumentParser(description="Compute DER for diarization outputs.")
    p.add_argument("--ytid-file", required=True, help="TSV/plain file with ytids (first column).")
    p.add_argument("--reference-dir", required=True, type=Path, help="Directory of reference RTTM files (<ytid>.rttm).")
    p.add_argument(
        "--hyp-dir",
        type=Path,
        default=Path("results/diarization"),
        help="Root dir containing results/diarization/<ytid>/diarized_timestamps.tsv",
    )
    p.add_argument("--max-files", type=int, help="Optional cap on number of ytids to evaluate.")
    return p.parse_args()


def load_ytids(path: Path, max_files: int | None) -> List[str]:
    ytids: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if not row:
                continue
            ytid = row[0].strip()
            if not ytid or ytid.lower() == "ytid":
                continue
            ytids.append(ytid)
            if max_files and len(ytids) >= max_files:
                break
    return ytids


def load_rttm(path: Path):
    """
    Minimal RTTM parser -> list of (speaker, start, end).
    """
    spans: List[Tuple[str, float, float]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.strip().split()
            if len(parts) < 9 or parts[0].upper() != "SPEAKER":
                continue
            try:
                start = float(parts[3])
                dur = float(parts[4])
                spk = parts[7]
                spans.append((spk, start, start + dur))
            except Exception:
                continue
    return spans


def load_hyp_tsv(path: Path):
    """
    Parse diarized_timestamps.tsv -> list of (speaker, start, end).
    """
    spans: List[Tuple[str, float, float]] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for i, row in enumerate(reader):
            if i == 0 and row and row[0].lower() == "ytid":
                continue
            if len(row) < 4:
                continue
            try:
                _, speaker, start, end, *_ = row
                spans.append((speaker, float(start), float(end)))
            except Exception:
                continue
    return spans


def to_annotation(spans):
    """
    Convert spans -> pyannote.core.Annotation.
    """
    from pyannote.core import Annotation, Segment  # type: ignore

    ann = Annotation()
    for spk, s, e in spans:
        ann[Segment(s, e)] = spk
    return ann


def main():
    args = parse_args()
    try:
        from pyannote.metrics.diarization import DiarizationErrorRate  # type: ignore
    except ImportError:
        raise SystemExit("pyannote.metrics is required. Install via pip: pyannote.metrics")

    ytids = load_ytids(Path(args.ytid_file), args.max_files)
    if not ytids:
        raise SystemExit("No ytids loaded from list.")

    metric = DiarizationErrorRate()
    per_file: Dict[str, float] = {}

    for ytid in ytids:
        ref_path = args.reference_dir / f"{ytid}.rttm"
        hyp_path = args.hyp_dir / ytid / "diarized_timestamps.tsv"
        if not ref_path.exists():
            print(f"[warn] reference missing for {ytid}: {ref_path}")
            continue
        if not hyp_path.exists():
            print(f"[warn] hypothesis missing for {ytid}: {hyp_path}")
            continue

        ref_spans = load_rttm(ref_path)
        hyp_spans = load_hyp_tsv(hyp_path)
        if not ref_spans or not hyp_spans:
            print(f"[warn] empty spans for {ytid}")
            continue

        ref_ann = to_annotation(ref_spans)
        hyp_ann = to_annotation(hyp_spans)
        der = metric(ref_ann, hyp_ann)
        per_file[ytid] = der
        print(f"{ytid}\tDER={der:.4f}")

    if per_file:
        mean_der = sum(per_file.values()) / len(per_file)
        print(f"\nFiles evaluated: {len(per_file)}")
        print(f"Mean DER: {mean_der:.4f}")
    else:
        print("No files evaluated.")


if __name__ == "__main__":
    main()
