#!/usr/bin/env python3
"""
Move .opus files (and their .json files) whose title dates match a list.
Converts YYYY-MM-DD format to "Month Day, Year" text format for matching.
"""

import sys
import re
from pathlib import Path
from datetime import datetime
import shutil

def date_to_text_format(date_str):
    """Convert 2024-01-31 to 'January 31, 2024'"""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt.strftime("%B %d, %Y").replace(" 0", " ")  # Remove leading zero from day
    except Exception as e:
        print(f"[WARN] Invalid date format: {date_str} - {e}")
        return None

def extract_text_date_from_filename(filename):
    """Extract the text date like 'January 5, 2024' from filename"""
    # Pattern: HasanAbi Month Day, Year
    pattern = r'HasanAbi\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})'
    match = re.search(pattern, filename, re.IGNORECASE)
    if match:
        month, day, year = match.groups()
        return f"{month} {day}, {year}"
    return None

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Move files by date list')
    parser.add_argument('dates_file', help='File containing dates (YYYY-MM-DD format)')
    parser.add_argument('source_dir', help='Directory containing .opus files')
    parser.add_argument('--dest-dir', default=None,
                       help='Destination directory (default: source_dir/selected_dates)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be moved without moving')

    args = parser.parse_args()

    # Read dates
    dates_file = Path(args.dates_file)
    if not dates_file.exists():
        print(f"[ERROR] Dates file not found: {dates_file}")
        sys.exit(1)

    with open(dates_file, 'r') as f:
        date_lines = [line.strip() for line in f if line.strip()]

    # Convert to text format
    target_dates = set()
    for date_str in date_lines:
        text_date = date_to_text_format(date_str)
        if text_date:
            target_dates.add(text_date)

    print(f"[INFO] Loaded {len(target_dates)} dates from {dates_file}")
    print(f"[INFO] Example dates: {list(target_dates)[:3]}")

    # Setup directories
    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        print(f"[ERROR] Source directory not found: {source_dir}")
        sys.exit(1)

    if args.dest_dir:
        dest_dir = Path(args.dest_dir)
    else:
        dest_dir = source_dir / "selected_dates"

    if not args.dry_run:
        dest_dir.mkdir(exist_ok=True)

    print(f"[INFO] Source: {source_dir}")
    print(f"[INFO] Destination: {dest_dir}")
    if args.dry_run:
        print(f"[INFO] DRY RUN - No files will be moved")

    # Find matching files
    opus_files = list(source_dir.glob("*.opus"))
    print(f"\n[SCAN] Found {len(opus_files)} .opus files")

    matched_files = []
    for opus_file in opus_files:
        file_date = extract_text_date_from_filename(opus_file.name)
        if file_date and file_date in target_dates:
            matched_files.append((opus_file, file_date))

    print(f"[MATCH] Found {len(matched_files)} files matching date list")

    # Move files
    moved_count = 0
    for opus_file, file_date in matched_files:
        # Find corresponding .json files
        json_pattern = opus_file.stem + "*.json"
        json_files = list(source_dir.glob(json_pattern))

        files_to_move = [opus_file] + json_files

        print(f"\n[{file_date}] {opus_file.name}")
        for file in files_to_move:
            dest_file = dest_dir / file.name
            if args.dry_run:
                print(f"  Would move: {file.name}")
            else:
                try:
                    shutil.move(str(file), str(dest_file))
                    print(f"  ✓ Moved: {file.name}")
                    moved_count += 1
                except Exception as e:
                    print(f"  ✗ Error moving {file.name}: {e}")

    # Summary
    print(f"\n{'=' * 80}")
    print(f"[SUMMARY]")
    if args.dry_run:
        print(f"  Would move {len(matched_files)} .opus files and their .json files")
    else:
        print(f"  Moved {moved_count} files ({len(matched_files)} .opus + {moved_count - len(matched_files)} .json)")
        print(f"  Destination: {dest_dir}")

if __name__ == "__main__":
    main()
