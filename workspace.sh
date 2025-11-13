#!/usr/bin/env bash
# workspace.sh - Unified CLI interface for all workspace tools
# Usage: ./workspace.sh <command> [options]

set -euo pipefail
#
# Ensure we're running from workspace root
WORKSPACE_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$WORKSPACE_ROOT" || exit 1

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
  transcripts <action>        Download auto-generated subtitles/captions
  voice <action>              Voice filtering operations
  transcribe [options]        Dual-GPU transcription with Whisper
  analyze <transcripts...>    AI-powered transcript analysis
  stitch <method>             Stitch videos together
  dates <action>              Date-based file management
  gpu <action>                GPU binding management (requires sudo)
  info                        Show workspace information
  help [command]              Show help for a command

EXAMPLES
  # Download a video
  ./workspace.sh download "https://youtube.com/watch?v=..."

  # Find clips with phrases
  ./workspace.sh clips hits -q "phrase1,phrase2"

  # Filter by Hasan's voice
  ./workspace.sh voice filter --clips-dir media/clips/raw

  # Stitch videos (batched method)
  ./workspace.sh stitch batched media/clips/input/ media/final/output.mp4

  # Find missing dates
  ./workspace.sh dates find-missing data/dates.txt /path/to/search

  # Show help for specific command
  ./workspace.sh help voice

For detailed help on any command, use: ./workspace.sh help <command>

LOCATION
  Workspace: $WORKSPACE_ROOT
  Scripts:   $WORKSPACE_ROOT/scripts/
  Data:      $WORKSPACE_ROOT/data/
  Results:   $WORKSPACE_ROOT/results/
  Media:     $WORKSPACE_ROOT/media/
EOF
}

show_command_help() {
    local cmd="$1"
    case "$cmd" in
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

  # Cut from local files
  ./workspace.sh clips cut-local results/wanted.tsv media/clips/raw

  # Refine clips
  ./workspace.sh clips refine -q "hasan" media/clips/raw media/clips/refined

For full clips.sh documentation, see scripts/utilities/clips.sh help
EOF
            ;;
        transcripts)
            cat <<'EOF'
TRANSCRIPTS - Download auto-generated subtitles

USAGE
  ./workspace.sh transcripts <action> [options]

ACTIONS
  subs-only <URL> [format]    Download only subtitles for a single video
  batch <file> [format]       Batch download subtitles from URL list

FORMATS
  vtt     Video Text Tracks (default, includes timestamps)
  srt     SubRip format (includes timestamps)
  json3   JSON format (structured data)

EXAMPLES
  # Download subs for a single video
  ./workspace.sh transcripts subs-only "https://www.youtube.com/watch?v=VIDEO_ID"

  # Batch download subs from a list
  ./workspace.sh transcripts batch url_list.txt vtt

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

  # Extract matching clips to text file
  ./workspace.sh voice extract results/voice_filtered/results.json

OUTPUT
  Results go to: results/voice_filtered/
  Extracted list: results/voice_filtered/hasan_clips.txt
EOF
            ;;
        stitch)
            cat <<'EOF'
STITCH - Concatenate videos

USAGE
  ./workspace.sh stitch <method> <input-dir> <output-file> [sort-method]

METHODS
  batched             Batched approach (most reliable, recommended)
  cfr                 Constant frame rate (fast)
  simple              Simple re-encode

SORT METHODS
  date_timestamp      Sort by date then timestamp (recommended)
  timestamp           Sort by timestamp only
  name                Sort alphabetically
  time                Sort by file modification time

EXAMPLES
  # Stitch with batched method (recommended)
  ./workspace.sh stitch batched \
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
  find-missing        Find which dates don't have files
  create-list         Create download list from missing dates
  move                Move files matching date list
  compare             Compare dates between sources

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
  to-vfio             Rebind NVIDIA GPUs to VM passthrough (vfio-pci)

EXAMPLES
  # Check current GPU bindings
  ./workspace.sh gpu status

  # Rebind to host (for transcoding, CUDA work, etc.)
  sudo ./workspace.sh gpu to-nvidia

  # Rebind to VM passthrough
  sudo ./workspace.sh gpu to-vfio

NOTE
  - status: No sudo required
  - to-nvidia/to-vfio: Requires sudo/root
  - Rebinding will affect running VMs and GPU applications
  - After rebinding to nvidia, you may need to restart display manager

COMMON WORKFLOW
  # Before running VM
  sudo ./workspace.sh gpu to-vfio

  # After VM shutdown, to use GPU for transcoding
  sudo ./workspace.sh gpu to-nvidia
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
  --follow / --no-follow      Live-tail logs [default: --follow]
  --setup-venvs               Create/update virtual environments (first-time setup)

EXAMPLES
  # First-time setup (creates virtual environments)
  ./workspace.sh transcribe --setup-venvs

  # Regular transcription (uses files in pull/)
  ./workspace.sh transcribe

  # Use larger model for better accuracy
  ./workspace.sh transcribe --model large

  # Force re-transcribe with SRT format
  ./workspace.sh transcribe --force --outfmt srt

  # Enable CPU worker alongside GPUs
  ENABLE_CPU=1 ./workspace.sh transcribe

INPUT
  Searches for media in: pull/ (or current dir if pull/ doesn't exist)
  Supported formats: mp4, mkv, mov, avi, mp3, wav, m4a, opus

OUTPUT
  Transcriptions go to: generated/
  Logs go to: logs/nv.log, logs/amd.log

NOTE
  Requires GPU access. For NVIDIA/AMD setup, see the script help:
  ./scripts/transcription/dual_gpu_transcribe.sh --help
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
    echo -e "${C_BLUE}Workspace Information${C_RESET}"
    echo ""
    echo "Location: $WORKSPACE_ROOT"
    echo ""
    echo "Directory Status:"

    for dir in pull cuts data results media logs config; do
        if [[ -d "$dir" ]]; then
            count=$(find "$dir" -type f 2>/dev/null | wc -l)
            echo -e "  ${C_GREEN}✓${C_RESET} $dir/ ($count files)"
        else
            echo -e "  ${C_YELLOW}○${C_RESET} $dir/ (not created)"
        fi
    done

    echo ""
    echo "Available Scripts:"
    echo "  Voice filtering:  $(find scripts/voice_filtering -name "*.py" 2>/dev/null | wc -l) scripts"
    echo "  Video processing: $(find scripts/video_processing -name "*.sh" 2>/dev/null | wc -l) scripts"
    echo "  Date management:  $(find scripts/date_management -name "*.py" 2>/dev/null | wc -l) scripts"
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
        mkdir -p logs/pull 2>/dev/null || true
        local ts lp
        ts="$(date -u '+%Y%m%d_%H%M%SZ')"
        lp="logs/pull/${ts}"
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
            ./clips.sh pull "$url" "$@"
        done < "$list_file"
    elif [[ "${1:-}" == "--retry-failed" ]]; then
        shift || true
        local list_file="${1:-}"
        # If a file path isn't provided or doesn't exist, pick the latest logs/pull/*.failed_urls.txt
        if [[ -z "$list_file" || ! -f "$list_file" ]]; then
            # pick the latest non-empty failed_urls file
            list_file=""
            for f in $(ls -1t logs/pull/*.failed_urls.txt 2>/dev/null || true); do
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
            for f in $(ls -1t logs/pull/*.failed_urls.txt 2>/dev/null || true); do
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
        mkdir -p logs/pull 2>/dev/null || true
        local ts lp
        ts="$(date -u '+%Y%m%d_%H%M%SZ')"
        lp="logs/pull/${ts}"
        export PULL_LOG_PREFIX="$lp"
        export PULL_COOKIES_FIRST=1
        rm -f "${lp}.requested.tsv" "${lp}.succeeded.tsv" "${lp}.failed.tsv" "${lp}.failed_urls.txt" 2>/dev/null || true
        while read -r url; do
            [[ -z "$url" ]] && continue
            [[ "$url" =~ ^# ]] && continue
            # Always force for retries; also forward any extra args after --retry-failed
            ./clips.sh pull "$url" --force "$@"
        done < "$list_file"
    else
        ./clips.sh pull "$@"
    fi
}

cmd_clips() {
    ./clips.sh "$@"
}

cmd_transcripts() {
    ./clips.sh transcripts "$@"
}

cmd_voice() {
    local action="${1:-}"
    shift || true

    case "$action" in
        filter)
            echo "Select voice filter method:"
            echo "  1) Simple (single-threaded)"
            echo "  2) Parallel (fast, multi-core)"
            echo "  3) Chunked (most reliable, recommended)"
            read -p "Choice [3]: " choice
            choice=${choice:-3}

            case "$choice" in
                1) python3 scripts/voice_filtering/filter_voice.py "$@" ;;
                2) python3 scripts/voice_filtering/filter_voice_parallel.py "$@" ;;
                3) python3 scripts/voice_filtering/filter_voice_parallel_chunked.py "$@" ;;
                *) echo "Invalid choice"; exit 1 ;;
            esac
            ;;
        filter-simple)
            python3 scripts/voice_filtering/filter_voice.py "$@"
            ;;
        filter-parallel)
            python3 scripts/voice_filtering/filter_voice_parallel.py "$@"
            ;;
        filter-chunked)
            python3 scripts/voice_filtering/filter_voice_parallel_chunked.py "$@"
            ;;
        extract)
            local results_file="${1:-results/voice_filtered/results.json}"
            local output_file="${2:-results/voice_filtered/matched_clips.txt}"

            echo "Extracting matching clips from: $results_file"
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
    scripts/transcription/dual_gpu_transcribe.sh "$@"
}

cmd_analyze() {
    python3 scripts/analysis/analyze_transcript.py "$@"
}

cmd_stitch() {
    local method="${1:-batched}"
    shift || true

    case "$method" in
        batched)
            scripts/video_processing/stitch_videos_batched.sh "$@"
            ;;
        cfr)
            scripts/video_processing/stitch_videos_cfr.sh "$@"
            ;;
        simple)
            scripts/video_processing/stitch_videos.sh "$@" 1
            ;;
        *)
            echo "Error: Unknown stitch method: $method"
            echo "Available: batched, cfr, simple"
            exit 1
            ;;
    esac
}

cmd_dates() {
    local action="${1:-}"
    shift || true

    case "$action" in
        find-missing)
            python3 scripts/date_management/find_missing_dates.py "$@"
            ;;
        create-list)
            python3 scripts/date_management/create_download_list.py "$@"
            ;;
        move)
            python3 scripts/date_management/move_files_by_date.py "$@"
            ;;
        compare)
            python3 scripts/date_management/extract_and_compare_dates.py "$@"
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
            scripts/gpu_tools/gpu-bind-status.sh
            ;;
        to-nvidia)
            if [[ $EUID -ne 0 ]]; then
                echo -e "${C_RED}Error: This action requires root privileges${C_RESET}"
                echo "Run: sudo ./workspace.sh gpu to-nvidia"
                exit 1
            fi
            echo -e "${C_YELLOW}Rebinding NVIDIA GPUs to host drivers...${C_RESET}"
            scripts/gpu_tools/gpu-to-nvidia.sh
            ;;
        to-vfio)
            if [[ $EUID -ne 0 ]]; then
                echo -e "${C_RED}Error: This action requires root privileges${C_RESET}"
                echo "Run: sudo ./workspace.sh gpu to-vfio"
                exit 1
            fi
            if [[ ! -f scripts/gpu_tools/gpu-to-vfio.sh ]]; then
                echo -e "${C_YELLOW}Creating gpu-to-vfio.sh...${C_RESET}"
                create_gpu_to_vfio_script
            fi
            echo -e "${C_YELLOW}Rebinding NVIDIA GPUs to vfio-pci...${C_RESET}"
            scripts/gpu_tools/gpu-to-vfio.sh
            ;;
        *)
            echo "Error: Unknown gpu action: $action"
            echo "Use: ./workspace.sh help gpu"
            exit 1
            ;;
    esac
}

create_gpu_to_vfio_script() {
    cat > scripts/gpu_tools/gpu-to-vfio.sh << 'EOFVFIO'
#!/usr/bin/env bash
# gpu-to-vfio.sh — rebind NVIDIA GPU to vfio-pci (for VM passthrough)
set -euo pipefail

log(){ printf '\033[1;34m==> %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m!!  %s\033[0m\n' "$*" >&2; }
die(){ printf '\033[1;31mxx  %s\033[0m\n' "$*" >&2; exit 1; }

require_root(){ [[ $EUID -eq 0 ]] || die "Run as root."; }

load_vfio_stack(){
  for mod in vfio vfio_pci vfio_iommu_type1; do
    if ! modprobe "$mod" >/dev/null 2>&1; then
      warn "Could not load module $mod"
      return 1
    fi
  done
  return 0
}

current_driver(){
  local bdf="$1" link="/sys/bus/pci/devices/$bdf/driver"
  if [[ -L "$link" ]]; then
    basename "$(readlink -f "$link")"
  else
    echo ""
  fi
}

discover_nvidia_functions(){
  local out=()
  for devpath in /sys/bus/pci/devices/*; do
    [[ -e "$devpath/vendor" && -e "$devpath/class" ]] || continue
    local ven devcls bdf
    ven=$(<"$devpath/vendor")
    devcls=$(<"$devpath/class")
    bdf=$(basename "$devpath")
    [[ "$ven" == "0x10de" ]] || continue
    case "$devcls" in
      0x030000|0x030200|0x040300) out+=("$bdf") ;;
    esac
  done
  printf '%s\n' "${out[@]}"
}

unbind_current_driver(){
  local bdf="$1"
  local drvlink="/sys/bus/pci/devices/$bdf/driver"
  [[ -L "$drvlink" ]] || return 0
  local drv; drv=$(basename "$(readlink -f "$drvlink")")
  if [[ -e "/sys/bus/pci/devices/$bdf/driver/unbind" ]]; then
    printf "%s" "$bdf" > "/sys/bus/pci/devices/$bdf/driver/unbind"
    log "Unbound $bdf from $drv"
  fi
}

bind_to_vfio(){
  local bdf="$1"
  local bind_path="/sys/bus/pci/drivers/vfio-pci/bind"

  load_vfio_stack || die "Failed to load vfio modules"
  [[ -e "$bind_path" ]] || die "vfio-pci driver not available"

  local cur; cur=$(current_driver "$bdf")
  if [[ "$cur" == "vfio-pci" ]]; then
    log "$bdf already bound to vfio-pci"
    return 0
  elif [[ -n "$cur" ]]; then
    unbind_current_driver "$bdf"
    udevadm settle || true
  fi

  echo "vfio-pci" > "/sys/bus/pci/devices/$bdf/driver_override"
  if printf "%s" "$bdf" > "$bind_path"; then
    log "Bound $bdf -> vfio-pci"
  else
    die "Failed to bind $bdf to vfio-pci"
  fi
  echo "" > "/sys/bus/pci/devices/$bdf/driver_override"
}

main(){
  require_root

  mapfile -t ALL < <(discover_nvidia_functions)
  [[ ${#ALL[@]} -gt 0 ]] || die "No NVIDIA functions found."

  log "Found ${#ALL[@]} NVIDIA function(s)"

  for bdf in "${ALL[@]}"; do
    bind_to_vfio "$bdf"
  done

  udevadm settle || true
  log "Done. NVIDIA GPU(s) now bound to vfio-pci for VM passthrough."
}

main "$@"
EOFVFIO
    chmod +x scripts/gpu_tools/gpu-to-vfio.sh
    log "Created scripts/gpu_tools/gpu-to-vfio.sh"
}

# Command logging
log_command() {
    local logfile="logs/workspace_commands.log"
    mkdir -p logs 2>/dev/null || true
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
        transcripts)
            cmd_transcripts "$@"
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
