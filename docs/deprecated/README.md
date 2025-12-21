# VidOps - Video Processing Toolkit

A comprehensive video processing toolkit for downloading, transcribing, searching, and editing video content. Designed for working with YouTube videos and streams at scale.

## 🎯 Quick Start

```bash
# Create a new project
mkdir ~/my-video-project
cd ~/my-video-project

# Initialize (creates directory structure)
~/tools/vidops/workspace.sh info

# Download a video
~/tools/vidops/workspace.sh download "https://www.youtube.com/watch?v=..."

# Transcribe it
~/tools/vidops/workspace.sh transcribe

# Search transcripts
~/tools/vidops/workspace.sh hits "search term"
```

## 📁 Architecture

VidOps uses a **two-directory architecture**:

- **TOOL_ROOT** (`~/tools/vidops`): Code and scripts (read-only)
- **PROJECT_ROOT** (any directory): Your project data (pull/, generated/, logs/, etc.)

This allows you to:
- ✅ Install the tool once
- ✅ Create multiple independent projects
- ✅ Keep project data separate from code
- ✅ Run commands from any project directory

## 🛠️ Features

### Video Download
- YouTube video/audio download via yt-dlp
- Playlist support
- Archive tracking to prevent re-downloads
- Automatic retry on failure

### Transcription
- GPU-accelerated transcription with faster-whisper
- Multiple model sizes (tiny, base, small, medium, large)
- Dual-GPU support for parallel processing
- Per-word timestamps and confidence scores
- VTT, SRT, and TSV output formats
- Low-confidence segment retry system
- Default chunked mode to handle very long files (1h chunks with 5s overlap); disable with `--no-fragment`

### Search & Analysis
- Full-text search across all transcripts
- Keyword matching with timestamps
- Date-based filtering and comparison
- Quality reporting and metrics
- Database import for SQL queries

### Video Processing
- Segment extraction by timestamp
- Batch concatenation and stitching
- Constant frame rate (CFR) encoding
- Multiple sorting methods (date, timestamp, filename)

## 📚 Documentation

- See `AGENTS.md` (contributors), `EXTRA_UTILS.md` (standalone tools), and `DB_README.md` (database pipeline)
- **REFACTORING_COMPLETE.txt** - Architecture details and full feature list
- **REFACTORING_INSTRUCTIONS_FOR_AI.txt** - Detailed implementation guide
- **docs/SMOKE_TESTS.md** - Reproducible smoke-test suite and troubleshooting tips
- **docs/REFACTOR_ARCHITECTURE/STORAGE_BROKER_HTTPS_HOWTO.md** - Deploy the Storage Broker over internal HTTPS (nginx, TLS, tokens, health)

## 🚀 Usage Examples

### Download and Transcribe
```bash
cd ~/my-project
~/tools/vidops/workspace.sh download "https://youtube.com/watch?v=dQw4w9WgXcQ"
~/tools/vidops/workspace.sh transcribe
```

### Search for Keywords
```bash
~/tools/vidops/workspace.sh hits "important topic" > results.tsv
```

### Database Import
```bash
~/tools/vidops/workspace.sh db import
# Follow prompts to import videos, transcripts, and words
```

### Find Missing Dates
```bash
python3 ~/tools/vidops/scripts/date_management/find_missing_dates.py \
  date-list.txt ./pull
```

### Quality Report
```bash
python3 ~/tools/vidops/scripts/utilities/quality_report.py
```

### Map IDs to Local Files
```bash
# Build a list of local media files for IDs lacking transcripts
python3 ~/tools/vidops/scripts/utilities/map_ids_to_files.py

# Defaults:
#   Reads IDs from: logs/no_transcripts_available.txt
#   Scans media in: pull/*.opus
#   Writes list to: transcripts_needed.txt

# Or via the workspace wrapper
./workspace.sh extra-utils map_ids_to_files.py
```

### Reference-Guided Diarization
```bash
~/tools/vidops/workspace.sh diarize --ytids-file ytids.txt --workers 3 --device cuda --verbose
```
Uses Resemblyzer to assign speakers; will prompt to build reference clips under `generated/diary_reference/<ytid>/` if none are present.
You can also derive IDs from a folder of media files:
```bash
~/tools/vidops/workspace.sh diarize --ytids-from-dir pull/ --workers 3 --device cuda --verbose
```
Pull words straight from the transcripts database and write spans back:
```bash
~/tools/vidops/workspace.sh diarize --ytids-file ytids.txt --use-db-words --db-host 192.168.0.187 --db-name transcripts --write-db --verbose
```
Local TSV/JSON outputs still write, and when pulling words from the DB they land under `generated/from-db/diarization_resemblyzer/<ytid>/`; DB insert only runs when `--use-db-words` is enabled.
You can set defaults in `db.cfg` (db_host, db_port, db_name, db_user, db_password, db_path_prefix) at the repo root; `db_path_prefix` is prepended to transcript paths stored in the DB (e.g., `/mnt` for a remote mount).

### Video Stitching
```bash
bash ~/tools/vidops/scripts/video_processing/stitch_videos_batched.sh \
  ./cuts final-output.mp4
```

## 📂 Project Structure

Each project directory contains:

```
my-project/
├── .vidops-project        # Project marker file (legacy)
├── .vidops_deploy_marker  # Project marker file (current)
├── pull/                  # Downloaded videos
├── generated/             # Transcripts and words
├── logs/
│   ├── pull/             # Download logs
│   └── db/               # Database logs
├── data/                 # Analysis outputs
├── results/              # Comparison results
├── cuts/                 # Video segments
└── media/
    ├── clips/            # Raw clips
    └── final/            # Final videos
```

## 🔧 Configuration

Environment variables:

- `PROJECT_ROOT` - Project directory (auto-detected from pwd)
- `TOOL_ROOT` - Installation directory (auto-detected)
- `MODEL` - Whisper model size (default: small)
- `LANGUAGE` - Transcription language (default: en)
- `WORKERS` - Parallel transcription workers (default: 1)
- `DRY_RUN` - Test mode without making changes (0 or 1)

Example with custom settings:
```bash
MODEL=medium WORKERS=4 ~/tools/vidops/workspace.sh transcribe
```

## 🎨 Bash Aliases (Optional)

Add to `~/.bashrc` or `~/.zshrc`:

```bash
alias vidops="~/tools/vidops/workspace.sh"
alias vidops-dl="~/tools/vidops/workspace.sh download"
alias vidops-tr="~/tools/vidops/workspace.sh transcribe"
alias vidops-hits="~/tools/vidops/workspace.sh hits"
```

Then use shorter commands:
```bash
vidops download "URL"
vidops-tr
vidops-hits "search term"
```

## 🔍 Key Scripts

### Core Commands
- `workspace.sh` - Main CLI interface
- `clips.sh` - Legacy wrapper for clips operations

### Clips & Download
- `scripts/utilities/clips.sh` - Clips workflow scaffolding
- `scripts/utilities/clips_templates/pull.sh` - Video download logic
- `scripts/utilities/clips_templates/hits.sh` - Transcript search

### Transcription
- `scripts/transcription/dual_gpu_transcribe.sh` - Batch transcription
- `scripts/transcription/batch_retry.sh` - Retry low-confidence segments
- `scripts/transcription/batch_retry_worker.py` - Retry worker process

### Database
- `scripts/db/import_videos.sh` - Full database import pipeline
- `scripts/db/export_videos_from_info.py` - Extract video metadata
- `scripts/db/export_transcripts_from_lists_fast.py` - Build transcript manifest

### Date Management
- `scripts/date_management/find_missing_dates.py` - Find date gaps
- `scripts/date_management/create_download_list.py` - Generate download list
- `scripts/date_management/extract_and_compare_dates.py` - Date comparison

### Utilities
- `scripts/utilities/quality_report.py` - Transcription quality metrics
- `scripts/utilities/convert-captions.sh` - Convert YouTube captions

### Video Processing
- `scripts/video_processing/stitch_videos_batched.sh` - Batch concatenation
- `scripts/video_processing/stitch_videos_cfr.sh` - CFR concatenation
- `scripts/video_processing/stitch_videos.sh` - Basic concatenation

## 📊 Database Schema

VidOps can export to PostgreSQL with tables:

- `videos` - Video metadata (title, date, duration, url)
- `transcripts` - Full transcript text per video
- `assets` - Media file tracking
- `words` - Per-word timestamps and confidence scores
- `hits` - Search results and clips

## 🐛 Troubleshooting

**Commands not working?**
- Ensure you're in a project directory
- Check `~/tools/vidops` exists
- Make scripts executable: `chmod +x ~/tools/vidops/workspace.sh`

**Files in wrong location?**
- Check current directory with `pwd`
- Verify `.vidops-project` exists
- Check `echo $PROJECT_ROOT`

**Transcription failing?**
- Verify CUDA/GPU setup for faster-whisper
- Try `MODEL=tiny` for testing
- Check logs in `./logs/`

## 🔄 Migration from Old Setup

If you have existing projects:

1. Tool code already moved to `~/tools/vidops`
2. For each old project:
   ```bash
   cd /path/to/old/project
   touch .vidops-project
   ```
3. Your existing `pull/`, `generated/`, `logs/` directories work as-is!

## 🎓 How It Works

1. **Download**: Videos are saved to `./pull/` with metadata in `.info.json` files
2. **Transcribe**: Whisper processes audio and generates `.vtt`, `.srt`, and `.words.tsv` in `./generated/`
3. **Search**: Python scripts scan `.words.tsv` files for keywords and extract timestamps
4. **Extract**: FFmpeg cuts segments from videos based on timestamps
5. **Stitch**: Segments are concatenated into final videos
6. **Database**: All metadata can be imported into PostgreSQL for advanced queries

## 📦 Dependencies

- `bash` - Shell scripting
- `python3` - Data processing
- `ffmpeg` - Video/audio processing
- `yt-dlp` - Video downloading
- `faster-whisper` - GPU transcription (optional but recommended)
- `postgresql` - Database (optional)

## 🧪 Testing

Basic functionality test:
```bash
mkdir ~/test-vidops
cd ~/test-vidops
~/tools/vidops/workspace.sh info
~/tools/vidops/workspace.sh download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
~/tools/vidops/workspace.sh transcribe
```

Multiple projects test:
```bash
mkdir ~/project-a ~/project-b
cd ~/project-a && ~/tools/vidops/workspace.sh download "URL1"
cd ~/project-b && ~/tools/vidops/workspace.sh download "URL2"
# Each project has separate pull/ directories
```

## 📝 License

This project is a personal toolkit. Use at your own discretion.

## 🙏 Credits

Built with:
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Video downloading
- [faster-whisper](https://github.com/guillaumekln/faster-whisper) - Speech recognition
- [FFmpeg](https://ffmpeg.org/) - Media processing

---

**Version**: 2.0 (Refactored 2025-11-19)
**Installation**: `/home/billie/tools/vidops`
**Status**: ✅ Production Ready
