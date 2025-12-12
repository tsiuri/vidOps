# Extra Utilities — Unwrapped Scripts

Generated: 2025-11-22

This document provides detailed help for standalone scripts not directly wrapped by `workspace.sh`.
These are power-user tools that can be called directly when needed.

---

## UTILITIES - STANDALONE SCRIPTS

### 1. mark_success.sh
**Purpose:** Mark a video ID as successfully downloaded in pull logs

**Location:** `scripts/utilities/mark_success.sh`

**Description:**
Internal utility used by download workflows to track successful downloads. Updates planned/pending/succeeded TSV manifests for download tracking. Automatically maintains consistency across log files.

**Usage:**
```bash
scripts/utilities/mark_success.sh <video_id> <base_prefix>
```

**Arguments:**
- `video_id` - YouTube video ID (e.g., `dQw4w9WgXcQ`)
- `base_prefix` - Log file prefix (e.g., `logs/pull/20251122_123456Z`)

**Behavior:**
1. Finds video entry in `${base_prefix}.planned.tsv`
2. Appends to `${base_prefix}.succeeded.tsv` (if not already present)
3. Removes from `${base_prefix}.pending.tsv`

**Examples:**
```bash
# Mark video as successfully downloaded
scripts/utilities/mark_success.sh dQw4w9WgXcQ logs/pull/20251122_120000Z
```

**Use Case:** Primarily called automatically by `clips.sh pull`. Manual use for fixing log inconsistencies.

**Dependencies:** bash, awk, grep

---

### 2. quality_report.py
**Purpose:** Generate quality metrics dashboard for transcription batches

**Location:** `scripts/utilities/quality_report.py`

**Description:**
Analyzes all `.words.tsv` files to provide comprehensive quality insights including confidence score distributions, retry statistics, per-file metrics, and identifies problematic files for manual review.

**Usage:**
```bash
python3 scripts/utilities/quality_report.py [directory]
```

**Arguments:**
- `directory` - Path to search for `.words.tsv` files (default: `generated/`)

**Metrics Reported:**
- Confidence score distribution (histogram)
- Average/min/max confidence per file
- Total words and segments
- Retry statistics (segments re-transcribed)
- Low confidence word counts (< -1.0, < -1.5)
- Problem file identification

**Examples:**
```bash
# Analyze all transcripts in generated/
python3 scripts/utilities/quality_report.py

# Analyze specific directory
python3 scripts/utilities/quality_report.py /path/to/transcripts/

# Save report to file
python3 scripts/utilities/quality_report.py generated/ > quality_dashboard.txt
```

**Output Example:**
```
Confidence Distribution:
  -2.00 to -1.80: ████ 42
  -1.80 to -1.60: ████████ 89
  ...

Top 10 Problem Files:
  video1.words.tsv - Avg: -1.82, Low conf: 234 words
  video2.words.tsv - Avg: -1.67, Low conf: 189 words
```

**Use Case:**
- Post-transcription quality assessment
- Identify videos needing retry
- Monitor transcription pipeline health

**Dependencies:** Python 3 standard library only

---

### 3. repair_archive.sh
**Purpose:** Identify and clean metadata-only entries from download archives

**Location:** `scripts/utilities/repair_archive.sh`

**Description:**
Comprehensive archive maintenance tool that identifies videos where only `.info.json` exists (no actual media file) and cleans the download archive. Helps recover from failed/interrupted downloads and maintains archive integrity.

**Usage:**
```bash
scripts/utilities/repair_archive.sh [OPTIONS]
```

**Options:**
```
--archive <file>        Main archive file (default: .clips-download-archive.txt)
--unfinished <file>     Unfinished archive (default: .clips-download-archive-from-unfinished.txt)
--pull-dir <dir>        Directory with pulled files (default: pull)
--apply                 Apply fixes in-place (default: writes .cleaned files)
--report                Print detailed "why" breakdown from .info.json
--regen-provenance      Generate missing .src.json for existing media
--emit-lists            Write URL/ID lists for re-download
--out-prefix <name>     Prefix for emitted lists (default: info_only)
-h, --help              Show help
```

**Features:**
- Identifies info-only entries (metadata without media)
- Generates cleaned archives (safe by default)
- Creates re-download lists
- Regenerates provenance files
- Detailed reporting with availability/live_status analysis

**Examples:**
```bash
# Dry run - create .cleaned files without modifying originals
scripts/utilities/repair_archive.sh --report

# Apply fixes in-place (destructive - use with caution)
scripts/utilities/repair_archive.sh --apply

# Generate lists for re-downloading info-only videos
scripts/utilities/repair_archive.sh --emit-lists --out-prefix failed_media

# Full repair with provenance regeneration
scripts/utilities/repair_archive.sh --apply --regen-provenance --report
```

**Output:**
- `${archive}.cleaned` - Cleaned archive files
- `${prefix}_urls.txt` - URLs to re-download
- `${prefix}_ids.txt` - Video IDs missing media

**Use Case:**
- After interrupted download batches
- Archive maintenance/cleanup
- Recovering from storage issues
- Pre-processing before re-download attempts

**Dependencies:** bash, grep, comm, awk

---

### 4. sort_clips.py
**Purpose:** Sort video clips by date extracted from filename and timestamp

**Location:** `scripts/utilities/sort_clips.py`

**Description:**
Specialized sorting utility for video clips with embedded date/timestamp information in filenames. Reads file paths from stdin, extracts dates ("Month Day, Year") and timestamps (NNNN.NN-NNNN.NN.mp4), and outputs sorted paths.

**Usage:**
```bash
find /path/to/clips -name "*.mp4" | python3 scripts/utilities/sort_clips.py
```

**Input:** File paths via stdin (one per line)

**Expected Filename Format:**
```
..._Month Day, Year_..._NNNN.NN-NNNN.NN.mp4
```
Example: `January 15, 2024_1234.56-1456.78.mp4`

**Sorting Order:**
1. By date (YYYY-MM-DD)
2. By start timestamp
3. Files without date/timestamp go last

**Examples:**
```bash
# Sort clips in directory
find media/clips/raw -name "*.mp4" | python3 scripts/utilities/sort_clips.py

# Sort and create concat list
find media/clips/filtered -name "*.mp4" | \
  python3 scripts/utilities/sort_clips.py > /tmp/sorted_clips.txt

# Used by stitch scripts internally
```

**Output:** Sorted file paths (one per line)

**Use Case:**
- Called internally by `stitch_videos_*.sh` scripts
- Manual clip list sorting before concatenation
- Date-based clip organization

**Dependencies:** Python 3 standard library only

---

## VIDEO PROCESSING - ALTERNATIVE METHODS

### 5. concat_filter_from_list.sh
**Purpose:** Concatenate videos using ffmpeg concat filter from a file list

**Location:** `scripts/video_processing/concat_filter_from_list.sh`

**Description:**
Low-level concat utility that reads file paths from a list file and concatenates using ffmpeg's complex filter. Provides fine control over frame rate and audio resampling via environment variables.

**Usage:**
```bash
scripts/video_processing/concat_filter_from_list.sh <list_file> <output.mp4>
```

**Arguments:**
- `list_file` - Text file with absolute paths (one per line)
- `output.mp4` - Output video file

**Environment Variables:**
```bash
FPS_EXPR="fps=60"                          # Frame rate filter expression
ARESAMPLE_EXPR="aresample=async=1:first_pts=0"  # Audio resample expression
VSYNC_ARGS="-vsync cfr -r 60"              # vsync arguments
```

**Examples:**
```bash
# Basic usage
echo "/path/to/video1.mp4" > list.txt
echo "/path/to/video2.mp4" >> list.txt
scripts/video_processing/concat_filter_from_list.sh list.txt output.mp4

# Custom frame rate
FPS_EXPR="fps=30" scripts/video_processing/concat_filter_from_list.sh list.txt output.mp4

# Variable frame rate
VSYNC_ARGS="-vsync vfr" scripts/video_processing/concat_filter_from_list.sh list.txt output.mp4
```

**Output:** Single concatenated MP4 with:
- Video: H.264, CRF 18, veryfast preset
- Audio: AAC, 192k
- Frame rate: 60fps CFR (default, configurable)

**Use Case:**
- Low-level concatenation with custom settings
- Called by `stitch_videos_batched_filter.sh`
- When you need precise control over ffmpeg filters

**Dependencies:** ffmpeg

---

### 6. stitch_videos_batched_filter.sh
**Purpose:** Concatenate large numbers of videos in batches using concat filter

**Location:** `scripts/video_processing/stitch_videos_batched_filter.sh`

**Description:**
Advanced batched concatenation using ffmpeg's concat filter per batch, then merging batches. Most reliable for large clip counts with varying formats. Prevents memory issues by processing in configurable batch sizes.

**Usage:**
```bash
scripts/video_processing/stitch_videos_batched_filter.sh <input_dir> <output.mp4> [sort_method]
```

**Arguments:**
- `input_dir` - Directory containing MP4 files
- `output.mp4` - Output filename
- `sort_method` - One of: name, time, timestamp, date_timestamp (default: date_timestamp)

**Features:**
- Interactive confirmation with file preview
- Single ffmpeg command for all files
- Ensures consistent encoding across all clips
- Most reliable for mixed-format sources

**Examples:**
```bash
# Interactive mode (shows first/last 5 files, asks to continue)
scripts/video_processing/stitch_videos_concat_filter.sh media/clips/selected output.mp4

# Sort by filename
scripts/video_processing/stitch_videos_concat_filter.sh clips/ out.mp4 name

# Sort by file timestamp
scripts/video_processing/stitch_videos_concat_filter.sh clips/ out.mp4 time
```

**Interactive Prompts:**
```
==> First 5 files:
/path/to/file1.mp4
/path/to/file2.mp4
...
==> Last 5 files:
...
==> Continue with concatenation? [y/N]
```

**Limitations:**
- Memory usage scales with file count
- For >200 files, use batched version
- Requires user confirmation (not scriptable)

**Use Case:**
- Small to medium clip counts (<100 files)
- When you want maximum control
- Interactive verification before processing

**Dependencies:** ffmpeg, python3 (for date_timestamp sorting)

---

## TRANSCRIPTION - DIAGNOSTIC TOOLS

### 7. detect_dupe_hallu.py
**Purpose:** Detect hallucinated (duplicated) phrases in transcripts and generate retry manifests

**Location:** `scripts/transcription/detect_dupe_hallu.py`

**Description:**
Advanced quality control tool that scans `.words.tsv` files for repetitive patterns indicating Whisper hallucinations (e.g., "He needs this." repeated 20 times). Generates retry manifests for re-transcription of problematic segments.

**Usage:**
```bash
python3 scripts/transcription/detect_dupe_hallu.py [OPTIONS] MANIFEST.tsv [...]
```

**Options:**
```
--project-root PATH            Project root (default: $PROJECT_ROOT or ".")
--output PATH                  Output manifest path (default: <dir>/dupe_hallu.retry_manifest.tsv)
--ngram-thresholds SPEC        Detection rules (default: "1:10,2:4,3:4")
                               Format: "length:count,length:count,..."
                               1:10 = 1-word phrase repeated >=10 times
                               2:4  = 2-word phrase repeated >=4 times
--max-window SECONDS           Time window for repetition (default: 20.0)
--verbose                      Extra logging
```

**N-gram Threshold Examples:**
```bash
# Default: single words >=10 repeats, 2+ word phrases >=4 repeats
--ngram-thresholds "1:10,2:4,3:4"

# Stricter: detect shorter repetitions
--ngram-thresholds "1:5,2:3,3:3"

# Focus only on multi-word hallucinations
--ngram-thresholds "2:3,3:3,4:2"
```

**Examples:**
```bash
# Scan all retry manifests for hallucinations
python3 scripts/transcription/detect_dupe_hallu.py generated/*.retry_manifest.tsv

# Custom output location
python3 scripts/transcription/detect_dupe_hallu.py \
  --output results/hallucinations.tsv \
  generated/*.retry_manifest.tsv

# Verbose mode with custom thresholds
python3 scripts/transcription/detect_dupe_hallu.py \
  --verbose \
  --ngram-thresholds "1:8,2:3,3:3" \
  --max-window 15.0 \
  generated/*.retry_manifest.tsv

# Use with batch retry workflow
python3 scripts/transcription/detect_dupe_hallu.py generated/*.retry_manifest.tsv
ws transcribe --batch-retry  # Will include detected hallucinations
```

**Output Manifest Format:**
```
media_file    segment_idx    start_time    end_time    confidence    zero_length    text
```

**Detection Logic:**
1. Scans for repeated n-grams within time window
2. Applies threshold rules based on phrase length
3. Identifies segments containing hallucinations
4. Generates retry manifest for re-transcription

**Use Case:**
- Post-transcription quality control
- Before database import
- Automated hallucination detection in batch processing
- Improving transcript accuracy

**Dependencies:** Python 3 standard library only

---

### 8. watch_cuda_error.sh
**Purpose:** Monitor log files for CUDA errors and send alerts

**Location:** `scripts/transcription/watch_cuda_error.sh`

---

### 9. map_ids_to_files.py
**Purpose:** Map YouTube video IDs to local media files

**Location:** `scripts/utilities/map_ids_to_files.py`

**Description:**
Reads IDs from `logs/no_transcripts_available.txt`, scans `pull/*.opus` for files whose names start with `VIDEO_ID__`, and writes the matched file paths to `transcripts_needed.txt`.

**Usage:**
```bash
python3 scripts/utilities/map_ids_to_files.py
```

**Defaults:**
- ID source: `logs/no_transcripts_available.txt`
- Media dir: `pull/` (scans `*.opus`)
- Output: `transcripts_needed.txt`

**Use Case:**
- Build a file list for follow-up processing (e.g., manual transcription) from a list of known IDs lacking transcripts.
**Purpose:** Monitor log files for CUDA errors and send alerts
