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

# Verify project marker; warn but do not prompt/initialize (non-interactive workers must not block)
# Accept either legacy marker (.vidops-project) or new marker (.vidops_deploy_marker)
if [[ ! -f "$PROJECT_ROOT/.vidops-project" && ! -f "$PROJECT_ROOT/.vidops_deploy_marker" ]]; then
    echo -e "\033[1;33m[warn] VidOps project marker missing at $PROJECT_ROOT; continuing without auto-init.\033[0m"
    echo "Set VIDOPS_PROJECT_ROOT to your data workspace or touch .vidops_deploy_marker to silence this warning."
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

COMMANDS (legacy bash bridges — most operator-facing flows are now in `vo` CLI)
  analyze <transcripts...>    AI-powered transcript analysis (Ollama bridge)
  extra-utils <tool|help>     Run extra utilities (see EXTRA_UTILS.md)
  dbupdate [database]         Full DB ingest/update pipeline (prompts)
  gpu <action>                GPU binding management (to-nvidia requires sudo)
  info                        Show workspace information
  help [command]              Show help for a command

The download / clips / cuts / transcribe / diarize / dl-subs / voice / stitch /
dates / convert-captions subcommands have been removed from this script —
they are now native Python implementations reachable via `vo <subcommand>`.
See README.md for the equivalents.

EXAMPLES
  # AI transcript analysis (still bridged from this script)
  ./workspace.sh analyze pull/*.transcript.en.vtt

  # Run an extra utility
  ./workspace.sh extra-utils map_ids_to_files.py

  # Show GPU binding status
  ./workspace.sh gpu status

  # Show help for specific command
  ./workspace.sh help analyze

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
  Standalone scripts (see EXTRA_UTILS.md, e.g., ~/bq_netservices/vidops/EXTRA_UTILS.md):
    scripts/utilities/mark_success.sh
    scripts/utilities/quality_report.py
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
  ./workspace.sh extra-utils map_ids_to_files.py
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

cmd_analyze() {
    python3 "$TOOL_ROOT/scripts/analysis/analyze_transcript.py" "$@"
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
        analyze)
            cmd_analyze "$@"
            ;;
        extra-utils)
            cmd_extra_utils "$@"
            ;;
        dbupdate)
            bash "$TOOL_ROOT/scripts/db/import_videos.sh" "${1:-transcripts}"
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
