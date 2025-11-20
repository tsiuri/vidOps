#!/usr/bin/env python3
import re
import subprocess
import json
import os
from pathlib import Path
from datetime import datetime
from collections import defaultdict

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "."))

# Parse date from Hasan video title format: "HasanAbi February 11, 2022 – ..."
def extract_date_from_title(title):
    """Extract date from title like 'HasanAbi February 11, 2022 – ...'"""
    # Pattern: Month Day, Year
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

# Get dates from current podcasts (from filenames)
def get_podcast_dates(directory):
    """Extract dates from opus filenames in pull directory"""
    dates = set()
    pull_dir = Path(directory) / "pull"
    
    for opus_file in pull_dir.glob("*.opus"):
        filename = opus_file.name
        # Extract title part (after the date and hyphen)
        # Format: VIDEOID__YYYY-MM-DD - TITLE.opus
        date = extract_date_from_title(filename)
        if date:
            dates.add(date)
            print(f"[PODCAST] {date}: {filename[:80]}")
    
    return sorted(dates)

# Get dates from archive videos (need to fetch metadata)
def get_archive_dates(archive_file, use_cache=True):
    """Fetch metadata for archive video IDs and extract dates"""
    dates = set()
    cache_file = Path(archive_file).parent / "archive_metadata_cache.json"
    
    # Try to load from cache
    metadata_cache = {}
    if use_cache and cache_file.exists():
        print(f"[INFO] Loading cached metadata from {cache_file}")
        with open(cache_file, 'r') as f:
            metadata_cache = json.load(f)
    
    video_ids = []
    with open(archive_file, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                video_ids.append(parts[1])
    
    print(f"[INFO] Found {len(video_ids)} videos in archive")
    print(f"[INFO] Cached: {len(metadata_cache)}, Need to fetch: {len(video_ids) - len(metadata_cache)}")
    
    # Fetch metadata for videos not in cache
    for i, vid_id in enumerate(video_ids):
        if vid_id in metadata_cache:
            title = metadata_cache[vid_id]
        else:
            # Fetch from YouTube
            print(f"[{i+1}/{len(video_ids)}] Fetching metadata for {vid_id}...")
            try:
                result = subprocess.run(
                    ['yt-dlp', '--skip-download', '--print', '%(title)s', f'https://www.youtube.com/watch?v={vid_id}'],
                    capture_output=True, text=True, timeout=10
                )
                title = result.stdout.strip()
                metadata_cache[vid_id] = title
                
                # Save cache every 10 videos
                if i % 10 == 0:
                    with open(cache_file, 'w') as f:
                        json.dump(metadata_cache, f, indent=2)
            except Exception as e:
                print(f"[ERROR] Failed to fetch {vid_id}: {e}")
                continue
        
        date = extract_date_from_title(title)
        if date:
            dates.add(date)
            print(f"[ARCHIVE] {date}: {title[:80]}")
    
    # Save final cache
    with open(cache_file, 'w') as f:
        json.dump(metadata_cache, f, indent=2)
    
    return sorted(dates)

# Main comparison
def main():
    base_dir = PROJECT_ROOT
    archive_file = "/mnt/firecuda/Videos/yt-videos/collargate/yt-downloader-test/Vods 2023 to 2025 fixed/original_scripts/.clips-download-archive.txt"
    
    print("=" * 80)
    print("EXTRACTING DATES FROM PODCASTS (1022 files)")
    print("=" * 80)
    podcast_dates = get_podcast_dates(base_dir)
    
    print("\n" + "=" * 80)
    print("EXTRACTING DATES FROM ARCHIVE (927 videos)")
    print("=" * 80)
    archive_dates = get_archive_dates(archive_file)
    
    # Compare
    print("\n" + "=" * 80)
    print("COMPARISON RESULTS")
    print("=" * 80)
    print(f"Podcast dates: {len(podcast_dates)}")
    print(f"Archive dates: {len(archive_dates)}")
    
    podcast_set = set(podcast_dates)
    archive_set = set(archive_dates)
    
    only_in_podcasts = podcast_set - archive_set
    only_in_archive = archive_set - podcast_set
    in_both = podcast_set & archive_set
    
    print(f"\nDates in both: {len(in_both)}")
    print(f"Only in podcasts: {len(only_in_podcasts)}")
    print(f"Only in archive: {len(only_in_archive)}")
    
    # Write results
    output_file = PROJECT_ROOT / "results" / "date_comparison.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w') as f:
        f.write("DATES ONLY IN PODCASTS (1022):\n")
        for date in sorted(only_in_podcasts):
            f.write(f"{date}\n")
        f.write(f"\nDATES ONLY IN ARCHIVE (927):\n")
        for date in sorted(only_in_archive):
            f.write(f"{date}\n")
        f.write(f"\nDATES IN BOTH:\n")
        for date in sorted(in_both):
            f.write(f"{date}\n")
    
    print(f"\n[DONE] Results written to date_comparison.txt")

if __name__ == "__main__":
    main()
