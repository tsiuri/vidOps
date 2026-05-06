#!/usr/bin/env bash
set -euo pipefail

LIST_FILE="$1"
OUT="$2"

# Read raw absolute paths per line (no quoting)
mapfile -t inputs < "$LIST_FILE"

input_args=()
for f in "${inputs[@]}"; do
  input_args+=( -i "$f" )
done

n=${#inputs[@]}
chains=""
for ((i=0; i<n; i++)); do
  chains+="[$i:v][$i:a]"
done

# Allow custom FPS and audio resample via env
FPS_EXPR="${FPS_EXPR:-fps=60}"
ARESAMPLE_EXPR="${ARESAMPLE_EXPR:-aresample=async=1:first_pts=0}"
filter="${chains}concat=n=${n}:v=1:a=1[v][a];[v]${FPS_EXPR},settb=AVTB,setpts=PTS-STARTPTS[v2];[a]${ARESAMPLE_EXPR}[a2]"

ffmpeg -hide_banner -loglevel error -stats \
  "${input_args[@]}" \
  -filter_complex "$filter" \
  -map "[v2]" -map "[a2]" \
  -c:v libx264 -preset veryfast -crf 18 \
  -c:a aac -b:a 192k \
  ${VSYNC_ARGS:- -vsync cfr -r 60} -movflags +faststart \
  "$OUT"
