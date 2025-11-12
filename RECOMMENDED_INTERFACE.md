# Recommended Interface - How to Use This Workspace

## TL;DR - The Simple Rule

**Always run from workspace root using wrapper scripts:**

```bash
cd "/mnt/firecuda/Videos/yt-videos/collargate/yt-downloader-test/the 2021 2021 vods bak folder b4 cleanup/"
./clips.sh pull <url>
./filter_voice_parallel.py [args]
./stitch_videos_batched.sh [args]
```

That's it. Everything will work correctly.

---

## The Problem You Encountered

When you ran `clips.sh pull` from inside `scripts/utilities/`, it created `pull/` in the wrong location:

```bash
# ✗ Wrong - creates pull/ in scripts/utilities/
cd scripts/utilities
./clips.sh pull <url>
# Result: scripts/utilities/pull/ ← Wrong place!

# ✓ Correct - creates pull/ in workspace root
cd /path/to/workspace
./clips.sh pull <url>
# Result: workspace/pull/ ← Correct!
```

## Why This Happens

`clips.sh` creates directories relative to the **current working directory**, not relative to where the script is located. This is intentional - it allows you to organize media in different project folders.

## The Fix Applied

The wrapper script (`clips.sh` in workspace root) now:
1. Changes to workspace root
2. Then executes the real `scripts/utilities/clips.sh`
3. Ensures `pull/` goes to the right place

## Recommended Workflow Directory

```
workspace/
├── pull/                      # ← clips.sh pull writes here
├── cuts/                      # ← clips.sh cut-local writes here
├── media/
│   ├── clips/
│   │   └── confirmedPiker1/  # ← filtered clips ready to stitch
│   └── final/                # ← final videos
├── data/                      # ← your data files
├── results/                   # ← analysis results
└── [wrapper scripts]          # ← USE THESE
```

## All Common Commands

### From workspace root, using wrappers:

```bash
cd /path/to/workspace

# Download videos
./clips.sh pull "URL"
./clips.sh pull "@channel_handle"

# Find clips with specific phrases
./clips.sh hits -q "phrase1,phrase2" -o results/wanted.tsv

# Cut clips from local files
./clips.sh cut-local results/wanted.tsv media/clips/raw/

# Filter by voice matching
./filter_voice_parallel_chunked.py \
  --clips-dir media/clips/raw/ \
  --reference-dir data/reference_clips/ \
  --output results/voice_filtered/results.json

# Stitch filtered clips
./stitch_videos_batched.sh \
  media/clips/confirmedPiker1/ \
  media/final/output.mp4 \
  date_timestamp

# Find missing dates
./find_missing_dates.py \
  --dates-file data/dates.txt \
  --search-dir /path/to/existing \
  --output data/missing.txt

# Create download list
./create_download_list.py \
  data/missing.txt \
  /path/to/archive_cache.json

# Batch download
while read url; do ./clips.sh pull "$url"; done < data/download_list.txt
```

## What If I Want to Run from a Different Directory?

You can, but you must use absolute paths:

```bash
# From anywhere
cd /my/data/folder

/full/path/to/workspace/clips.sh pull <url>
# Creates: /my/data/folder/pull/ ← In YOUR current directory

python3 /full/path/to/workspace/scripts/voice_filtering/filter_voice.py \
  --clips-dir ./my_clips \
  --output ./results.json
```

This is more complex and not recommended unless you have a specific need.

## Environment Variables (Optional)

For complex workflows, source the environment helper:

```bash
cd /path/to/workspace
source set_paths.sh

# Now you can use:
echo $WORKSPACE_ROOT  # /path/to/workspace
echo $DATA_DIR        # /path/to/workspace/data
echo $RESULTS_DIR     # /path/to/workspace/results
echo $MEDIA_DIR       # /path/to/workspace/media

# Use in commands:
./filter_voice.py \
  --clips-dir "$MEDIA_DIR/clips/raw" \
  --output "$RESULTS_DIR/voice_filtered/results.json"
```

## Summary

**Best Practice:**
1. `cd` to workspace root
2. Use wrapper scripts (`./script_name.sh` or `./script_name.py`)
3. Use relative paths (`data/`, `media/`, `results/`)
4. Everything works and goes where expected

**Don't:**
- Run scripts from inside `scripts/` subdirectories
- Use absolute paths unless necessary
- Forget where you are when running commands

**The Golden Rule:** If you're about to run a command and you're not in the workspace root, stop and `cd` there first.
