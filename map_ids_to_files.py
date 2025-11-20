#!/usr/bin/env python3
"""Map YouTube IDs to opus files"""
import os
from pathlib import Path

# Config
ID_FILE = "logs/no_transcripts_available.txt"
OUTPUT_FILE = "transcripts_needed.txt"
PULL_DIR = Path("pull")

# Build ID -> file mapping
print("Building file index...")
id_map = {}
for f in PULL_DIR.glob("*.opus"):
    # Extract ID (everything before the first __)
    name = f.name
    if "__" in name:
        vid_id = name.split("__")[0]
        id_map[vid_id] = str(f)

print(f"Indexed {len(id_map)} opus files")

# Match IDs
print("Matching IDs to files...")
found = 0
not_found = []

with open(ID_FILE) as ids, open(OUTPUT_FILE, "w") as out:
    for line in ids:
        vid = line.strip()
        if not vid:
            continue

        if vid in id_map:
            out.write(id_map[vid] + "\n")
            found += 1
        else:
            not_found.append(vid)

print(f"Found: {found} opus files")
print(f"Not found: {len(not_found)} IDs")

if not_found:
    print(f"\nMissing IDs (first 10):")
    for vid in not_found[:10]:
        print(f"  {vid}")

print(f"\nOutput written to: {OUTPUT_FILE}")
