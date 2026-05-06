# Note to AI Assistants (Claude, etc.)

This repository is a unified video‑processing workspace driven by `workspace.sh`. It orchestrates downloading, clipping, transcription, analysis, voice filtering, and stitching via scripts in `scripts/`.

Start here:
- Read `AGENTS.md` for a concise contributor guide: structure, key commands, coding standards, init safety, and related docs.
- Run `./workspace.sh help` and `./workspace.sh help <command>` to explore capabilities and examples.

Guidance for agents:
- Prefer the `workspace.sh` entrypoint; only call deep scripts directly when necessary.
- Use relative paths (tool is designed to run remotely; current directory is the project root).
- Store AI work summaries under `logs/changelog/` with timestamped names.

For deeper architecture/mappings see `AGENTS_DRAFT.md`. For standalone utilities, consult `EXTRA_UTILS.md`.
**Variants**:
- `filter_voice.py` - Simple single-threaded
- `filter_voice_parallel.py` - Parallelized
- `filter_voice_parallel_chunked.py` - Chunked (most reliable)
- `filter_voice_batched.py` - Batch processing

**Dependencies**:
- PyTorch + torchaudio (GPU acceleration)
- Resemblyzer (voice embeddings)
- CUDA for GPU processing

**Output**:
```json
{
  "results": [
    {
      "clip_path": "/path/to/clip.mp4",
      "similarity": 0.72,
      "is_match": true
    }
  ]
}
```

### 6. Video Processing

**Main Scripts**:
- `stitch_videos_batched.sh` - Batch concatenation (most reliable)
- `stitch_videos_cfr.sh` - Constant frame rate (fast)
- `stitch_videos.sh` - Simple re-encode

**Sort Methods**:
- `date_timestamp` - By date then timestamp (recommended)
- `timestamp` - By timestamp in filename
- `name` - Alphabetically
- `time` - By file modification time

**Batched Method**:
- Concatenates in groups of 100 (configurable via `BATCH_SIZE`)
- Re-encodes each batch to consistent format
- Merges all batches
- Most compatible, handles mixed codecs

**Helper**: `scripts/utilities/sort_clips.py` - Extracts date/timestamp from filenames for sorting

### 7. Date Management

**Purpose**: Track and fill gaps in video archives

**Scripts**:
- `find_missing_dates.py` - Compare date list vs downloaded files
- `create_download_list.py` - Generate URLs for missing dates
- `extract_and_compare_dates.py` - Date comparison reports
- `move_files_by_date.py` - Organize files by date

**Workflow**:
1. Have: `dates_to_pull.txt` (desired dates)
2. Run: `find_missing_dates.py` → outputs missing dates
3. Run: `create_download_list.py` with archive metadata
4. Download: Use `workspace.sh download --from-list`

### 8. GPU Management

**Scripts**:
- `scripts/gpu_tools/gpu-to-nvidia.sh` - Bind GPU to NVIDIA drivers
- `scripts/gpu_tools/gpu-bind-status.sh` - Check current bindings

**Use Cases**:
- **to-nvidia**: For transcoding, voice filtering (CUDA), host graphics
- **to-vfio**: For VM passthrough (requires sudo)

---

## Common Workflows

### Complete Clip Creation Pipeline

```bash
# 1. Set up project
mkdir ~/my-clips-project
cd ~/my-clips-project
~/tools/vidops/workspace.sh info

# 2. Download videos from missing dates
~/tools/vidops/workspace.sh download --from-list data/download_list.txt

# 3. Transcribe (dual-GPU)
~/tools/vidops/workspace.sh transcribe

# 4. Search transcripts
~/tools/vidops/workspace.sh hits "interesting,topic,phrases" > results/wanted.tsv

# 5. Cut clips locally
~/tools/vidops/workspace.sh clips cut-local results/wanted.tsv media/clips/raw

# 6. Filter by voice
~/tools/vidops/workspace.sh voice filter-chunked \
  --clips-dir media/clips/raw \
  --reference-dir data/reference_clips \
  --output results/voice_filtered/results.json

# 7. Extract matching clips
~/tools/vidops/workspace.sh voice extract

# 8. Move to final location
mkdir -p media/clips/confirmed
while IFS= read -r clip; do
    mv "$clip" media/clips/confirmed/
done < results/voice_filtered/matched_clips.txt

# 9. Stitch into compilation
~/tools/vidops/workspace.sh stitch batched \
  media/clips/confirmed/ \
  media/final/compilation_$(date +%Y%m%d).mp4 \
  date_timestamp
```

### Database Analytics Workflow

```bash
cd ~/video-archive-project

# 1. Import all metadata
~/tools/vidops/workspace.sh dbupdate transcripts

# 2. Query in PostgreSQL
psql -d transcripts -c "
  SELECT v.title, COUNT(w.word) as word_count
  FROM videos v
  JOIN words w ON v.ytid = w.ytid
  WHERE v.upload_date >= '2024-01-01'
  GROUP BY v.ytid, v.title
  ORDER BY word_count DESC
  LIMIT 10;
"

# 3. Find videos containing specific phrases
psql -d transcripts -c "
  SELECT DISTINCT v.title, v.url, w.start_sec
  FROM videos v
  JOIN words w ON v.ytid = w.ytid
  WHERE w.word = 'important'
  ORDER BY v.upload_date DESC;
"
```

### Transcription Quality Check

```bash
cd ~/transcription-project

# 1. Transcribe batch
MODEL=medium ~/tools/vidops/workspace.sh transcribe

# 2. Generate quality report
python3 ~/tools/vidops/scripts/utilities/quality_report.py

# 3. Review low-confidence segments
grep "confidence.*-[2-9]" generated/*.words.tsv

# 4. Retry low-confidence segments
bash ~/tools/vidops/scripts/transcription/batch_retry.sh
```

---

## Database Schema

**Tables** (inferred from SQL files):

### videos
```sql
- ytid (TEXT, PK) - YouTube video ID
- url (TEXT) - Full video URL
- title (TEXT) - Video title
- upload_date (DATE) - Upload date
- duration_sec (INTEGER) - Duration in seconds
- channel (TEXT) - Channel name
- channel_id (TEXT) - Channel ID
- extractor_key (TEXT) - yt-dlp extractor
- tags (JSONB) - Video tags
- categories (JSONB) - Video categories
- upload_type (TEXT) - vod/stream/clip (manual annotation)
- title_date (DATE) - Date parsed from title
```

### transcripts
```sql
- ytid (TEXT, FK → videos)
- kind (TEXT) - vtt/words_ytt/words_whisper
- lang (TEXT) - Language code
- path (TEXT) - File path
- word_count (INTEGER)
- segment_count (INTEGER)
- UNIQUE(ytid, kind, lang)
```

### assets
```sql
- path (TEXT, PK) - File path
- ytid (TEXT, FK → videos)
- kind (TEXT) - Asset type
- bytes (BIGINT) - File size
```

### words
```sql
- ytid (TEXT, FK → videos)
- source (TEXT) - yt/whisper
- idx (INTEGER) - Word index
- word (TEXT) - Lowercase word
- start_sec (NUMERIC(10,3)) - Start timestamp
- end_sec (NUMERIC(10,3)) - End timestamp
- confidence (REAL) - Log probability
- segment_id (INTEGER) - Segment number
- UNIQUE(ytid, source, idx)
```

### hits
```sql
- ytid (TEXT, FK → videos)
- label (TEXT) - Search term/label
- start_sec (NUMERIC) - Start timestamp
- end_sec (NUMERIC) - End timestamp
- duration_sec (NUMERIC) - Clip duration
- source_caption (TEXT) - Source filename
```

---

## Configuration & Environment

### Environment Variables

**Global**:
- `TOOL_ROOT` - Installation directory (auto-detected)
- `PROJECT_ROOT` - Current project directory (auto-detected from pwd)
- `DRY_RUN` - Test mode (0 or 1)

**Transcription**:
- `MODEL` - Whisper model (tiny/base/small/medium/large)
- `LANGUAGE` - Language code (default: en)
- `FORCE` - Force re-transcription (0 or 1)
- `OUTFMT` - Output format (vtt/srt/both)
- `NV_COMPUTE` - NVIDIA precision (float16/int8_float16/int8)
- `NV_VAD_FILTER` - Enable VAD (0 or 1)
- `ENABLE_CPU` - Third CPU worker (0 or 1)
- `HOTWORDS_FILE` - Hotwords list path
- `CORRECTIONS_TSV` - Corrections file path

**Clips**:
- `CLIP_CONTAINER` - Output format (opus/mp3/mka/mp4)
- `CLIP_AUDIO_BR` - Audio bitrate (default: 96k)
- `PAD_START` - Pre-padding seconds (default: 0)
- `PAD_END` - Post-padding seconds (default: 0)
- `CLIPS_OUT` - Output directory
- `CLIP_ARCHIVE_FILE` - yt-dlp archive file

**yt-dlp Pacing**:
- `YT_SLEEP_REQUESTS` - Delay between requests
- `YT_SLEEP_INTERVAL` - Sleep interval
- `YT_MAX_SLEEP_INTERVAL` - Max sleep
- `YT_RETRIES` - Retry attempts

### Configuration Files

**Tool-level**:
- `config/clips.env` - Default clips settings

**Project-level**:
- `config/` - Project-specific overrides
- `data/hotwords.txt` - Domain vocabulary
- `data/.clips-download-archive.txt` - yt-dlp archive

---

## Script Categories

### Date Management (4 scripts)
- `find_missing_dates.py` - Identify date gaps
- `create_download_list.py` - Generate download URLs
- `extract_and_compare_dates.py` - Date analysis
- `move_files_by_date.py` - Date-based organization

### Database (10 scripts + 6 SQL)
**Python**:
- `export_videos_from_info.py` - Parse info.json
- `export_transcripts_from_lists_fast.py` - Scan transcripts
- `export_transcripts_and_words_from_lists.py` - Export words
- `export_transcripts_and_words.py` - Alternative exporter

**Shell**:
- `import_videos.sh` - Full import pipeline
- `annotate_upload_type.sh` - Interactive classification
- `load_hits.sh` - Load search results

**SQL**:
- `load_videos_from_info.sql` - Import videos
- `load_transcripts.sql` - Import transcripts
- `load_words.sql` - Import per-word data
- `derive_title_dates.sql` - Parse title dates
- `set_upload_type.sql` - Update classifications
- `load_title_dates.sql` - Import date metadata

### GPU Tools (2 scripts)
- `gpu-to-nvidia.sh` - Bind to NVIDIA drivers (requires sudo)
- `gpu-bind-status.sh` - Check GPU bindings

### Transcription (5 scripts)
- `dual_gpu_transcribe.sh` - Main transcription engine
- `batch_retry.sh` - Retry orchestrator
- `batch_retry_worker.py` - Retry worker process
- `detect_dupe_hallu.py` - Hallucination detection
- `watch_cuda_error.sh` - Monitor CUDA errors

### Utilities (7 scripts + templates)
- `clips.sh` - Clip workflow orchestrator
- `convert-captions.sh` - VTT to words.tsv conversion
- `find_word_hits.py` - Search for specific words
- `quality_report.py` - Transcription quality analysis
- `mark_success.sh` - Success marker utility
- `repair_archive.sh` - Fix yt-dlp archive
- `sort_clips.py` - Sort by date/timestamp

**Clips Templates** (7 files in `clips_templates/`):
- `common.sh` - Shared functions
- `pull.sh` - Download logic
- `hits.sh` - Search engine
- `cut_local.sh` - Local extraction
- `cut_net.sh` - Network extraction
- `refine.sh` - Clip refinement
- `transcripts.sh` - Subtitle download

### Video Processing (6 scripts)
- `stitch_videos_batched.sh` - Batch concatenation (recommended)
- `stitch_videos_cfr.sh` - Constant frame rate
- `stitch_videos.sh` - Simple re-encode
- `stitch_videos_batched_filter.sh` - Batched with filter
- `stitch_videos_concat_filter.sh` - Concat filter method
- `concat_filter_from_list.sh` - Concat from file list

### Voice Filtering (4 scripts)
- `filter_voice.py` - Simple single-threaded
- `filter_voice_parallel.py` - Parallelized
- `filter_voice_parallel_chunked.py` - Chunked (recommended)
- `filter_voice_batched.py` - Batch processing

---

## Dependencies

### Required
- **bash** - Shell scripting
- **python3** - Data processing (3.8+)
- **ffmpeg** - Video/audio processing
- **yt-dlp** - Video downloading

### Optional
- **faster-whisper** - GPU transcription (recommended)
  - Requires: CUDA, CTranslate2
- **OpenAI whisper** - CPU/AMD transcription fallback
  - Requires: PyTorch, torchaudio
- **PostgreSQL** - Database analytics (optional)
- **Resemblyzer** - Voice filtering
  - Requires: PyTorch, torchaudio

### Python Packages
**Core**:
- Standard library: json, csv, pathlib, os, sys, re, datetime, subprocess

**Transcription**:
- faster-whisper (NVIDIA GPUs)
- openai-whisper (CPU/AMD)
- torch, torchaudio

**Voice Filtering**:
- resemblyzer
- torch, torchaudio
- numpy

**Database**:
- psycopg2 (PostgreSQL client, if using DB features)

### System Requirements
**Transcription**:
- NVIDIA GPU with CUDA support (for faster-whisper)
- OR AMD GPU with ROCm (for OpenAI whisper)
- OR CPU with 8+ cores

**Voice Filtering**:
- GPU recommended (CUDA or ROCm)
- Significant RAM (embeddings in memory)

---

## Important Patterns

### Path References in Scripts

**Bash**:
```bash
# At script start
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_ROOT="${TOOL_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"

export TOOL_ROOT
export PROJECT_ROOT

# For data files
INPUT="${PROJECT_ROOT}/pull/"
OUTPUT="${PROJECT_ROOT}/generated/"

# For other scripts
python3 "${TOOL_ROOT}/scripts/utilities/helper.py"
```

**Python**:
```python
import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "."))
TOOL_ROOT = Path(os.environ.get("TOOL_ROOT", Path(__file__).parent.parent))

# For data files
input_dir = PROJECT_ROOT / "pull"
output_dir = PROJECT_ROOT / "generated"

# For other scripts
helper_script = TOOL_ROOT / "scripts" / "utilities" / "helper.py"
```

### Wrapper Pattern

Wrappers in `wrappers/` simply forward to real scripts:
```bash
#!/usr/bin/env bash
WRAPPER_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_ROOT="$(cd "$WRAPPER_DIR/.." && pwd)"
exec python3 "${TOOL_ROOT}/scripts/voice_filtering/filter_voice_parallel_chunked.py" "$@"
```

### Project Initialization

Scripts should check/create project structure:
```bash
if [[ ! -f "$PROJECT_ROOT/.vidops-project" ]]; then
    echo "Initializing VidOps project in: $PROJECT_ROOT"
    touch "$PROJECT_ROOT/.vidops-project"
    mkdir -p "$PROJECT_ROOT"/{pull,generated,logs/pull,logs/db,data,results,cuts,media/{clips,final}}
fi
```

### File Naming Conventions

**Downloaded Videos**:
```
{video_id}__{upload_date} - {title}.{ext}
Example: Bldyb2JvaF0__2019-05-21 - Video Title Here.mp4
```

**Metadata Files**:
```
{video_id}__{upload_date} - {title}.info.json  # yt-dlp metadata
{video_id}__{upload_date} - {title}.src.json   # Provenance tracking
```

**Transcripts**:
```
{video_id}__{upload_date} - {title}.vtt         # WebVTT
{video_id}__{upload_date} - {title}.srt         # SubRip
{video_id}__{upload_date} - {title}.words.tsv   # Per-word data
```

**Clips**:
```
{date}_{title_snippet}_{start}-{end}.mp4
Example: 2024-01-15_Video_Title_123.45-145.67.mp4
```

### TSV Formats

**words.tsv**:
```tsv
start	end	word	confidence	seg	retried
0.000	0.480	hello	-0.234	1	0
0.520	0.880	world	-0.156	1	0
```

**hits.tsv** (search results):
```tsv
url	start	end	label	source_caption
https://youtube.com/watch?v=ID	123.45	145.67	search_term	video_file.mp4
```

---

## Troubleshooting Guide

### Common Issues

**"Not in a VidOps project directory"**:
```bash
# Solution: Initialize or cd to project
touch .vidops-project
# OR
cd /path/to/existing/project
```

**"Command not found: workspace.sh"**:
```bash
# Use absolute path
~/tools/vidops/workspace.sh info

# OR create alias
alias vidops="~/tools/vidops/workspace.sh"
```

**Transcription fails with CUDA errors**:
```bash
# Check GPU binding
~/tools/vidops/workspace.sh gpu status

# Bind to NVIDIA if needed
sudo ~/tools/vidops/workspace.sh gpu to-nvidia

# Test with smaller model
MODEL=tiny ~/tools/vidops/workspace.sh transcribe
```

**Files appearing in wrong directory**:
```bash
# Check current directory
pwd

# Verify PROJECT_ROOT
echo $PROJECT_ROOT

# Ensure .vidops-project exists
ls -la .vidops-project
```

**Database import fails**:
```bash
# Check PostgreSQL is running
psql -d transcripts -c "SELECT 1;"

# Verify intermediate files
ls -lh logs/db/

# Check for malformed data
head logs/db/videos_from_info.tsv
```

**Voice filtering out of memory**:
```bash
# Use chunked method with smaller chunks
~/tools/vidops/workspace.sh voice filter-chunked \
  --chunk-size 50 \  # Reduce from default 200
  --clips-dir media/clips/raw \
  --reference-dir data/reference_clips
```

### Debug Tips

**Enable dry-run mode**:
```bash
DRY_RUN=1 ~/tools/vidops/workspace.sh download "URL"
```

**Verbose logging**:
```bash
# Most scripts support -v or check logs/
tail -f logs/pull/*.log
tail -f logs/workspace_commands.log
```

**Check script execution**:
```bash
# For bash scripts
bash -x ~/tools/vidops/workspace.sh info

# For Python scripts
python3 -u ~/tools/vidops/scripts/utilities/quality_report.py
```

**Validate project structure**:
```bash
~/tools/vidops/workspace.sh info
# Shows all directories and their status
```

---

## Quick Reference

### Most Common Commands

```bash
# Project setup
mkdir ~/my-project && cd ~/my-project
~/tools/vidops/workspace.sh info

# Download
~/tools/vidops/workspace.sh download "URL"
~/tools/vidops/workspace.sh download --from-list data/urls.txt

# Transcribe
~/tools/vidops/workspace.sh transcribe
MODEL=medium ~/tools/vidops/workspace.sh transcribe

# Search
~/tools/vidops/workspace.sh hits "search term" > results.tsv

# Voice filter
~/tools/vidops/workspace.sh voice filter-chunked \
  --clips-dir media/clips/raw \
  --reference-dir data/reference_clips

# Stitch
~/tools/vidops/workspace.sh stitch batched \
  media/clips/input/ media/final/output.mp4 date_timestamp

# Database
~/tools/vidops/workspace.sh dbupdate transcripts

# GPU management
~/tools/vidops/workspace.sh gpu status
sudo ~/tools/vidops/workspace.sh gpu to-nvidia
```

### Key File Locations

```bash
# Tool code
/home/billie/tools/vidops/

# Project data
./{pull,generated,logs,data,results,cuts,media}/

# Logs
./logs/workspace_commands.log
./logs/pull/*.log
./logs/db/*.tsv

# Quality reports
python3 ~/tools/vidops/scripts/utilities/quality_report.py
```

---

## For AI Assistants: Working with This Codebase

### When Modifying Scripts

1. **Never break tool/project separation**: Use `$PROJECT_ROOT` for data, `$TOOL_ROOT` for scripts
2. **Preserve backward compatibility**: Old scripts should keep working
3. **Update both bash and Python** patterns if adding new features
4. **Test with multiple projects** to ensure independence
5. **Document environment variables** in script headers

### When Adding New Features

1. Place in appropriate `scripts/` subdirectory
2. Follow existing naming conventions
3. Use common.sh helpers for bash scripts
4. Export TOOL_ROOT and PROJECT_ROOT at script start
5. Add to workspace.sh if it's a major feature
6. Create wrapper in `wrappers/` if needed
7. Update this CLAUDE.md file

### When Helping Users

1. **Always ask about project context**: Which directory are they in?
2. **Check .vidops-project marker** exists
3. **Use absolute paths** for tool commands
4. **Verify environment variables** are set correctly
5. **Point to existing docs**: README.md, START_HERE.md, QUICK_REFERENCE.md
6. **Suggest workspace.sh** over individual scripts

### Code Quality Standards

- **Bash**: Use `set -euo pipefail`, quote variables, use `${VAR}` syntax
- **Python**: Use pathlib, type hints where helpful, handle encoding='utf-8'
- **Error handling**: Check for missing files, invalid input, provide clear errors
- **Logging**: Use consistent timestamp formats, log to appropriate `logs/` subdirectory
- **Comments**: Explain *why*, not *what* (code shows what)

---

**Last Updated**: 2025-11-21 by Claude Code  
**Maintainer**: billie  
**Repository**: /home/billie/tools/vidops  
**Documentation**: README.md, DB_README.md, AGENTS.md, EXTRA_UTILS.md
