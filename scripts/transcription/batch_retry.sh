#!/usr/bin/env bash
# batch_retry.sh - Process retry manifests and patch transcriptions
set -euo pipefail

# Script location and project directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_ROOT="${TOOL_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"

# Export for child scripts
export TOOL_ROOT
export PROJECT_ROOT

# Stay in project directory

# Color output
C_BLUE='\033[0;34m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'
C_RED='\033[0;31m'
C_RESET='\033[0m'

log() { echo -e "${C_BLUE}==>${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}!!${C_RESET} $*" >&2; }
error() { echo -e "${C_RED}xx${C_RESET} $*" >&2; }

########### user options (safe defaults) ###########
: "${MODEL:=medium}"  # Use larger model for retries by default
: "${LANGUAGE:=en}"
: "${MANIFEST_DIR:=${PROJECT_ROOT}/generated}"
: "${NV_VENV:=${HOME}/transcribe-nv}"
: "${DRY_RUN:=0}"
: "${MAX_MANIFESTS:=}"  # Empty = process all
: "${WORKERS:=1}"  # Number of parallel transcription workers

show_help() {
    cat <<'EOF'
batch_retry.sh - Process retry manifests and re-transcribe low-confidence segments

USAGE
  ./workspace.sh transcribe --batch-retry [options]

DESCRIPTION
  Finds all .retry_manifest.tsv files in generated/, extracts the low-confidence
  segments, re-transcribes them, and patches the results back into the VTT/SRT files.

OPTIONS
  --model <size>          Whisper model for retries (default: medium)
  --language <code>       Force language (default: en, empty=auto)
  --manifest-dir <dir>    Where to find manifests (default: generated)
  --max <N>               Process at most N manifests (for testing)
  --workers <N>           Parallel transcription workers (default: 1)
  --dry-run               Show what would be done without doing it
  --batch-retry           (This flag triggers batch retry mode)
  -h, --help              Show this help

ENV VARS
  MODEL=medium            Same as --model (larger model for better quality)
  LANGUAGE=en             Same as --language
  MANIFEST_DIR=generated  Where to find .retry_manifest.tsv files
  NV_VENV=~/transcribe-nv Path to NVIDIA venv
  DRY_RUN=0               1 for dry run

EXAMPLES
  # Process all retry manifests
  ./workspace.sh transcribe --batch-retry

  # Dry run to see what would be processed
  ./workspace.sh transcribe --batch-retry --dry-run

  # Process first 10 manifests with larger model
  ./workspace.sh transcribe --batch-retry --max 10 --model medium

  # Use 2 parallel workers for faster processing
  ./workspace.sh transcribe --batch-retry --workers 2

OUTPUT
  Updated VTT/SRT files with re-transcribed segments
  Updated confidence scores in VTT and words.tsv for retried segments
  Backup files created as *.vtt.bak, *.words.tsv.bak (only on first run)
  Processed manifests renamed to *.retry_manifest.tsv.processed

NOTE
  Requires NVIDIA GPU and faster-whisper installation
  Manifests are automatically marked as processed after successful retries
  Re-running will only process new/unprocessed manifests
  Backups are NEVER overwritten - only created if they don't exist
  Confidence scores are updated with new values from re-transcription
  To reprocess a manifest, rename it back from .tsv.processed to .tsv
EOF
    exit 0
}

########### args ###########
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="${2:-$MODEL}"; shift 2;;
    --lang|--language) LANGUAGE="${2:-}"; shift 2;;
    --manifest-dir) MANIFEST_DIR="${2:-$MANIFEST_DIR}"; shift 2;;
    --max) MAX_MANIFESTS="${2:-}"; shift 2;;
    --workers) WORKERS="${2:-1}"; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    --batch-retry) shift;;  # Consume the flag that got us here
    -h|--help) show_help;;
    *) warn "Unknown option: $1"; shift;;
  esac
done

log "Batch Retry Mode"
log "Model: ${MODEL} | Language: ${LANGUAGE:-auto} | Workers: ${WORKERS}"
log "Manifest directory: ${MANIFEST_DIR}"
[[ -n "${MAX_MANIFESTS}" ]] && log "Max manifests: ${MAX_MANIFESTS}"
[[ ${DRY_RUN} -eq 1 ]] && log "DRY RUN MODE - no changes will be made"
echo

# Check for required tools
if ! command -v python3 >/dev/null 2>&1; then
    error "python3 not found"
    exit 1
fi

# Check for NVIDIA venv
if [[ ! -d "$NV_VENV" ]]; then
    error "NVIDIA venv not found: $NV_VENV"
    error "Run: ./workspace.sh transcribe --setup-venvs"
    exit 1
fi

# Find retry manifests (exclude .processed)
log "Searching for retry manifests in ${MANIFEST_DIR}..."
mapfile -t ALL_MANIFESTS < <(find "$MANIFEST_DIR" -name "*.retry_manifest.tsv" -type f 2>/dev/null | sort)
MANIFESTS=()
for m in "${ALL_MANIFESTS[@]}"; do
    # Skip if already processed
    if [[ -f "${m}.processed" ]]; then
        continue
    fi
    MANIFESTS+=("$m")
done

if [[ ${#MANIFESTS[@]} -eq 0 ]]; then
    PROCESSED_COUNT=$(find "$MANIFEST_DIR" -name "*.retry_manifest.tsv.processed" -type f 2>/dev/null | wc -l)
    if [[ $PROCESSED_COUNT -gt 0 ]]; then
        log "No unprocessed retry manifests found (${PROCESSED_COUNT} already processed)"
    else
        warn "No retry manifests found in ${MANIFEST_DIR}"
    fi
    exit 0
fi

log "Found ${#MANIFESTS[@]} unprocessed retry manifests"

# Limit if requested
if [[ -n "${MAX_MANIFESTS}" ]]; then
    MANIFESTS=("${MANIFESTS[@]:0:${MAX_MANIFESTS}}")
    log "Limited to ${#MANIFESTS[@]} manifests"
fi

# Export vars for Python script
export MODEL LANGUAGE NV_VENV DRY_RUN PROJECT_ROOT WORKERS

# Call Python script to do the heavy lifting
log "Processing manifests..."
"$NV_VENV/bin/python3" "$SCRIPT_DIR/batch_retry_worker.py" "${MANIFESTS[@]}"

log "Batch retry complete!"
