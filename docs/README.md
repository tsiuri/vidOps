# Workspace Organization Guide

**Last Updated:** November 7, 2025

This workspace has been reorganized for better maintainability and clarity. This document explains the new structure and how to use it.

## Directory Structure

```
workspace/
├── scripts/                    # All executable scripts organized by function
│   ├── voice_filtering/       # Voice detection and filtering
│   ├── video_processing/      # Video stitching and processing
│   ├── date_management/       # Date-based file operations
│   ├── transcription/         # Transcription scripts
│   ├── utilities/             # Common utilities (clips.sh, sort_clips.py, etc.)
│   └── gpu_tools/             # GPU management tools
│
├── data/                       # Input data and reference files
│   ├── dates_to_pull_from_2024and5.txt
│   ├── missing_dates.txt
│   ├── hotwords.txt
│   ├── .clips-download-archive.txt
│   └── *.tsv files
│
├── results/                    # Output and analysis results
│   ├── voice_filtered/         # Voice filtering results
│   ├── date_comparison.txt
│   └── temp.txt
│
├── media/                      # All video/audio files
│   ├── raw/                    # Original downloads (pull/)
│   ├── transcribed/            # Transcribed files (generated/)
│   ├── clips/                  # All clip variations (cuts/, cuts_exact/, etc.)
│   └── final/                  # Final stitched outputs
│
├── logs/                       # All log files organized by operation
│   ├── transcription/
│   ├── stitching/
│   └── processing/
│
├── backups/                    # Backups and archives
│   └── original_scripts/
│
├── config/                     # Configuration files
│   └── clips.env
│
├── docs/                       # Documentation
│   ├── README.md (this file)
│   ├── VOICE_FILTERING.md
│   ├── VIDEO_STITCHING.md
│   └── DATE_MANAGEMENT.md
│
├── voice_filter_env/           # Python virtual environment
│
└── [wrapper scripts]           # Backward-compatible wrapper scripts in root
    ├── clips.sh
    ├── stitch_videos.sh
    ├── filter_voice.py
    └── ... (all common scripts)
```

## Quick Start

### Option 1: Use Wrapper Scripts (Recommended)

For backward compatibility, all common scripts have wrappers in the root directory. You can continue using them as before:

```bash
# Voice filtering (works from root)
./filter_voice_parallel.py --clips-dir /path/to/clips --reference-dir /path/to/refs

# Video stitching (works from root)
./stitch_videos_batched.sh media/clips/confirmedPiker1/ media/final/output.mp4 date_timestamp

# Date management (works from root)
./find_missing_dates.py --dates-file data/dates.txt --search-dir /path/to/search
```

### Option 2: Use Full Paths

For more control, use the full paths to scripts:

```bash
# Voice filtering
python3 scripts/voice_filtering/filter_voice_parallel.py --clips-dir /path/to/clips --reference-dir /path/to/refs

# Video stitching
scripts/video_processing/stitch_videos_batched.sh media/clips/confirmedPiker1/ media/final/output.mp4 date_timestamp

# Date management
python3 scripts/date_management/find_missing_dates.py --dates-file data/dates.txt --search-dir /path/to/search
```

## Common Workflows

### 1. Voice Filtering Workflow

See [VOICE_FILTERING.md](VOICE_FILTERING.md) for details.

```bash
# Step 1: Run voice filter
./filter_voice_parallel_chunked.py \
  --clips-dir /path/to/clips \
  --reference-dir /path/to/reference \
  --output results/voice_filtered/results.json

# Step 2: Extract matching clips
python3 -c "
import json
with open('results/voice_filtered/results.json') as f:
    data = json.load(f)
with open('results/voice_filtered/matched_clips.txt', 'w') as out:
    for clip in data['results']:
        if clip.get('is_match', clip.get('is_hasan', False)):
            out.write(f\"{clip['clip_path']}\n\")
"
```

### 2. Video Stitching Workflow

See [VIDEO_STITCHING.md](VIDEO_STITCHING.md) for details.

```bash
# Recommended: Batched approach for reliability
./stitch_videos_batched.sh \
  media/clips/confirmedPiker1/ \
  media/final/output.mp4 \
  date_timestamp
```

### 3. Date Management Workflow

See [DATE_MANAGEMENT.md](DATE_MANAGEMENT.md) for details.

```bash
# Step 1: Find missing dates
./find_missing_dates.py \
  --dates-file data/dates_to_pull.txt \
  --search-dir /path/to/search \
  --output data/missing_dates.txt

# Step 2: Create download list
./create_download_list.py \
  data/missing_dates.txt \
  /path/to/archive_metadata_cache.json

# Step 3: Download missing files
while read url; do ./clips.sh pull "$url"; done < data/download_list.txt
```

## Migration from Old Structure

If you have existing commands or scripts that reference the old structure:

1. **Most scripts work unchanged** - Wrapper scripts provide backward compatibility
2. **Data files have moved** - Update paths:
   - Old: `./dates.txt` → New: `data/dates.txt`
   - Old: `./missing_dates.txt` → New: `data/missing_dates.txt`
3. **Results files** - Now in `results/` directory
4. **Media directories** - Now under `media/` hierarchy

## Environment Setup

An optional environment helper script is available:

```bash
# Source environment variables
source set_paths.sh

# Now you can use:
echo $DATA_DIR      # Points to data/
echo $RESULTS_DIR   # Points to results/
echo $MEDIA_DIR     # Points to media/
echo $SCRIPTS_DIR   # Points to scripts/
```

## Troubleshooting

### Script can't find another script

Make sure you're running from the workspace root directory, or use the wrapper scripts which handle paths automatically.

### Data files not found

Data files are now in `data/` directory. Update your command:
```bash
# Old
--dates-file dates.txt

# New
--dates-file data/dates.txt
```

### Output files go to wrong location

Default output paths have been updated. You can override with explicit paths:
```bash
--output results/my_output.txt
```

## Maintenance Files

These files are in the root directory for maintenance purposes:

- `reorganization_manifest.txt` - Complete log of what was moved where
- `path_fixes_log.txt` - Log of all path updates made
- `script_compatibility_test.txt` - Test results
- `reorganize.sh` - The reorganization script used
- `fix_paths.sh` - The path fixing script used
- `test_scripts.sh` - Compatibility test script

## Benefits of New Structure

1. **Easy to find scripts** - Organized by function
2. **Cleaner root directory** - Only wrapper scripts and core files
3. **Logical data organization** - Input data, results, and media separated
4. **Better logging** - Logs organized by operation type
5. **Backward compatible** - Wrapper scripts maintain old usage patterns
6. **Future-proof** - Easy to add new script categories

## Getting Help

For detailed information on specific workflows, see:
- [VOICE_FILTERING.md](VOICE_FILTERING.md) - Voice detection and filtering
- [VIDEO_STITCHING.md](VIDEO_STITCHING.md) - Video concatenation methods
- [DATE_MANAGEMENT.md](DATE_MANAGEMENT.md) - Date-based file operations

For issues or questions, check the individual script help:
```bash
# Python scripts
python3 scripts/path/to/script.py --help

# Shell scripts
scripts/path/to/script.sh  # (will show usage)
```
