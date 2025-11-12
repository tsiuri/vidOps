# Usage Guide - How to Interface with the Tools

## Recommended Usage Pattern

**Always run commands from the workspace root directory using wrapper scripts.**

This is the simplest and most reliable approach:

```bash
# Navigate to workspace root
cd "/path/to/workspace"

# Run any command using the wrapper
./clips.sh pull <url>
./filter_voice_parallel.py --clips-dir media/clips/ ...
./stitch_videos_batched.sh media/clips/input/ media/final/output.mp4 date_timestamp
```

## Why Run from Workspace Root?

The tools create files and directories relative to where they're executed:

| Tool | Creates | Where |
|------|---------|-------|
| `clips.sh pull` | `pull/` directory | Current directory |
| Voice filtering | Output JSON | Specified path or current dir |
| Video stitching | Output video | Specified path |
| Date scripts | Output files | `data/` (now) or current dir (old) |

**Running from workspace root ensures:**
- `pull/` goes to workspace root (not buried in scripts/)
- Relative paths work correctly (`data/`, `results/`, `media/`)
- Logs and outputs go to expected locations
- You can see what's being created

## Usage Patterns

### Pattern 1: Wrapper Scripts from Root (Recommended ✓)

```bash
cd /path/to/workspace

# Download videos
./clips.sh pull "https://youtube.com/watch?v=..."

# Filter voice
./filter_voice_parallel_chunked.py \
  --clips-dir media/clips/my_clips \
  --reference-dir data/reference_clips \
  --output results/voice_filtered/results.json

# Stitch videos
./stitch_videos_batched.sh \
  media/clips/confirmedPiker1/ \
  media/final/output.mp4 \
  date_timestamp
```

## Quick Reference

**Before running any command:**
```bash
cd /path/to/workspace
```

**Then use wrapper scripts:**
```bash
./clips.sh [command]              # Download, hit detection, cutting
./filter_voice*.py [args]   # Voice filtering
./stitch_videos*.sh [args]        # Video stitching
./find_missing_dates.py [args]    # Date management
```

**All commands assume you're in workspace root.**
