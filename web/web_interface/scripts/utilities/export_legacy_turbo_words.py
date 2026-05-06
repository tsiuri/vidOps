#!/usr/bin/env python3
"""
Export legacy *.turbo.words.tsv files into a single TSV for DB ingest.

Each legacy file is expected to live under a directory and follow the header:
start    end    word    seg    confidence    retried

Output header (compatible with scripts/db/load_words.sql in VidOps):
ytid    source    idx    word    start_sec    end_sec    confidence    segment_id
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List


def export_legacy_words(
    input_dir: Path,
    output_path: Path,
    source: str,
    lower: bool,
    glob_pattern: str = "*.turbo.words.tsv",
) -> tuple[int, int]:
    """Export all matching legacy word files into a single TSV."""
    # Allow very long fields from legacy exports.
    try:
        csv.field_size_limit(sys.maxsize)
    except OverflowError:
        csv.field_size_limit(10**9)
    files: List[Path] = sorted(input_dir.glob(glob_pattern))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    with output_path.open("w", newline="", encoding="utf-8") as outf:
        writer = csv.writer(outf, delimiter="\t")
        writer.writerow(["ytid", "source", "idx", "word", "start_sec", "end_sec", "confidence", "segment_id"])

        for path in files:
            ytid = path.name.split("__", 1)[0]
            with path.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for idx, row in enumerate(reader):
                    word = row.get("word", "")
                    if lower:
                        word = word.lower()
                    writer.writerow(
                        [
                            ytid,
                            source,
                            idx,
                            word,
                            row.get("start") or "",
                            row.get("end") or "",
                            row.get("confidence") or "",
                            "",  # segment_id left empty
                        ]
                    )
                    total_rows += 1
    return len(files), total_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Export legacy *.turbo.words.tsv files into a single TSV for DB ingest.")
    parser.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        help="Directory containing legacy *.turbo.words.tsv files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/db/legacy_words_export.tsv"),
        help="Output TSV path (default: logs/db/legacy_words_export.tsv).",
    )
    parser.add_argument(
        "--source",
        default="whisper-turbo",
        help="Value to use for the `source` column (default: whisper-turbo).",
    )
    parser.add_argument(
        "--no-lower",
        action="store_true",
        help="Do not lowercase words (default is to lowercase).",
    )
    parser.add_argument(
        "--glob",
        default="*.turbo.words.tsv",
        help="Glob pattern to match legacy files (default: *.turbo.words.tsv).",
    )
    args = parser.parse_args()

    files, rows = export_legacy_words(
        input_dir=args.input_dir,
        output_path=args.output,
        source=args.source,
        lower=not args.no_lower,
        glob_pattern=args.glob,
    )
    print(f"Export complete: {files} file(s), {rows} row(s) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
