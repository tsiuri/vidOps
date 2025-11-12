#!/usr/bin/env bash
# Concatenate MP4 files using concat FILTER (most reliable, slower)

set -euo pipefail

INDIR="${1:-cuts_exact}"
OUTFILE="${2:-stitched_output.mp4}"
SORT_METHOD="${3:-date_timestamp}"

echo "==> Stitching videos from: $INDIR"
echo "==> Output file: $OUTFILE"
echo "==> Sort method: $SORT_METHOD"
echo "==> Using concat FILTER (most reliable method)"

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
        find "$(realpath "$INDIR")" -name "*.mp4" -type f | python3 /mnt/firecuda/Videos/yt-videos/collargate/yt-downloader-test/the 2021 2021 vods bak folder b4 cleanup/scripts/utilities/sort_clips.py > /tmp/files.txt
        ;;
    *)
        echo "Unknown sort method: $SORT_METHOD"
        exit 1
        ;;
esac

FILE_COUNT=$(wc -l < /tmp/files.txt)
echo "==> Found $FILE_COUNT MP4 files to concatenate"

if [[ $FILE_COUNT -eq 0 ]]; then
    echo "!! No MP4 files found in $INDIR"
    exit 1
fi

echo "==> First 5 files:"
head -5 /tmp/files.txt
echo "..."
echo "==> Last 5 files:"
tail -5 /tmp/files.txt

read -p "==> Continue with concatenation? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "!! Aborted"
    exit 0
fi

echo "==> Building ffmpeg concat filter command..."

# Build the complex filter for concat
FILTER_COMPLEX=""
INPUT_ARGS=""
i=0

while IFS= read -r file; do
    INPUT_ARGS="$INPUT_ARGS -i $(printf '%q' "$file")"
    FILTER_COMPLEX="${FILTER_COMPLEX}[$i:v][$i:a]"
    ((i++))
done < /tmp/files.txt

FILTER_COMPLEX="${FILTER_COMPLEX}concat=n=$FILE_COUNT:v=1:a=1[outv][outa]"

echo "==> Concatenating $FILE_COUNT files (this will take a while)..."
echo "==> Progress will show below..."

# Use eval to properly handle the input args
eval ffmpeg -hide_banner $INPUT_ARGS \
    -filter_complex "$FILTER_COMPLEX" \
    -map "[outv]" -map "[outa]" \
    -c:v libx264 -preset veryfast -crf 18 \
    -c:a aac -b:a 192k \
    "$OUTFILE"

echo "==> Done! Output: $OUTFILE"
ls -lh "$OUTFILE"
