# Quick Reference Card

## NEW: Unified Interface (Recommended)

```bash
./workspace.sh <command> [options]
```

### Most Common Commands

```bash
# Download
./workspace.sh download "URL"
./workspace.sh download --from-list data/download_list.txt

# Find & Cut Clips
./workspace.sh clips hits -q "phrase1,phrase2"
./workspace.sh clips cut-local results/wanted.tsv media/clips/raw

# Voice Filtering
./workspace.sh voice filter-chunked \
  --clips-dir media/clips/raw \
  --reference-dir data/reference_clips \
  --output results/voice_filtered/results.json

./workspace.sh voice extract  # Extract matching clips

# Video Stitching
./workspace.sh stitch batched \
  media/clips/confirmedPiker1/ \
  media/final/output.mp4 \
  date_timestamp

# Date Management
./workspace.sh dates find-missing \
  --dates-file data/dates.txt \
  --search-dir /path/to/search \
  --output data/missing.txt

./workspace.sh dates create-list \
  data/missing.txt \
  archive_metadata_cache.json

# GPU Binding
./workspace.sh gpu status        # Check current bindings
sudo ./workspace.sh gpu to-nvidia  # Bind to host (CUDA, transcoding)
sudo ./workspace.sh gpu to-vfio    # Bind to VM passthrough

# Information
./workspace.sh info    # Show workspace status
./workspace.sh help    # Show all commands
```

## OLD: Individual Scripts (Still Work)

### Voice Filtering
```bash
./filter_voice_parallel_chunked.py \
  --clips-dir /path/to/clips \
  --reference-dir /path/to/reference_clips \
  --output results/voice_filtered/results.json \
  --threshold 0.65 \
  --chunk-size 200 \
  --workers 23
```

### Video Stitching
```bash
./stitch_videos_batched.sh \
  media/clips/confirmedPiker1/ \
  media/final/output.mp4 \
  date_timestamp
```

### Date Management
```bash
./find_missing_dates.py \
  --dates-file data/dates.txt \
  --search-dir /path/to/search \
  --output data/missing.txt

./create_download_list.py \
  data/missing.txt \
  archive_metadata_cache.json

while read url; do ./clips.sh pull "$url"; done < data/download_list.txt
```

## Directory Quick Access

| What | Where |
|------|-------|
| Workspace CLI | `./workspace.sh` |
| Scripts | `scripts/<category>/` |
| Input data | `data/` |
| Results | `results/` |
| Video clips | `media/clips/` |
| Final videos | `media/final/` |
| Logs | `logs/<category>/` |
| Downloads | `pull/` |

## Getting Help

```bash
# Unified interface help
./workspace.sh help
./workspace.sh help <command>

# Individual script help
python3 scripts/<category>/<script>.py --help
scripts/<category>/<script>.sh  # Shows usage
```

## Tab Completion

```bash
# Enable tab completion
source workspace-completion.bash

# Try it
./workspace.sh <TAB><TAB>
./workspace.sh voice <TAB><TAB>
```
