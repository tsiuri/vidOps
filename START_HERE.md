# 🎬 START HERE - Unified Workspace Interface

## The New Way: One Command to Rule Them All

Instead of remembering 20+ different scripts, use **`workspace.sh`** - your single entry point for everything.

```bash
cd /path/to/workspace
./workspace.sh <command> [options]
```

That's it!

---

## Quick Start Guide

### See What's Available

```bash
./workspace.sh help          # Show all commands
./workspace.sh info          # Show workspace status
```

### Download Videos

```bash
# Download single video
./workspace.sh download "https://youtube.com/watch?v=..."

# Download from channel
./workspace.sh download "@channelname"

# Download from list
./workspace.sh download --from-list data/download_list.txt
```

### Find and Cut Clips

```bash
# Find clips containing phrases
./workspace.sh clips hits -q "hasan,react,drama"

# Cut clips from local files
./workspace.sh clips cut-local results/wanted.tsv media/clips/raw
```

### Filter by Voice

```bash
# Interactive - prompts you for method
./workspace.sh voice filter \
  --clips-dir media/clips/raw \
  --reference-dir data/reference_clips

# Or use specific method directly
./workspace.sh voice filter-chunked \
  --clips-dir media/clips/raw \
  --reference-dir data/reference_clips \
  --output results/voice_filtered/results.json

# Extract matching clips to text file
./workspace.sh voice extract
```

### Stitch Videos

```bash
# Recommended: batched method
./workspace.sh stitch batched \
  media/clips/confirmedPiker1/ \
  media/final/compilation.mp4 \
  date_timestamp

# Fast method (may have sync issues)
./workspace.sh stitch cfr \
  media/clips/confirmedPiker1/ \
  media/final/compilation.mp4 \
  date_timestamp
```

### Manage Dates

```bash
# Find which dates are missing
./workspace.sh dates find-missing \
  --dates-file data/dates_to_pull.txt \
  --search-dir /path/to/existing/files \
  --output data/missing_dates.txt

# Create download list from missing dates
./workspace.sh dates create-list \
  data/missing_dates.txt \
  /path/to/archive_metadata_cache.json

# Move files matching dates
./workspace.sh dates move \
  --dates-file data/dates.txt \
  --source-dir /path/to/source \
  --dest-dir media/clips/selected
```

---

## Complete Workflow Example

Here's a real workflow from start to finish:

```bash
cd /path/to/workspace

# Step 1: Download videos from missing dates
./workspace.sh download --from-list data/download_list.txt

# Step 2: Find interesting clips
./workspace.sh clips hits -q "hasan,react,politics" -o results/wanted.tsv

# Step 3: Cut the clips
./workspace.sh clips cut-local results/wanted.tsv media/clips/raw

# Step 4: Filter by voice matching
./workspace.sh voice filter-chunked \
  --clips-dir media/clips/raw \
  --reference-dir data/reference_clips \
  --output results/voice_filtered/results.json

# Step 5: Extract matching clips
./workspace.sh voice extract

# Step 6: Move filtered clips to final location
mkdir -p media/clips/confirmedPiker1
while IFS= read -r clip; do
    mv "$clip" media/clips/confirmedPiker1/
done < results/voice_filtered/matched_clips.txt

# Step 7: Stitch into final video
./workspace.sh stitch batched \
  media/clips/confirmedPiker1/ \
  media/final/compilation_$(date +%Y%m%d).mp4 \
  date_timestamp
```

Done! You now have a compilation video.

---

## Getting Help

### General Help
```bash
./workspace.sh help              # Show all commands
./workspace.sh help <command>    # Help for specific command
```

### Specific Command Help
```bash
./workspace.sh help download     # Download help
./workspace.sh help voice        # Voice filtering help
./workspace.sh help stitch       # Video stitching help
./workspace.sh help dates        # Date management help
./workspace.sh help clips        # Clips operations help
```

---

## Optional: Enable Tab Completion

For even faster workflows, enable tab completion:

```bash
# One-time setup
source workspace-completion.bash

# Or add to ~/.bashrc for permanent use
echo "source $(pwd)/workspace-completion.bash" >> ~/.bashrc
```

Now you can use TAB to autocomplete:
```bash
./workspace.sh <TAB><TAB>         # Shows all commands
./workspace.sh voice <TAB><TAB>   # Shows voice subcommands
```

---

## Why Use workspace.sh?

### Before (❌ Confusing)
```bash
# Which script do I use again?
./filter_voice_parallel_chunked.py --clips-dir ... --reference-dir ... --output ...

# Or was it?
scripts/voice_filtering/filter_voice_parallel_chunked.py ...

# Where does this go?
cd scripts/video_processing/
./stitch_videos_batched.sh ../../media/clips/input/ ../../media/final/output.mp4 date_timestamp
cd ../..
```

### After (✅ Simple)
```bash
# Always the same pattern from workspace root
./workspace.sh voice filter-chunked --clips-dir media/clips/raw --reference-dir data/reference_clips
./workspace.sh stitch batched media/clips/input/ media/final/output.mp4 date_timestamp
```

**Benefits:**
- ✅ One command to remember: `workspace.sh`
- ✅ Clear, organized subcommands
- ✅ Always runs from workspace root (no path confusion)
- ✅ Built-in help for every command
- ✅ Easier to discover what's available
- ✅ Tab completion support

---

## Still Want the Old Way?

All the individual wrapper scripts still work:

```bash
./clips.sh pull <url>
./filter_voice_parallel.py [args]
./stitch_videos_batched.sh [args]
```

But `workspace.sh` is recommended for consistency and discoverability.

---

## Directory Structure Reminder

```
workspace/
├── workspace.sh           # ← USE THIS - unified interface
├── pull/                  # Downloads go here
├── cuts/                  # Cut clips go here
├── media/
│   ├── clips/            # Organize your clips
│   └── final/            # Final videos
├── data/                 # Date lists, download lists
├── results/              # Voice filtering, analysis
└── [individual wrappers] # Still work, but workspace.sh is easier
```

---

## Common Commands Reference

| Task | Command |
|------|---------|
| Download video | `./workspace.sh download <url>` |
| Find clips | `./workspace.sh clips hits -q "phrase"` |
| Filter voice | `./workspace.sh voice filter-chunked [options]` |
| Stitch videos | `./workspace.sh stitch batched input/ output.mp4` |
| Find missing dates | `./workspace.sh dates find-missing [options]` |
| Show workspace info | `./workspace.sh info` |
| Get help | `./workspace.sh help [command]` |

---

## Next Steps

1. **Try it:** `./workspace.sh info` to see your workspace status
2. **Get help:** `./workspace.sh help` to see all available commands
3. **Enable completion:** `source workspace-completion.bash` for tab completion
4. **Start working:** Pick a command above and start processing videos!

For detailed documentation, see:
- `docs/README.md` - Complete workspace guide
- `QUICK_REFERENCE.md` - Quick command reference
- `RECOMMENDED_INTERFACE.md` - Interface recommendations

---

## NEW: GPU Binding Management

Easily switch GPU drivers between host (NVIDIA) and VM passthrough (vfio-pci):

```bash
# Check current GPU bindings
./workspace.sh gpu status

# Before running a VM - bind to vfio-pci
sudo ./workspace.sh gpu to-vfio

# After VM shutdown - bind back to NVIDIA for transcoding/CUDA
sudo ./workspace.sh gpu to-nvidia

# Get help
./workspace.sh help gpu
```

**Use cases:**
- **to-nvidia**: For video transcoding, voice filtering (CUDA), or host graphics
- **to-vfio**: For VM passthrough (GPU in virtual machine)

---

## Command Logging

All commands (except help/info/status) are automatically logged to:
```
logs/workspace_commands.log
```

View recent commands:
```bash
tail -20 logs/workspace_commands.log
```

This helps track what operations you've run and when.
