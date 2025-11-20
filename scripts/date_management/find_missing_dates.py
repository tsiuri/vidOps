#!/usr/bin/env python3
"""
Find which dates from the list don't have corresponding .opus files.
"""

import sys
import re
import os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "."))

def date_to_text_format(date_str):
    """Convert 2024-01-31 to 'January 31, 2024'"""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception as e:
        return None

def extract_text_date_from_filename(filename):
    """Extract the text date like 'January 5, 2024' from filename"""
    pattern = r'HasanAbi\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})'
    match = re.search(pattern, filename, re.IGNORECASE)
    if match:
        month, day, year = match.groups()
        return f"{month} {day}, {year}"
    return None

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Find missing dates')
    parser.add_argument('dates_file', help='File containing dates (YYYY-MM-DD format)')
    parser.add_argument('source_dir', help='Directory containing .opus files')
    parser.add_argument('--output', default=str(PROJECT_ROOT / 'data' / 'missing_dates.txt'),
                       help='Output file for missing dates (default: data/missing_dates.txt)')

    args = parser.parse_args()

    # Read requested dates
    dates_file = Path(args.dates_file)
    with open(dates_file, 'r') as f:
        date_lines = [line.strip() for line in f if line.strip()]

    # Convert to text format and keep original
    requested_dates = {}  # text_format -> original_format
    for date_str in date_lines:
        text_date = date_to_text_format(date_str)
        if text_date:
            requested_dates[text_date] = date_str

    print(f"[INFO] Requested {len(requested_dates)} dates")

    # Find existing dates in .opus files
    source_dir = Path(args.source_dir)
    opus_files = list(source_dir.glob("*.opus"))

    existing_dates = set()
    for opus_file in opus_files:
        file_date = extract_text_date_from_filename(opus_file.name)
        if file_date:
            existing_dates.add(file_date)

    print(f"[INFO] Found {len(existing_dates)} unique dates in .opus files")

    # Find missing dates
    missing_text_dates = set(requested_dates.keys()) - existing_dates
    missing_original_dates = [requested_dates[td] for td in sorted(missing_text_dates)]

    print(f"[INFO] Missing {len(missing_original_dates)} dates")

    # Write to file
    output_file = Path(args.output)
    with open(output_file, 'w') as f:
        for date_str in missing_original_dates:
            f.write(f"{date_str}\n")

    print(f"\n[RESULTS]")
    print(f"  Requested: {len(requested_dates)}")
    print(f"  Found: {len(requested_dates) - len(missing_original_dates)}")
    print(f"  Missing: {len(missing_original_dates)}")
    print(f"  Missing dates written to: {output_file}")

    # Show first few missing dates
    if missing_original_dates:
        print(f"\n[SAMPLE] First 10 missing dates:")
        for date in missing_original_dates[:10]:
            text = date_to_text_format(date)
            print(f"  {date} ({text})")

if __name__ == "__main__":
    main()
