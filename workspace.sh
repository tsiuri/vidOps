#!/usr/bin/env bash
# workspace.sh - Unified CLI interface for all workspace tools
# Usage: ./workspace.sh <command> [options]

set -euo pipefail

# Tool installation directory (where scripts live)
# Resolve symlinks to find the actual script location
SCRIPT_PATH="$(readlink -f "$0")"
TOOL_ROOT="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

# Project directory (where data lives) - current working directory
PROJECT_ROOT="$(pwd)"

# Export for child scripts
export TOOL_ROOT
export PROJECT_ROOT

# Verify we're in a valid project directory; prompt before creating structure
# Accept either legacy marker (.vidops-project) or new marker (.vidops_deploy_marker)
if [[ ! -f "$PROJECT_ROOT/.vidops-project" && ! -f "$PROJECT_ROOT/.vidops_deploy_marker" ]]; then
    echo -e "\033[1;33mThis directory is not initialized as a VidOps project.\033[0m"
    echo "Path: $PROJECT_ROOT"
    echo "If initialized, the following directories may be created here:"
    echo "  pull/ generated/ logs/{pull,db} data/ results/ media/{clips,final} config/ cuts"
    if [[ -t 0 ]]; then
        read -r -p "Initialize VidOps project structure here? [y/N]: " _ans
    else
        echo "Non-interactive session detected; defaulting to No."
        _ans=""
    fi
    case "${_ans,,}" in
        y|yes)
            echo -e "\033[1;33mInitializing VidOps project in: $PROJECT_ROOT\033[0m"
            # Create deployment marker to avoid future prompts in this directory
            touch "$PROJECT_ROOT/.vidops_deploy_marker"
            mkdir -p "$PROJECT_ROOT"/{pull,generated,logs/pull,logs/db,data,results,media/{clips,final},config,cuts}
            echo -e "\033[1;32mProject structure created.\033[0m"
            ;;
        *)
            echo -e "\033[1;34mSkipping initialization.\033[0m"
            echo "To suppress this prompt in the future for this directory, either re-run and answer 'y',"
            echo "or manually create the marker file: $PROJECT_ROOT/.vidops_deploy_marker"
            ;;
    esac
fi

# Stay in project directory - don't cd to tool directory

# Color output
C_BLUE='\033[0;34m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'
C_RED='\033[0;31m'
C_RESET='\033[0m'

show_help() {
    cat <<'EOF'
workspace.sh - Unified CLI for video processing workspace

USAGE
  ./workspace.sh <command> [options]

COMMANDS
  download <url>              Download videos from YouTube
  clips <subcommand>          Clip operations (hits, cut-local, cut-net, refine)
  dl-subs <action>            Download auto-generated subtitles/captions
  extra-utils <tool|help>     Run extra utilities (see EXTRA_UTILS.md)
  voice <action>              Voice filtering operations
  transcribe [options]        Dual-GPU transcription with Whisper
  analyze <transcripts...>    AI-powered transcript analysis
  convert-captions            Convert VTT captions to words.yt.tsv
  stitch <method>             Stitch videos together
  dates <action>              Date-based file management
  gpu <action>                GPU binding management (to-nvidia requires sudo)
  dbupdate [database]         Full DB ingest/update pipeline (prompts)
  info                        Show workspace information
  help [command]              Show help for a command

EXAMPLES
  # Download a video
  ./workspace.sh download "https://youtube.com/watch?v=..."

  # Find clips with phrases
  ./workspace.sh clips hits -q "phrase1,phrase2"

  # Filter by Hasan's voice
  ./workspace.sh voice filter --clips-dir media/clips/raw

  # Stitch videos (batch method)
  ./workspace.sh stitch batch media/clips/input/ media/final/output.mp4

  # Find missing dates
  ./workspace.sh dates find-missing data/dates.txt /path/to/search

  # Show help for specific command
  ./workspace.sh help voice

For detailed help on any command, use: ./workspace.sh help <command>

LOCATION
  Tool installed at: $TOOL_ROOT
  Project directory: (current directory)

  Project structure:
    pull/       - Downloaded videos and metadata
    generated/  - Transcriptions
    logs/       - Operation logs
    data/       - Configuration and lists
    results/    - Processing outputs
    media/      - Processed media files

EXTRA UTILITIES (use: ./workspace.sh extra-utils)
  Standalone scripts (see EXTRA_UTILS.md, e.g., ~/tools/vidops/EXTRA_UTILS.md):
    scripts/utilities/mark_success.sh
    scripts/utilities/quality_report.py
    scripts/utilities/repair_archive.sh
    scripts/utilities/map_ids_to_files.py
    scripts/utilities/sort_clips.py
    scripts/video_processing/concat_filter_from_list.sh
    scripts/video_processing/stitch_videos_batched_filter.sh
    scripts/transcription/detect_dupe_hallu.py
    scripts/transcription/watch_cuda_error.sh
EOF
}

show_command_help() {
    local cmd="$1"
    case "$cmd" in
        extra-utils)
            cat <<'EOF'
EXTRA-UTILS - Run extra standalone utilities

USAGE
  ./workspace.sh extra-utils help
  ./workspace.sh extra-utils <tool> [args]

TOOLS
  See EXTRA_UTILS.md for the full list and usage details.

EXAMPLES
  ./workspace.sh extra-utils repair_archive.sh --report
  ./workspace.sh extra-utils map_ids_to_files.py
EOF
            ;;
        download)
            cat <<'EOF'
DOWNLOAD - Download videos from YouTube

USAGE
  ./workspace.sh download <url> [--force|--no-download-archive]
  ./workspace.sh download <channel-handle> [--force]
  ./workspace.sh download --from-list <file>
  ./workspace.sh download --retry-failed [<failed_urls.txt>] [extra yt-dlp flags]

OPTIONS
  --force                Force redownload even if in archive
  --no-download-archive  Same as --force
  --retry-failed         Retry the most recent logs/pull/*.failed_urls.txt (or provided file)

EXAMPLES
  # Download single video
  ./workspace.sh download "https://youtube.com/watch?v=VIDEO_ID"

  # Download from channel
  ./workspace.sh download "@channelhandle"

  # Force redownload a video already in archive
  ./workspace.sh download "URL" --force

  # Download from list
  ./workspace.sh download --from-list data/download_list.txt

  # Retry failed entries from last run (forces reattempt)
  ./workspace.sh download --retry-failed --force

OUTPUT
  Downloads go to: pull/
EOF
            ;;
        clips)
            cat <<'EOF'
CLIPS - Clip detection and extraction

USAGE
  ./workspace.sh clips <subcommand> [options]

SUBCOMMANDS
  hits [-q "phrase"] [-o output.tsv]    Find clips with phrases
  cut-local <tsv> [outdir]              Cut from local files
  cut-net <tsv> [outdir]                Cut from network
  refine -q WORD [indir] [outdir]       Refine existing clips

EXAMPLES
  # Find clips with phrases
  ./workspace.sh clips hits -q "interesting,phrase" -o results/wanted.tsv

  # Exact hits using words timestamps with padding
  PAD_START=0.25 PAD_END=0.50 \
    ./workspace.sh clips hits --words --exact -q "hasan abi"

  # Cut from local files
  ./workspace.sh clips cut-local results/wanted.tsv media/clips/raw

  # Refine clips
  ./workspace.sh clips refine -q "hasan" media/clips/raw media/clips/refined

For full clips.sh documentation, see scripts/utilities/clips.sh help
EOF
            ;;
        dl-subs)
            cat <<'EOF'
DL-SUBS - Download auto-generated subtitles

USAGE
  ./workspace.sh dl-subs <action> [options]

ACTIONS
  subs-only <URL> [format]    Download only subtitles for a single video
  batch <file> [format]       Batch download subtitles from URL list
  from-dir <dir> [format]     Discover video IDs from media files in a directory and download subs for each

FORMATS
  vtt     Video Text Tracks (default, includes timestamps)
  srt     SubRip format (includes timestamps)
  json3   JSON format (structured data)

EXAMPLES
  # Download subs for a single video
  ./workspace.sh dl-subs subs-only "https://www.youtube.com/watch?v=VIDEO_ID"

  # Batch download subs from a list
  ./workspace.sh dl-subs batch url_list.txt vtt

  # Download subs for all media files in a directory
  ./workspace.sh dl-subs from-dir pull/ vtt

NOTES
  from-dir:
    - Scans only the top-level of <dir> (non-recursive)
    - Expects filenames to start with the 11-char YouTube ID followed by "__"
      e.g., ObWv-_aPiXI__2025-11-19 - Title.ext
    - Deduplicates IDs discovered in the directory
    - Supports common media extensions (mp4, mkv, mov, avi, mp3, wav, m4a, opus, webm)
    - Saves subtitles into pull/ matching the standard naming scheme

OUTPUT
  Subtitles saved to: pull/
  Filename format: {video_id}__{upload_date} - {title}.transcript.{lang}.{format}
  Example: Bldyb2JvaF0__2019-05-21 - Title Here.transcript.en.vtt

NOTE
  Videos downloaded via ./workspace.sh download now automatically include auto-generated subtitles.
EOF
            ;;
        voice)
            cat <<'EOF'
VOICE - Voice filtering operations

USAGE
  ./workspace.sh voice <action> [options]

ACTIONS
  filter              Run voice filter (prompts for method)
  filter-simple       Simple single-threaded filter
  filter-parallel     Parallel filter (fast)
  filter-chunked      Chunked parallel filter (most reliable)
  extract             Extract matching clips from results

EXAMPLES
  # Filter clips (interactive - will prompt for method)
  ./workspace.sh voice filter \
    --clips-dir media/clips/raw \
    --reference-dir data/reference_clips

  # Use specific method (chunked, recommended)
  ./workspace.sh voice filter-chunked \
    --clips-dir media/clips/raw \
    --reference-dir data/reference_clips \
    --output results/voice_filtered/results.json \
    --threshold 0.65 \
    --chunk-size 200

  # Filter with glob pattern and custom workers
  ws voice filter \
    --reference voice_reference/* \
    --output filtered \
    --workers 23 \
    --chunk-size 200 \
    filter_source

  # Extract matching clips to text file
  ./workspace.sh voice extract results/voice_filtered/results.json

OUTPUT
  Results go to: results/voice_filtered/
  Extracted list: results/voice_filtered/hasan_clips.txt
EOF
            ;;
        dbupdate)
            cat <<'EOF'
DBUPDATE - Full Postgres ingest/update pipeline

USAGE
  ./workspace.sh dbupdate [database]

DESCRIPTION
  Runs the end-to-end pipeline with prompts:
    1) Export videos from pull/*__*.info.json
    2) Load videos
    3) Prompt to set videos.upload_type for this batch
    4) Derive title dates from titles
    5) Scan generated/ for transcripts (prefer Whisper words) and load
    6) Prompt for optional hits TSV to load
    7) Prompt to optionally load per-word timestamps (large)
    8) ANALYZE planner stats

OUTPUTS
  Intermediate manifests under logs/db/; DB tables populated/updated.

NOTE
  You can run steps individually via scripts/db/* if you prefer.
EOF
            ;;
        stitch)
            cat <<'EOF'
STITCH - Concatenate videos

USAGE
  ./workspace.sh stitch <method> <input-dir> <output-file> [sort-method]

METHODS
  batch               Batch approach (most reliable, recommended)
  cfr                 Constant frame rate (fast)
  simple              Simple re-encode

SORT METHODS
  date_timestamp      Sort by date then timestamp (recommended)
  timestamp           Sort by timestamp only
  name                Sort alphabetically
  time                Sort by file modification time

EXAMPLES
  # Stitch with batch method (recommended)
  ./workspace.sh stitch batch \
    media/clips/confirmedPiker1/ \
    media/final/output.mp4 \
    date_timestamp

  # Stitch with CFR method (faster, may have issues)
  ./workspace.sh stitch cfr \
    media/clips/confirmedPiker1/ \
    media/final/output.mp4 \
    date_timestamp

OUTPUT
  Final videos go to specified path (recommend: media/final/)
EOF
            ;;
        dates)
            cat <<'EOF'
DATES - Date-based file management

USAGE
  ./workspace.sh dates <action> [options]

ACTIONS
  find-missing        Find + print text list of which dates, of a list of dates in a textfile, don't have a corresponding youtube video file of that date.  (ex, check every calendar day for a VOD)
  create-list         Creates a download list of videos from missing dates NOTE: for fixing files with metadata and no corresponding video!  For fixing partial downloads!
  move                Moves .opus files whose filenames contain a specific “Month Day, Year” date that matches a provided list, plus any sidecar .json files sharing the same base name.
  compare               - Compares dates present in your local “podcasts” (.opus files under pull/) against dates present in a txt list of YouTube video IDs. Outputs three sets: only in podcasts, only in archive, and in both.  For deduplicating VODs from overlapping sources.


EXAMPLES
  # Find missing dates
  ./workspace.sh dates find-missing \
    --dates-file data/dates_to_pull.txt \
    --search-dir /path/to/existing/files \
    --output data/missing_dates.txt

  # Create download list
  ./workspace.sh dates create-list \
    data/missing_dates.txt \
    /path/to/archive_metadata_cache.json

  # Move files by date
  ./workspace.sh dates move \
    --dates-file data/dates.txt \
    --source-dir /path/to/source \
    --dest-dir media/clips/selected

OUTPUT
  Results go to: data/ (lists) or specified paths
EOF
            ;;
        gpu)
            cat <<'EOF'
GPU - GPU binding management

USAGE
  ./workspace.sh gpu <action>

ACTIONS
  status              Show current GPU driver bindings
  to-nvidia           Rebind NVIDIA GPUs to host (nvidia drivers)

EXAMPLES
  # Check current GPU bindings
  ./workspace.sh gpu status

  # Rebind to host (for transcoding, CUDA work, etc.)
  sudo ./workspace.sh gpu to-nvidia

NOTE
  - status: No sudo required
  - to-nvidia: Requires sudo/root
  - Rebinding will affect running GPU applications
  - After rebinding to nvidia, you may need to restart display manager
  - Only affects GPU at 0000:05:00.0 and its audio cohort at 0000:05:00.1
EOF
            ;;
        transcribe)
            cat <<'EOF'
TRANSCRIBE - Dual-GPU batch transcription with Whisper

USAGE
  ./workspace.sh transcribe [options]

DESCRIPTION
  Transcribes all media files in pull/ using NVIDIA + AMD GPUs in parallel
  Outputs transcriptions to generated/ directory

COMMON OPTIONS
  --model <size>              Whisper model (tiny|base|small|medium|large) [default: medium]
  --language <code>           Force language (en, es, etc) or empty for auto [default: en]
  --force                     Re-transcribe even if .txt already exists
  --outfmt <format>           Output format: vtt|srt|both [default: vtt]
  --filelist <file>           Read media paths from file (one per line, bypasses directory search)
  --batch-retry               Process retry manifests (re-transcribe low-confidence segments)
  --follow / --no-follow      Live-tail logs [default: --follow]
  --setup-venvs               Create/update virtual environments (first-time setup)

EXAMPLES
  # First-time setup (creates virtual environments)
  ./workspace.sh transcribe --setup-venvs

  # Regular transcription (uses files in pull/)
  ./workspace.sh transcribe

  # Transcribe specific files from a list
  ./workspace.sh transcribe --filelist my_videos.txt

  # Use larger model for better accuracy
  ./workspace.sh transcribe --model large

  # Force re-transcribe with SRT format
  ./workspace.sh transcribe --force --outfmt srt

  # Process retry manifests to fix low-confidence segments
  ./workspace.sh transcribe --batch-retry

  # Dry run to see what would be retried
  ./workspace.sh transcribe --batch-retry --dry-run

  # Enable CPU worker alongside GPUs
  ENABLE_CPU=1 ./workspace.sh transcribe

INPUT
  Searches for media in: pull/ (or current dir if pull/ doesn't exist)
  Or use --filelist to specify exact files to transcribe
  Supported formats: mp4, mkv, mov, avi, mp3, wav, m4a, opus

OUTPUT
  Transcriptions go to: generated/
  Logs go to: logs/nv.log, logs/amd.log
  Retry manifests: generated/*.retry_manifest.tsv (low-confidence segments)

NOTE
  Requires GPU access. For NVIDIA/AMD setup, see the script help:
  ./scripts/transcription/dual_gpu_transcribe.sh --help

  To re-transcribe low-confidence segments, use --batch-retry after transcription
  After transcription completes, this command also runs:
    ./scripts/transcription/detect_dupe_hallu.py
  to generate an additional dupe_hallu.retry_manifest.tsv (when seed manifests exist),
  which you can include in a subsequent --batch-retry.
EOF
            ;;
        analyze)
            cat <<'EOF'
ANALYZE - AI-Powered Transcript Analysis

USAGE
  ./workspace.sh analyze <file|dir> [file|dir ...] [options]

DESCRIPTION
  Analyzes VTT transcript files using local AI (Ollama) to extract:
  - People mentioned with frequency counts
  - Main topics and themes
  - Sentiment and mood analysis
  - Key points and notable quotes
  - Content categorization

  Outputs JSON data and human-readable markdown reports to analyzed/

COMMON OPTIONS
  --output <dir>              Output directory [default: analyzed/]
  --model <name>              Ollama model to use [default: llama3.2]
  --chunk-size <words>        Words per chunk [default: 1000]
  --overlap <words>           Overlap between chunks [default: 150]
  --ollama-url <url>          Ollama API URL [default: http://localhost:11434]
  --no-summaries              Skip generating multiple summary variants
  --quality <q>               Quality profile: fast|balanced|thorough [default: balanced]
  --temperature <float>       Sampling temperature (override profile)
  --top-p <float>             Top-p nucleus sampling (override profile)
  --top-k <int>               Top-k sampling (override profile)
  --num-predict <int>         Max tokens to generate (override profile)
  --num-ctx <int>             Context window in tokens (override profile)
  --repeat-penalty <float>    Repetition penalty (override profile)

EXAMPLES
  # Analyze a single transcript
  ./workspace.sh analyze pull/VIDEO_ID__*.transcript.en.vtt

  # Analyze multiple at once (shell expands globs)
  ./workspace.sh analyze pull/*.transcript.en.vtt generated/*.vtt

  # Analyze a directory (auto-detects *.transcript.en.vtt, falls back to *.vtt)
  ./workspace.sh analyze pull/ generated/

  # Higher quality with more generation budget
  ./workspace.sh analyze pull/*.vtt \
    --quality thorough --num-predict 1500 --num-ctx 8192 --temperature 0.2

  # Use a different model
  ./workspace.sh analyze pull/VIDEO_ID__*.transcript.en.vtt --model llama3

  # Custom output directory
  ./workspace.sh analyze pull/VIDEO_ID__*.transcript.en.vtt --output reports/

OUTPUT
  Creates two files per transcript in analyzed/:
  - VIDEO_ID_analysis.json  (structured data)
  - VIDEO_ID_analysis.md    (human-readable report with Summaries section)

REQUIREMENTS
  - Ollama installed and running (https://ollama.com)
  - Python 3 with 'requests' library
  - Model downloaded: ollama pull llama3.2

NOTE
  First time setup:
    1. Install Ollama: curl -fsSL https://ollama.com/install.sh | sh
    2. Pull model: ollama pull llama3.2
    3. Start service: ollama serve (runs in background)
    4. Install Python deps: pip install -r scripts/analysis/requirements.txt
EOF
            ;;
        convert-captions)
            cat <<'EOF'
CONVERT-CAPTIONS - Convert VTT captions to words.yt.tsv

USAGE
  ./workspace.sh convert-captions [options] [files...]

DESCRIPTION
  Converts YouTube VTT subtitle files (pull/*.transcript.en.vtt) into the
  words.yt.tsv format used by other tools. Each cue is split into words and
  timestamps are distributed evenly per word. Confidence defaults to 0.0
  unless present as a preceding "NOTE Confidence:" line in the VTT.

OPTIONS
  --source-dir <dir>     Source directory (default: pull)
  --dest-dir <dir>       Destination directory (default: generated)
  --overwrite            Overwrite existing *.words.yt.tsv
  --dry-run              Show actions without writing

EXAMPLES
  # Convert all VTTs in pull/ to generated/*.words.yt.tsv
  ./workspace.sh convert-captions

  # Overwrite existing outputs
  ./workspace.sh convert-captions --overwrite

  # Convert specific files
  ./workspace.sh convert-captions pull/VIDEO__*.transcript.en.vtt

OUTPUT
  Writes: generated/{BASE}.words.yt.tsv
  BASE: filename without .transcript.en.vtt
EOF
            ;;
        info)
            cat <<'EOF'
INFO - Show workspace information

Displays current workspace configuration and directory status.
EOF
            ;;
        *)
            echo "No help available for: $cmd"
            echo "Use: ./workspace.sh help"
            return 1
            ;;
    esac
}

show_info() {
    echo -e "${C_BLUE}VidOps Project Information${C_RESET}"
    echo ""
    echo "Tool Location:    $TOOL_ROOT"
    echo "Project Location: $PROJECT_ROOT"
    echo ""
    echo "Directory Status:"

    for dir in pull generated cuts data results media logs config; do
        if [[ -d "$PROJECT_ROOT/$dir" ]]; then
            count=$(find "$PROJECT_ROOT/$dir" -type f 2>/dev/null | wc -l)
            echo -e "  ${C_GREEN}✓${C_RESET} $dir/ ($count files)"
        else
            echo -e "  ${C_YELLOW}○${C_RESET} $dir/ (not created)"
        fi
    done

    echo ""
    echo "Available Tools:"
    echo "  Voice filtering:  $(find "$TOOL_ROOT/scripts/voice_filtering" -name "*.py" 2>/dev/null | wc -l) scripts"
    echo "  Video processing: $(find "$TOOL_ROOT/scripts/video_processing" -name "*.sh" 2>/dev/null | wc -l) scripts"
    echo "  Date management:  $(find "$TOOL_ROOT/scripts/date_management" -name "*.py" 2>/dev/null | wc -l) scripts"
    echo ""
}

cmd_download() {
    if [[ "${1:-}" == "--from-list" ]]; then
        local list_file="${2:-}"
        shift 2 || true
        if [[ -z "$list_file" ]]; then
            echo "Error: --from-list requires a file path"
            exit 1
        fi
        echo "Downloading from list: $list_file"
        # Consolidate logs across this batch using a shared prefix
        mkdir -p "$PROJECT_ROOT/logs/pull" 2>/dev/null || true
        local ts lp
        ts="$(date -u '+%Y%m%d_%H%M%SZ')"
        lp="$PROJECT_ROOT/logs/pull/${ts}"
        export PULL_LOG_PREFIX="$lp"
        # Enable cookies-first for from-list when the list likely contains failed URLs
        if [[ "$list_file" == *"failed_urls.txt"* ]]; then
            export PULL_COOKIES_FIRST=1
        fi
        # Ensure fresh files for this prefix
        rm -f "${lp}.requested.tsv" "${lp}.succeeded.tsv" "${lp}.failed.tsv" "${lp}.failed_urls.txt" 2>/dev/null || true
        while read -r url; do
            [[ -z "$url" ]] && continue
            [[ "$url" =~ ^# ]] && continue
            "$TOOL_ROOT/clips.sh" pull "$url" "$@"
        done < "$list_file"
    elif [[ "${1:-}" == "--retry-failed" ]]; then
        shift || true
        local list_file="${1:-}"
        # If a file path isn't provided or doesn't exist, pick the latest logs/pull/*.failed_urls.txt
        if [[ -z "$list_file" || ! -f "$list_file" ]]; then
            # pick the latest non-empty failed_urls file
            list_file=""
            for f in $(ls -1t "$PROJECT_ROOT/logs/pull"/*.failed_urls.txt 2>/dev/null || true); do
                if [[ -s "$f" ]]; then list_file="$f"; break; fi
            done
        else
            # consume the explicit file arg
            shift || true
        fi
        if [[ -z "$list_file" || ! -f "$list_file" ]]; then
            echo "Error: No failed_urls.txt found. Run a download first to generate logs under logs/pull/."
            exit 1
        fi
        if [[ ! -s "$list_file" ]]; then
            echo "Found failed list but it is empty: $list_file"
            echo "Looking for previous non-empty failed list..."
            alt=""
            for f in $(ls -1t "$PROJECT_ROOT/logs/pull"/*.failed_urls.txt 2>/dev/null || true); do
                [[ "$f" == "$list_file" ]] && continue
                if [[ -s "$f" ]]; then alt="$f"; break; fi
            done
            if [[ -n "$alt" ]]; then
                echo "Using previous non-empty list: $alt"
                list_file="$alt"
            else
                echo "No non-empty failed list found. Nothing to retry."
                exit 0
            fi
        fi
        echo "Retrying failed URLs from: $list_file"
        # Consolidate logs across this retry batch
        mkdir -p "$PROJECT_ROOT/logs/pull" 2>/dev/null || true
        local ts lp
        ts="$(date -u '+%Y%m%d_%H%M%SZ')"
        lp="$PROJECT_ROOT/logs/pull/${ts}"
        export PULL_LOG_PREFIX="$lp"
        export PULL_COOKIES_FIRST=1
        rm -f "${lp}.requested.tsv" "${lp}.succeeded.tsv" "${lp}.failed.tsv" "${lp}.failed_urls.txt" 2>/dev/null || true
        while read -r url; do
            [[ -z "$url" ]] && continue
            [[ "$url" =~ ^# ]] && continue
            # Always force for retries; also forward any extra args after --retry-failed
            "$TOOL_ROOT/clips.sh" pull "$url" --force "$@"
        done < "$list_file"
    else
        "$TOOL_ROOT/clips.sh" pull "$@"
    fi
}

cmd_clips() {
    "$TOOL_ROOT/clips.sh" "$@"
}

cmd_dl_subs() {
    local action="${1:-}"
    if [[ -z "$action" ]]; then
        "$TOOL_ROOT/clips.sh" transcripts help
        return
    fi
    if [[ "$action" == "from-dir" ]]; then
        shift || true
        local dir="${1:-}"
        local format="${2:-vtt}"
        if [[ -z "$dir" || ! -d "$dir" ]]; then
            echo "Error: from-dir requires a valid directory"
            echo "Usage: ./workspace.sh dl-subs from-dir <dir> [format]"
            exit 1
        fi
        echo "Discovering media in: $dir"
        # Ensure output location exists when clips layer writes to pull/
        mkdir -p "$PROJECT_ROOT/pull" 2>/dev/null || true
        # Supported media extensions (align with transcribe)
        local exts=(mp4 mkv mov avi mp3 wav m4a opus webm)
        declare -A seen
        local total=0 ok=0 fail=0
        while IFS= read -r -d '' f; do
            local bn id url
            bn="$(basename "$f")"
            id="$(printf '%s' "$bn" | sed -nE 's/^([A-Za-z0-9_-]{11})__.*/\1/p')"
            if [[ -z "$id" ]]; then
                continue
            fi
            if [[ -n "${seen[$id]:-}" ]]; then
                continue
            fi
            seen[$id]=1
            url="https://www.youtube.com/watch?v=$id"
            (( total++ ))
            echo "[$total] Downloading subs for $id ($bn)"
            if "$TOOL_ROOT/clips.sh" transcripts subs-only "$url" "$format"; then
                (( ok++ ))
            else
                (( fail++ ))
            fi
        done < <(find "$dir" -maxdepth 1 -type f \( -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.mov' -o -iname '*.avi' -o -iname '*.mp3' -o -iname '*.wav' -o -iname '*.m4a' -o -iname '*.opus' -o -iname '*.webm' \) -print0)
        echo "Done. Processed: $total, Succeeded: $ok, Failed: $fail"
        return
    fi
    "$TOOL_ROOT/clips.sh" transcripts "$@"
}

cmd_voice() {
    local action="${1:-}"
    shift || true
    local VENV_WRAPPER="$TOOL_ROOT/scripts/voice_filtering/voicefil_w_venv.sh"

    case "$action" in
        filter)
            echo "Select voice filter method:"
            echo "  1) Simple (single-threaded)"
            echo "  2) Parallel (fast, multi-core)"
            echo "  3) Chunked (most reliable, recommended)"
            read -p "Choice [3]: " choice
            choice=${choice:-3}

            case "$choice" in
                1) "$VENV_WRAPPER" "$TOOL_ROOT/scripts/voice_filtering/filter_voice.py" "$@" ;;
                2) "$VENV_WRAPPER" "$TOOL_ROOT/scripts/voice_filtering/filter_voice_parallel.py" "$@" ;;
                3) "$VENV_WRAPPER" "$TOOL_ROOT/scripts/voice_filtering/filter_voice_parallel_chunked.py" "$@" ;;
                *) echo "Invalid choice"; exit 1 ;;
            esac
            ;;
        filter-simple)
            "$VENV_WRAPPER" "$TOOL_ROOT/scripts/voice_filtering/filter_voice.py" "$@"
            ;;
        filter-parallel)
            "$VENV_WRAPPER" "$TOOL_ROOT/scripts/voice_filtering/filter_voice_parallel.py" "$@"
            ;;
        filter-chunked)
            "$VENV_WRAPPER" "$TOOL_ROOT/scripts/voice_filtering/filter_voice_parallel_chunked.py" "$@"
            ;;
        extract)
            local results_file="${1:-$PROJECT_ROOT/results/voice_filtered/results.json}"
            local output_file="${2:-$PROJECT_ROOT/results/voice_filtered/matched_clips.txt}"

            echo "Extracting matching clips from: $results_file"
            # Ensure output directory exists
            mkdir -p "$(dirname "$output_file")" 2>/dev/null || true
            python3 -c "
import json
with open('$results_file') as f:
    data = json.load(f)
with open('$output_file', 'w') as out:
    for clip in data['results']:
        if clip.get('is_match', clip.get('is_hasan', False)):
            out.write(f\"{clip['clip_path']}\n\")
print(f\"Extracted to: $output_file\")
"
            ;;
        *)
            echo "Error: Unknown voice action: $action"
            echo "Use: ./workspace.sh help voice"
            exit 1
            ;;
    esac
}

cmd_transcribe() {
    # Helper: run hallucination detection to generate dupe_hallu retry manifests
    run_dupe_hallu_detection() {
        # Collect unprocessed retry manifests to seed media discovery
        local manifests
        mapfile -t manifests < <(find "$PROJECT_ROOT/generated" -maxdepth 1 -type f -name "*.retry_manifest.tsv" ! -name "*.processed" 2>/dev/null | sort || true)
        if [[ ${#manifests[@]} -eq 0 ]]; then
            # Nothing to seed from; skip silently
            return 0
        fi
        echo "Detecting repeated-phrase hallucinations (dupe hallu)…"
        # Best-effort: do not fail overall flow if detector exits non-zero
        python3 "$TOOL_ROOT/scripts/transcription/detect_dupe_hallu.py" "${manifests[@]}" || true
    }

    # Check if --batch-retry is requested
    for arg in "$@"; do
        if [[ "$arg" == "--batch-retry" ]]; then
            "$TOOL_ROOT/scripts/transcription/batch_retry.sh" "$@"
            return
        fi
    done
    # Normal transcription
    "$TOOL_ROOT/scripts/transcription/dual_gpu_transcribe.sh" "$@"
    # Post-processing: generate dupe_hallu retry manifests (if any seed manifests exist)
    run_dupe_hallu_detection
}

cmd_analyze() {
    python3 "$TOOL_ROOT/scripts/analysis/analyze_transcript.py" "$@"
}

cmd_convert_captions() {
    "$TOOL_ROOT/scripts/utilities/convert-captions.sh" "$@"
}

cmd_extra_utils() {
    local tool="${1:-help}"
    shift || true

    case "$tool" in
        help|-h|--help)
            show_command_help extra-utils
            ;;
        mark_success.sh)
            "$TOOL_ROOT/scripts/utilities/mark_success.sh" "$@"
            ;;
        quality_report.py)
            python3 "$TOOL_ROOT/scripts/utilities/quality_report.py" "$@"
            ;;
        repair_archive.sh)
            "$TOOL_ROOT/scripts/utilities/repair_archive.sh" "$@"
            ;;
        map_ids_to_files.py)
            python3 "$TOOL_ROOT/scripts/utilities/map_ids_to_files.py" "$@"
            ;;
        sort_clips.py)
            python3 "$TOOL_ROOT/scripts/utilities/sort_clips.py" "$@"
            ;;
        concat_filter_from_list.sh)
            "$TOOL_ROOT/scripts/video_processing/concat_filter_from_list.sh" "$@"
            ;;
        stitch_videos_batched_filter.sh)
            "$TOOL_ROOT/scripts/video_processing/stitch_videos_batched_filter.sh" "$@"
            ;;
        detect_dupe_hallu.py)
            python3 "$TOOL_ROOT/scripts/transcription/detect_dupe_hallu.py" "$@"
            ;;
        watch_cuda_error.sh)
            "$TOOL_ROOT/scripts/transcription/watch_cuda_error.sh" "$@"
            ;;
        *)
            echo "Error: Unknown extra utility: $tool"
            echo "Use: ./workspace.sh help extra-utils"
            exit 1
            ;;
    esac
}

cmd_stitch() {
    # If no args, show help instead of defaulting silently
    if [[ $# -eq 0 ]]; then
        show_command_help stitch
        return 0
    fi

    local method="${1:-batch}"
    shift || true

    case "$method" in
        batch|batched)
            "$TOOL_ROOT/scripts/video_processing/stitch_videos_batched.sh" "$@"
            ;;
        cfr)
            "$TOOL_ROOT/scripts/video_processing/stitch_videos_cfr.sh" "$@"
            ;;
        simple)
            "$TOOL_ROOT/scripts/video_processing/stitch_videos.sh" "$@" 1
            ;;
        *)
            echo "Error: Unknown stitch method: $method"
            echo "Available: batch, cfr, simple"
            echo "Use: ./workspace.sh help stitch"
            exit 1
            ;;
    esac
}

cmd_dates() {
    local action="${1:-}"
    shift || true

    case "$action" in
        find-missing)
            python3 "$TOOL_ROOT/scripts/date_management/find_missing_dates.py" "$@"
            ;;
        create-list)
            python3 "$TOOL_ROOT/scripts/date_management/create_download_list.py" "$@"
            ;;
        move)
            python3 "$TOOL_ROOT/scripts/date_management/move_files_by_date.py" "$@"
            ;;
        compare)
            python3 "$TOOL_ROOT/scripts/date_management/extract_and_compare_dates.py" "$@"
            ;;
        *)
            echo "Error: Unknown dates action: $action"
            echo "Use: ./workspace.sh help dates"
            exit 1
            ;;
    esac
}

cmd_gpu() {
    local action="${1:-status}"

    case "$action" in
        status)
            echo -e "${C_BLUE}Current GPU Bindings:${C_RESET}"
            "$TOOL_ROOT/scripts/gpu_tools/gpu-bind-status.sh"
            ;;
        to-nvidia)
            if [[ $EUID -ne 0 ]]; then
                echo -e "${C_RED}Error: This action requires root privileges${C_RESET}"
                echo "Run: sudo ./workspace.sh gpu to-nvidia"
                exit 1
            fi
            echo -e "${C_YELLOW}Rebinding NVIDIA GPUs to host drivers...${C_RESET}"
            "$TOOL_ROOT/scripts/gpu_tools/gpu-to-nvidia.sh"
            ;;
        *)
            echo "Error: Unknown gpu action: $action"
            echo "Use: ./workspace.sh help gpu"
            exit 1
            ;;
    esac
}

# Command logging
log_command() {
    local logfile="$PROJECT_ROOT/logs/workspace_commands.log"
    mkdir -p "$PROJECT_ROOT/logs" 2>/dev/null || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$logfile" 2>/dev/null || true
}

# Main command dispatcher
main() {
    # Log command (but not help/info to avoid spam)
    if [[ $# -gt 0 && "$1" != "help" && "$1" != "info" && "$1" != "status" ]]; then
        log_command "$@"
    fi

    if [[ $# -eq 0 ]]; then
        show_help
        exit 0
    fi

    local command="$1"
    shift

    case "$command" in
        help|-h|--help)
            if [[ $# -eq 0 ]]; then
                show_help
            else
                show_command_help "$1"
            fi
            ;;
        download|dl)
            cmd_download "$@"
            ;;
        clips|clip)
            cmd_clips "$@"
            ;;
        dl-subs)
            cmd_dl_subs "$@"
            ;;
        voice|filter)
            cmd_voice "$@"
            ;;
        transcribe|trans)
            cmd_transcribe "$@"
            ;;
        analyze)
            cmd_analyze "$@"
            ;;
        extra-utils)
            cmd_extra_utils "$@"
            ;;
        dbupdate)
            bash "$TOOL_ROOT/scripts/db/import_videos.sh" "${1:-transcripts}"
            ;;
        convert-captions)
            cmd_convert_captions "$@"
            ;;
        stitch|concat)
            cmd_stitch "$@"
            ;;
        dates|date)
            cmd_dates "$@"
            ;;
        gpu)
            cmd_gpu "$@"
            ;;
        info|status)
            show_info
            ;;
        *)
            echo -e "${C_RED}Error: Unknown command: $command${C_RESET}"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

main "$@"
