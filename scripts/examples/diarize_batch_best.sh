#!/usr/bin/env bash
#
# Example script for batch diarization using best available transcript
#
# This script demonstrates how to enqueue diarization jobs for multiple
# ytids, automatically selecting the highest quality transcript available
# for each video.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VIDOPS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Change to vidops root directory
cd "$VIDOPS_ROOT"

echo "==================================================================="
echo "Batch Diarization with Auto Transcript Selection"
echo "==================================================================="
echo

# --- Option 1: Hard-coded list of ytids ---
echo "Option 1: Enqueue from hard-coded list"
echo "-------------------------------------------------------------------"

YTIDS=(
    "ytid1"
    "ytid2"
    "ytid3"
)

for ytid in "${YTIDS[@]}"; do
    echo "Enqueueing diarization for: $ytid"
    python vo_cli.py diarize enqueue "$ytid" \
        --transcript-kind best \
        --model resemblyzer \
        --priority 10
    echo
done

# --- Option 2: From a file (one ytid per line) ---
echo
echo "Option 2: Enqueue from file"
echo "-------------------------------------------------------------------"

# Create example file if it doesn't exist
if [[ ! -f "data/ytids_to_diarize.txt" ]]; then
    echo "Creating example file: data/ytids_to_diarize.txt"
    mkdir -p data
    cat > data/ytids_to_diarize.txt <<EOF
# ytids for diarization (one per line, comments allowed)
# ytid1
# ytid2
# ytid3
EOF
fi

echo "Reading ytids from: data/ytids_to_diarize.txt"
while IFS= read -r ytid; do
    # Skip empty lines and comments
    [[ -z "$ytid" || "$ytid" =~ ^[[:space:]]*# ]] && continue

    # Trim whitespace
    ytid=$(echo "$ytid" | xargs)
    [[ -z "$ytid" ]] && continue

    echo "Enqueueing diarization for: $ytid"
    python vo_cli.py diarize enqueue "$ytid" \
        --transcript-kind best \
        --model resemblyzer \
        --reference-dir "generated/diary_reference/$ytid" \
        --priority 10
    echo
done < data/ytids_to_diarize.txt

# --- Option 3: Query database for ytids ---
echo
echo "Option 3: Enqueue from database query"
echo "-------------------------------------------------------------------"

# Example: Get all ytids that have transcripts but no diarization
echo "Querying database for videos with transcripts but no diarization..."

# Check if psql is available
if ! command -v psql &> /dev/null; then
    echo "psql not found, skipping database option"
else
    psql -d transcripts -t -c "
        SELECT DISTINCT v.ytid
        FROM videos v
        JOIN transcripts t ON v.ytid = t.ytid
        WHERE t.kind LIKE 'words_whisper_%'
        AND NOT EXISTS (
            SELECT 1 FROM assets a
            WHERE a.ytid = v.ytid
            AND a.kind = 'diarization'
        )
        LIMIT 10;
    " 2>/dev/null | while IFS= read -r ytid; do
        ytid=$(echo "$ytid" | xargs)  # trim whitespace
        [ -z "$ytid" ] && continue

        echo "Enqueueing diarization for: $ytid"
        python vo_cli.py diarize enqueue "$ytid" \
            --transcript-kind best \
            --model resemblyzer \
            --priority 10
        echo
    done || echo "Database query failed (PostgreSQL may not be running)"
fi

echo "==================================================================="
echo "Batch diarization enqueue complete!"
echo "==================================================================="
echo
echo "To monitor job progress:"
echo "  python vo_cli.py jobs list --type diarization"
echo
echo "To start a worker:"
echo "  python vo_cli.py worker start --worker-type diarization"
echo
