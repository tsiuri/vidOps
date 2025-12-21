#!/usr/bin/env python3
"""
Convert Audacity label exports (start<TAB>end<TAB>label) to RTTM.

Usage:
  labels_to_rttm.py <labels.txt> <ytid> [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def to_rttm(label_path: Path, ytid: str) -> str:
    lines = []
    with label_path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 3:
                continue
            try:
                start = float(row[0])
                end = float(row[1])
            except Exception:
                continue
            spk = row[2].strip() or "UNKNOWN"
            dur = max(0.0, end - start)
            lines.append(f"SPEAKER {ytid} 1 {start:.3f} {dur:.3f} <NA> <NA> {spk} <NA> <NA>")
    return "\n".join(lines) + ("\n" if lines else "")


def main():
    ap = argparse.ArgumentParser(description="Convert Audacity label TXT to RTTM.")
    ap.add_argument("labels", type=Path, help="Audacity label file (start<TAB>end<TAB>label)")
    ap.add_argument("ytid", help="YTID/file_id for RTTM")
    ap.add_argument("--out-dir", type=Path, help="Output directory (default: same as labels)")
    args = ap.parse_args()

    out_dir = args.out_dir or args.labels.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.ytid}.rttm"

    text = to_rttm(args.labels, args.ytid)
    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
