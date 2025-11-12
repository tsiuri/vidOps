#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./pull.sh <youtube-url> [more-urls...]
#   ./pull.sh -a urls.txt
#
# Tuning (override via env if needed):
: "${SLEEP_REQUESTS:=2}"        # seconds between HTTP requests (per yt-dlp)
: "${SLEEP_INTERVAL:=1}"        # min seconds between downloads
: "${MAX_SLEEP_INTERVAL:=5}"    # max seconds between downloads (randomized)
: "${RETRIES:=15}"              # overall retries for network errors
: "${FRAG_RETRIES:=20}"         # retries for a single fragment
: "${EXTRACTOR_RETRIES:=10}"    # retries for metadata/extractor failures
: "${CONCURRENT_FRAG:=1}"       # 1 avoids YouTube anti-abuse triggers
: "${ARCHIVE_FILE:=downloaded.txt}"  # optional: comment out to disable

print_usage() {
  cat <<'EOF'
pull.sh — yt-dlp wrapper with polite sleeps & retries

USAGE:
  pull.sh <youtube-url> [more-urls...]
  pull.sh -a urls.txt

ENV TUNABLES (defaults):
  SLEEP_REQUESTS=2     SLEEP_INTERVAL=1   MAX_SLEEP_INTERVAL=5
  RETRIES=15           FRAG_RETRIES=20    EXTRACTOR_RETRIES=10
  CONCURRENT_FRAG=1    ARCHIVE_FILE=downloaded.txt

Examples:
  ./pull.sh https://www.youtube.com/watch?v=dQw4w9WgXcQ
  ./pull.sh -a vods.txt
  SLEEP_REQUESTS=3 MAX_SLEEP_INTERVAL=8 ./pull.sh https://youtube.com/playlist?list=...
EOF
}

URL_ARGS=()
INPUT_FILE=""
if [[ $# -eq 0 ]]; then
  print_usage; exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) print_usage; exit 0;;
    -a|--batch-file)
      shift
      INPUT_FILE="${1:-}"; [[ -n "$INPUT_FILE" ]] || { echo "Missing file after -a" >&2; exit 1; }
      [[ -r "$INPUT_FILE" ]] || { echo "Cannot read: $INPUT_FILE" >&2; exit 1; }
      shift
      ;;
    *)
      URL_ARGS+=("$1"); shift;;
  esac
done

# Common arguments
COMMON_ARGS=(
  --concurrent-fragments "$CONCURRENT_FRAG"
  --no-cookies
  --extractor-args "youtube:player_client=android,web_embedded,default,-tv"
  -x --audio-format opus --audio-quality 0
  --embed-metadata
  --write-info-json
  --ignore-no-formats-error -ciw --no-overwrites
  --sleep-requests "$SLEEP_REQUESTS"
  --sleep-interval "$SLEEP_INTERVAL"
  --max-sleep-interval "$MAX_SLEEP_INTERVAL"
  --retries "$RETRIES"
  --fragment-retries "$FRAG_RETRIES"
  --extractor-retries "$EXTRACTOR_RETRIES"
  -o "%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.%(ext)s"
)

# Add download archive unless explicitly disabled
if [[ -n "${ARCHIVE_FILE:-}" ]]; then
  COMMON_ARGS+=( --download-archive "$ARCHIVE_FILE" )
fi

# Build final command
if [[ -n "$INPUT_FILE" ]]; then
  CMD=( yt-dlp "${COMMON_ARGS[@]}" -a "$INPUT_FILE" )
else
  CMD=( yt-dlp "${COMMON_ARGS[@]}" "${URL_ARGS[@]}" )
fi

# Bash-level retry/backoff guard in case the process exits non-zero anyway.
# Won't overwrite because --no-overwrites + archive (if used).
max_attempts=5
attempt=1
until "${CMD[@]}"; do
  rc=$?
  if (( attempt >= max_attempts )); then
    echo "yt-dlp failed after ${attempt} attempts (rc=$rc)." >&2
    exit "$rc"
  fi
  # Exponential-ish backoff with jitter
  sleep_for=$(( attempt * 60 ))
  echo "Retrying in ~${sleep_for}s (attempt $((attempt+1))/${max_attempts})…" >&2
  sleep "$sleep_for"
  attempt=$((attempt+1))
done
