#!/usr/bin/env python3
"""
Scan a media root for downloaded files and emit a TSV manifest for assets.

Outputs: logs/db/media_assets.tsv with columns:
  ytid, kind, path, bytes, rel_path

Defaults:
  - root: PROJECT_ROOT/pull (override via MEDIA_SCAN_ROOT or --root)
  - kinds: media (for all matched extensions)
  - extensions: .mp4 .mkv .webm .m4a .opus .mp3 .wav .flac .mka
  - rel_path: <prefix>/<relative-to-root-path> (prefix default: raw)

Usage:
  scripts/db/export_media_assets.py [--root DIR] [--output PATH]
                                    [--prefix raw] [--extensions ".mp4,.mkv"]
"""

import argparse
import os
from pathlib import Path
import csv
import re

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", ".")).resolve()
DEFAULT_ROOT = Path(os.environ.get("MEDIA_SCAN_ROOT", PROJECT_ROOT / "pull")).resolve()
DEFAULT_OUT = PROJECT_ROOT / "logs/db/media_assets.tsv"

YTID_RE = re.compile(r"([A-Za-z0-9_-]{11})__")
DEFAULT_EXTS = [
    ".mp4",
    ".mkv",
    ".webm",
    ".m4a",
    ".opus",
    ".mp3",
    ".wav",
    ".flac",
    ".mka",
]


def looks_like_ytid(path: Path) -> str | None:
    m = YTID_RE.search(path.name)
    if m:
        return m.group(1)
    return None


def main():
    parser = argparse.ArgumentParser(description="Export media assets manifest from a download root.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Root directory to scan (default: PROJECT_ROOT/pull or MEDIA_SCAN_ROOT).")
    parser.add_argument("--output", default=str(DEFAULT_OUT), help="Output TSV path (default: logs/db/media_assets.tsv).")
    parser.add_argument("--prefix", default="raw", help="Prefix to prepend to rel_path/path columns (default: raw). Use '' to disable.")
    parser.add_argument("--extensions", default=",".join(DEFAULT_EXTS), help="Comma-separated list of allowed media extensions.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    out_path = Path(args.output)
    prefix = args.prefix.strip()
    allowed_exts = {e.lower().strip() for e in args.extensions.split(",") if e.strip()}

    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, str, int, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_exts:
            continue
        ytid = looks_like_ytid(path)
        if not ytid:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path.name
        rel_path = f"{prefix}/{rel}" if prefix else str(rel)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rows.append((ytid, "media", rel_path, size, rel_path))

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["ytid", "kind", "path", "bytes", "rel_path"])
        for row in sorted(rows, key=lambda r: r[0]):
            w.writerow(row)

    print(f"Wrote {out_path} ({len(rows)} media assets) from root {root}")


if __name__ == "__main__":
    main()
