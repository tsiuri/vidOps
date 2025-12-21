#!/usr/bin/env bash
# Concatenate MP4 files with forced constant frame rate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOL_ROOT="${TOOL_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"

export TOOL_ROOT
export PROJECT_ROOT

INDIR="${1:-${PROJECT_ROOT}/cuts_exact}"
OUTFILE="${2:-stitched_output.mp4}"
SORT_METHOD="${3:-date_timestamp}"

echo "==> Stitching videos from: $INDIR"
echo "==> Output file: $OUTFILE"
echo "==> Sort method: $SORT_METHOD"
echo "==> Forcing CFR (Constant Frame Rate) at 60fps"
echo "==> Sub-tools: scripts/utilities/sort_clips.py (date_timestamp sorting)"

# Create concat list file
CONCAT_LIST="$(mktemp --suffix=.txt)"
trap "rm -f '$CONCAT_LIST'" EXIT

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

# Build concat demuxer input file
while IFS= read -r file; do
    escaped=$(echo "$file" | sed "s/'/'\\\\''/g")
    echo "file '$escaped'" >> "$CONCAT_LIST"
done < /tmp/files.txt

FILE_COUNT=$(wc -l < "$CONCAT_LIST")
echo "==> Found $FILE_COUNT MP4 files to concatenate"

if [[ $FILE_COUNT -eq 0 ]]; then
    echo "!! No MP4 files found in $INDIR"
    exit 1
fi

echo "==> First 5 files:"
head -5 "$CONCAT_LIST"
echo "..."
echo "==> Last 5 files:"
tail -5 "$CONCAT_LIST"

read -p "==> Continue with concatenation? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "!! Aborted"
    exit 0
fi

echo "==> Re-encoding with forced 60fps CFR (this will take a while)..."

# Use fps filter to force constant frame rate + setpts to reset timestamps
ffmpeg -f concat -safe 0 -i "$CONCAT_LIST" \
    -vf "fps=60,setpts=N/FRAME_RATE/TB" \
    -c:v libx264 -preset veryfast -crf 18 \
    -c:a aac -b:a 192k \
    -vsync cfr \
    "$OUTFILE"

echo "==> Done! Output: $OUTFILE"
ls -lh "$OUTFILE"
