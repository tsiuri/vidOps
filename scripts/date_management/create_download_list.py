#!/usr/bin/env python3
"""
Create a download list from missing dates by matching against archive metadata cache.
Output format: YouTube URLs for clips.sh pull
"""

import sys
import re
import json
from pathlib import Path
from datetime import datetime

def extract_date_from_title(title):
    """Extract date from title like 'HasanAbi February 11, 2022 – ...'"""
    pattern = r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})'
    match = re.search(pattern, title)
    if match:
        month, day, year = match.groups()
        try:
            date_obj = datetime.strptime(f"{month} {day} {year}", "%B %d %Y")
            return date_obj.strftime("%Y-%m-%d")
        except:
            return None
    return None

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Create download list from missing dates')
    parser.add_argument('missing_dates', help='File with missing dates (YYYY-MM-DD)')
    parser.add_argument('archive_cache', help='archive_metadata_cache.json file')
    parser.add_argument('--output', default='data/download_list.txt',
                       help='Output file with YouTube URLs (default: download_list.txt)')

    args = parser.parse_args()

    # Read missing dates
    missing_dates_file = Path(args.missing_dates)
    if not missing_dates_file.exists():
        print(f"[ERROR] Missing dates file not found: {missing_dates_file}")
        sys.exit(1)

    with open(missing_dates_file, 'r') as f:
        missing_dates = set(line.strip() for line in f if line.strip())

    print(f"[INFO] Loaded {len(missing_dates)} missing dates")

    # Load archive metadata cache
    archive_cache_file = Path(args.archive_cache)
    if not archive_cache_file.exists():
        print(f"[ERROR] Archive cache file not found: {archive_cache_file}")
        sys.exit(1)

    with open(archive_cache_file, 'r') as f:
        metadata_cache = json.load(f)

    print(f"[INFO] Loaded {len(metadata_cache)} videos from archive cache")

    # Build date -> video_id mapping
    date_to_video = {}
    for video_id, title in metadata_cache.items():
        date = extract_date_from_title(title)
        if date:
            # If multiple videos have same date, keep the first one
            if date not in date_to_video:
                date_to_video[date] = video_id

    print(f"[INFO] Extracted dates from {len(date_to_video)} videos")

    # Match missing dates to video IDs
    matched_videos = []
    for date in sorted(missing_dates):
        if date in date_to_video:
            video_id = date_to_video[date]
            matched_videos.append((date, video_id))

    print(f"[INFO] Matched {len(matched_videos)} videos")
    print(f"[INFO] Could not find {len(missing_dates) - len(matched_videos)} dates in temp.txt")

    # Write YouTube URLs
    output_file = Path(args.output)
    with open(output_file, 'w') as f:
        for date, video_id in matched_videos:
            url = f"https://www.youtube.com/watch?v={video_id}"
            f.write(f"{url}\n")

    print(f"\n[RESULTS]")
    print(f"  Download list written to: {output_file}")
    print(f"  Total URLs: {len(matched_videos)}")
    print(f"\n[USAGE]")
    print(f"  You can now download these with:")
    print(f"    while read url; do ./clips.sh pull \"$url\"; done < {output_file}")
    print(f"  Or download one at a time:")
    print(f"    ./clips.sh pull \"$(head -1 {output_file})\"")

    # Show sample
    if matched_videos:
        print(f"\n[SAMPLE] First 5 URLs:")
        for date, video_id in matched_videos[:5]:
            url = f"https://www.youtube.com/watch?v={video_id}"
            print(f"  {date}: {url}")

if __name__ == "__main__":
    main()
