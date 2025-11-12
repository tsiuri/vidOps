#!/usr/bin/env bash
set -euo pipefail

# Locate and source shared helpers to reuse cookie logic
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -r "$SCRIPT_DIR/clips/common.sh" ]]; then
  # shellcheck source=clips/common.sh
  source "$SCRIPT_DIR/clips/common.sh"
fi

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
  --ignore-config -4 --no-playlist
  --ignore-no-formats-error -ciw --no-overwrites
  --sleep-requests "$SLEEP_REQUESTS"
  --sleep-interval "$SLEEP_INTERVAL"
  --max-sleep-interval "$MAX_SLEEP_INTERVAL"
  --retries "$RETRIES"
  --fragment-retries "$FRAG_RETRIES"
  --extractor-retries "$EXTRACTOR_RETRIES"
  --embed-metadata
  --write-info-json
  -o "%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.%(ext)s"
)

# Add download archive unless explicitly disabled
if [[ -n "${ARCHIVE_FILE:-}" ]]; then
  COMMON_ARGS+=( --download-archive "$ARCHIVE_FILE" )
fi

# Build two-pass command: 1) no-cookies with pull defaults; 2) with cookies if needed
build_args_no_cookies(){
  local -a a=( yt-dlp "${COMMON_ARGS[@]}" --no-cookies --extractor-args "youtube:player_client=android,web_embedded,default,-tv" -x --audio-format opus --audio-quality 0 )
  if [[ -n "${ARCHIVE_FILE:-}" ]]; then a+=( --download-archive "$ARCHIVE_FILE" ); fi
  if [[ -n "$INPUT_FILE" ]]; then a+=( -a "$INPUT_FILE" ); else a+=( "${URL_ARGS[@]}" ); fi
  printf '%s\n' "${a[@]}"
}

build_args_with_cookies(){
  local -a b=( yt-dlp "${COMMON_ARGS[@]}" )
  # Discover cookies (Firefox-first) only now
  COOKIE_ARG=()
  if declare -F clips_discover_cookies >/dev/null 2>&1; then
    clips_discover_cookies || true
  fi
  if [[ ${#COOKIE_ARG[@]} -gt 0 ]]; then b+=( "${COOKIE_ARG[@]}" ); fi
  b+=( --extractor-args "youtube:player_client=android,ios,web_safari,web,web_embedded,default" -x --audio-format opus --audio-quality 0 )
  if [[ -n "${ARCHIVE_FILE:-}" ]]; then b+=( --download-archive "$ARCHIVE_FILE" ); fi
  if [[ -n "$INPUT_FILE" ]]; then b+=( -a "$INPUT_FILE" ); else b+=( "${URL_ARGS[@]}" ); fi
  printf '%s\n' "${b[@]}"
}

# Run pass 1
mapfile -t CMD1 < <(build_args_no_cookies)
if ! "${CMD1[@]}"; then
  rc=$?
  echo "No-cookies pass failed (rc=$rc); retrying with cookies…" >&2
  mapfile -t CMD2 < <(build_args_with_cookies)
  if ! "${CMD2[@]}"; then
    rc2=$?
    echo "Cookies pass failed (rc=$rc2)." >&2
    exit "$rc2"
  fi
fi
