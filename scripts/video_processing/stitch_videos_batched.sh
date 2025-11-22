#!/usr/bin/env bash
# Concatenate MP4 files in batches, then merge batches

set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_ROOT="${TOOL_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"

export TOOL_ROOT
export PROJECT_ROOT

INDIR="${1:-${PROJECT_ROOT}/cuts_exact}"
OUTFILE="${2:-stitched_output.mp4}"
SORT_METHOD="${3:-date_timestamp}"
# Allow overriding batch size via env var
BATCH_SIZE=${BATCH_SIZE:-100}

echo "==> Stitching videos from: $INDIR"
echo "==> Output file: $OUTFILE"
echo "==> Sort method: $SORT_METHOD"
echo "==> Using batched approach (most reliable)"
echo "==> Sub-tools: scripts/utilities/sort_clips.py (date_timestamp sorting)"

echo "==> Building file list..."

case "$SORT_METHOD" in
    name)
        find "$(realpath "$INDIR")" -name "*.mp4" -type f | sort > /tmp/files.txt
        ;;
    time)
        find "$(realpath "$INDIR")" -name "*.mp4" -type f -printf "%T@ %p\n" | sort -n | cut -d' ' -f2- > /tmp/files.txt
        ;;
    timestamp)
        find "$(realpath "$INDIR")" -name "*.mp4" -type f | \
            awk '{
                match($0, /_([0-9]+\.[0-9]+)-([0-9]+\.[0-9]+)\.mp4$/, arr);
                if (arr[1] != "") print arr[1], $0;
            }' | sort -n | cut -d' ' -f2- > /tmp/files.txt
        ;;
    date_timestamp)
        echo "==> Using sort_clips.py for date+timestamp ordering"
        find "$(realpath "$INDIR")" -name "*.mp4" -type f | python3 "${TOOL_ROOT}/scripts/utilities/sort_clips.py" > /tmp/files.txt
        ;;
    *)
        echo "Unknown sort method: $SORT_METHOD"
        exit 1
        ;;
esac

FILE_COUNT=$(wc -l < /tmp/files.txt)
echo "==> Found $FILE_COUNT MP4 files"
BATCH_COUNT=$(( (FILE_COUNT + BATCH_SIZE - 1) / BATCH_SIZE ))
echo "==> Will process in $BATCH_COUNT batches of $BATCH_SIZE"

if [[ $FILE_COUNT -eq 0 ]]; then
    echo "!! No MP4 files found in $INDIR"
    exit 1
fi

echo "==> First 5 files:"
head -5 /tmp/files.txt
echo "..."
echo "==> Last 5 files:"
tail -5 /tmp/files.txt
echo ""

# Create temp directory for batch outputs
BATCH_DIR=$(mktemp -d)
# Allow keeping batch directory for debugging by setting KEEP_BATCH=1
if [[ "${KEEP_BATCH:-0}" == "1" ]]; then
  echo "==> KEEP_BATCH=1; batch dir: $BATCH_DIR (will not be removed)"
  trap ':' EXIT
else
  trap "rm -rf '$BATCH_DIR'" EXIT
fi

echo "==> Stage 1: Concatenating batches..."

batch_num=0
set +e
while IFS= read -r file; do
    batch_idx=$((batch_num / BATCH_SIZE))

    # Escape single quotes in filename
    escaped=$(echo "$file" | sed "s/'/'\\\\''/g")
    echo "file '$escaped'" >> "$BATCH_DIR/batch_${batch_idx}.txt"

    ((batch_num++))
done < /tmp/files.txt
set -e

# If DRY_RUN=1, stop after creating batch lists
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "==> DRY_RUN=1; created batch list files in $BATCH_DIR"
  ls -lh "$BATCH_DIR"/batch_*.txt | head -n 5
  exit 0
fi

# Sanity check: ensure batch lists were created
batch_lists=("$BATCH_DIR"/batch_*.txt)
echo "  Created ${#batch_lists[@]} batch list files in $BATCH_DIR"

# Concatenate each batch with re-encoding and forced CFR
for batch_file in "$BATCH_DIR"/batch_*.txt; do
    batch_name=$(basename "$batch_file" .txt)
    batch_output="$BATCH_DIR/${batch_name}.mp4"

    echo "  Processing $batch_name ($(wc -l < "$batch_file") files)..."

    ffmpeg -hide_banner -loglevel error -stats \
        -fflags +genpts \
        -f concat -safe 0 -i "$batch_file" \
        -vf "settb=AVTB,setpts=PTS-STARTPTS,fps=60" \
        -af "aresample=async=1:first_pts=0" \
        -c:v libx264 -preset veryfast -crf 18 \
        -c:a aac -b:a 192k \
        -vsync cfr -r 60 \
        -movflags +faststart \
        "$batch_output"
done

echo "==> Stage 2: Merging batches into final output..."

# Allow stopping after stage 1 for debugging
if [[ "${STOP_AFTER_STAGE1:-0}" == "1" ]]; then
    echo "==> STOP_AFTER_STAGE1=1 set; stopping before merge. Batch dir: $BATCH_DIR"
    ls -lh "$BATCH_DIR"/*.mp4 || true
    exit 0
fi

# Create list of batch outputs
FINAL_LIST="$BATCH_DIR/final.txt"
for batch_output in "$BATCH_DIR"/batch_*.mp4; do
    escaped=$(echo "$batch_output" | sed "s/'/'\\\\''/g")
    echo "file '$escaped'" >> "$FINAL_LIST"
done
# Sort the final list by batch number
sort -V "$FINAL_LIST" -o "$FINAL_LIST"

# Final concatenation with stream copy (batches already properly encoded)
ffmpeg -hide_banner -loglevel info \
    -f concat -safe 0 -i "$FINAL_LIST" \
    -c copy \
    "$OUTFILE"

echo "==> Done! Output: $OUTFILE"
ls -lh "$OUTFILE"
