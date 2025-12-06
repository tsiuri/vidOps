#!/usr/bin/env bash
#
# run_diarization.sh - Unified shell wrapper for diarization pipeline
# Part of VidOps Diarization 2.0
#
# Usage:
#   run_diarization.sh single <ytid> [--reference <name>]
#   run_diarization.sh batch <ytids_file> [--reference <name>]
#   run_diarization.sh build-reference <reference_name> <video1> [video2...]
#   run_diarization.sh match <ytid> <reference_name>
#

set -euo pipefail

# Detect tool and project roots
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_ROOT="${TOOL_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"

export TOOL_ROOT
export PROJECT_ROOT

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
info() {
    echo -e "${BLUE}9  $*${NC}"
}

success() {
    echo -e "${GREEN} $*${NC}"
}

warning() {
    echo -e "${YELLOW}   $*${NC}" >&2
}

error() {
    echo -e "${RED}L Error: $*${NC}" >&2
}

# Check if in VidOps project
check_project() {
    if [[ ! -f "$PROJECT_ROOT/.vidops-project" ]]; then
        warning "Not in a VidOps project directory"
        info "Run from a project directory or set PROJECT_ROOT"
        return 1
    fi
    return 0
}

# Initialize project directories if needed
init_project_dirs() {
    mkdir -p "$PROJECT_ROOT"/{data/references,results/diarization,logs/diarization,generated/diarization_inputs}
}

# Find audio file for YTID
find_audio() {
    local ytid="$1"

    # Try preprocessed audio first
    local enhanced="$PROJECT_ROOT/generated/diarization_inputs/$ytid/enhanced.wav"
    if [[ -f "$enhanced" ]]; then
        echo "$enhanced"
        return 0
    fi

    local canonical="$PROJECT_ROOT/generated/diarization_inputs/$ytid/canonical.wav"
    if [[ -f "$canonical" ]]; then
        echo "$canonical"
        return 0
    fi

    # Fallback to pull directory
    local pull_dir="$PROJECT_ROOT/pull"
    if [[ -d "$pull_dir" ]]; then
        # Try common patterns
        local patterns=(
            "${ytid}__*.mp4"
            "${ytid}__*.webm"
            "${ytid}__*.mkv"
            "${ytid}__*.opus"
            "${ytid}.mp4"
            "${ytid}.webm"
        )

        for pattern in "${patterns[@]}"; do
            local matches=("$pull_dir"/$pattern)
            if [[ -f "${matches[0]}" ]]; then
                echo "${matches[0]}"
                return 0
            fi
        done
    fi

    return 1
}

# Command: single <ytid> [--reference <name>]
cmd_single() {
    local ytid="$1"
    shift

    local reference_name=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --reference)
                reference_name="$2"
                shift 2
                ;;
            *)
                error "Unknown option: $1"
                return 1
                ;;
        esac
    done

    info "Diarizing single file: $ytid"

    # Find audio
    local audio_path
    if ! audio_path=$(find_audio "$ytid"); then
        error "Audio file not found for: $ytid"
        info "Searched in:"
        info "  - generated/diarization_inputs/$ytid/{enhanced,canonical}.wav"
        info "  - pull/${ytid}__*.{mp4,webm,mkv,opus}"
        return 1
    fi

    info "Audio: $audio_path"

    # Create output directory
    local output_dir="$PROJECT_ROOT/results/diarization"
    mkdir -p "$output_dir"

    # Run inference
    info "Running diarization inference..."
    python3 "$TOOL_ROOT/scripts/diarization/diarize_inference.py" \
        "$audio_path" \
        "$ytid" \
        "$output_dir"

    # Match to reference if provided
    if [[ -n "$reference_name" ]]; then
        info "Matching to reference: $reference_name"

        local ref_dir="$PROJECT_ROOT/data/references/$reference_name"
        if [[ ! -d "$ref_dir" ]]; then
            error "Reference not found: $ref_dir"
            return 1
        fi

        python3 "$TOOL_ROOT/scripts/diarization/match_reference.py" \
            "$output_dir/$ytid" \
            "$ref_dir"
    fi

    success "Diarization complete: $output_dir/$ytid"
}

# Command: batch <ytids_file> [--reference <name>]
cmd_batch() {
    local ytids_file="$1"
    shift

    if [[ ! -f "$ytids_file" ]]; then
        error "YTIDs file not found: $ytids_file"
        return 1
    fi

    local reference_name=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --reference)
                reference_name="$2"
                shift 2
                ;;
            *)
                error "Unknown option: $1"
                return 1
                ;;
        esac
    done

    info "Batch diarization from: $ytids_file"

    local output_dir="$PROJECT_ROOT/results/diarization"
    mkdir -p "$output_dir"

    local args=("$ytids_file" "$output_dir")

    if [[ -n "$reference_name" ]]; then
        args+=(--reference "$reference_name")
    fi

    python3 "$TOOL_ROOT/scripts/diarization/batch_diarize.py" "${args[@]}"
}

# Command: build-reference <reference_name> <video1> [video2...]
cmd_build_reference() {
    local reference_name="$1"
    shift

    if [[ $# -eq 0 ]]; then
        error "No source videos provided"
        info "Usage: run_diarization.sh build-reference <name> <video1> [video2...]"
        return 1
    fi

    info "Building reference: $reference_name"
    info "Source videos: $# files"

    python3 "$TOOL_ROOT/scripts/diarization/build_reference.py" \
        "$reference_name" \
        "$@" \
        --output-dir "$PROJECT_ROOT/data/references"

    success "Reference built: $PROJECT_ROOT/data/references/$reference_name"
}

# Command: match <ytid> <reference_name>
cmd_match() {
    local ytid="$1"
    local reference_name="$2"

    local diar_dir="$PROJECT_ROOT/results/diarization/$ytid"
    if [[ ! -d "$diar_dir" ]]; then
        error "Diarization results not found for: $ytid"
        info "Run diarization first: run_diarization.sh single $ytid"
        return 1
    fi

    local ref_dir="$PROJECT_ROOT/data/references/$reference_name"
    if [[ ! -d "$ref_dir" ]]; then
        error "Reference not found: $reference_name"
        info "Build reference first: run_diarization.sh build-reference $reference_name ..."
        return 1
    fi

    info "Matching $ytid to reference: $reference_name"

    python3 "$TOOL_ROOT/scripts/diarization/match_reference.py" \
        "$diar_dir" \
        "$ref_dir"

    success "Matching complete: $diar_dir/diarized_timestamps_matched.tsv"
}

# Help
show_help() {
    cat <<EOF
run_diarization.sh - VidOps Diarization 2.0 Shell Wrapper

Usage:
  run_diarization.sh <command> [args...]

Commands:
  single <ytid> [--reference <name>]
      Diarize a single video
      Example: run_diarization.sh single abc123xyz --reference speaker_alpha

  batch <ytids_file> [--reference <name>]
      Batch diarize multiple videos from a list
      Example: run_diarization.sh batch ytids.txt --reference speaker_alpha

  build-reference <reference_name> <video1> [video2...]
      Build a speaker reference from source videos
      Example: run_diarization.sh build-reference speaker_alpha video1.mp4 video2.mp4

  match <ytid> <reference_name>
      Match existing diarization to reference
      Example: run_diarization.sh match abc123xyz speaker_alpha

  help
      Show this help message

Environment Variables:
  PROJECT_ROOT  - Project directory (default: current directory)
  TOOL_ROOT     - VidOps installation directory (auto-detected)

Examples:
  # Diarize a single video
  ./run_diarization.sh single abc123xyz

  # Diarize with speaker matching
  ./run_diarization.sh single abc123xyz --reference my_speaker

  # Batch process
  ./run_diarization.sh batch pikerbreakdown_ytids.tsv --reference speaker_alpha

  # Build a new reference
  ./run_diarization.sh build-reference speaker_alpha *.mp4

Configuration:
  Edit config/diarization.yaml to adjust hyperparameters

Logs:
  Check logs/diarization/ for detailed logs
EOF
}

# Main
main() {
    if [[ $# -eq 0 ]]; then
        show_help
        return 1
    fi

    # Initialize if needed
    init_project_dirs

    local command="$1"
    shift

    case "$command" in
        single)
            cmd_single "$@"
            ;;
        batch)
            cmd_batch "$@"
            ;;
        build-reference)
            cmd_build_reference "$@"
            ;;
        match)
            cmd_match "$@"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            error "Unknown command: $command"
            echo ""
            show_help
            return 1
            ;;
    esac
}

main "$@"
