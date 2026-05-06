#!/bin/bash
# transcripts.sh — Download auto-generated captions/transcripts from YouTube videos

cmd_transcripts_subs_only(){
  [[ $# -ge 1 ]] || { c_er "transcripts subs-only: need a URL"; exit 1; }
  local URL="$1"
  shift || true

  local format="${1:-vtt}"  # Default to VTT (can also be srt, json3, etc)

  c_do "Downloading auto-generated subtitles from: $URL"

  # Ensure pull dir exists
  mkdir -p pull 2>/dev/null || true

  # Use yt-dlp to download subs only (no video)
  # Match naming scheme from pull.sh: {id}__{upload_date} - {title}.transcript.{lang}.{format}
  if yt-dlp \
    --write-auto-subs \
    --sub-langs en \
    --sub-format "$format" \
    --skip-download \
    --ignore-no-formats-error \
    --no-warnings \
    -o "pull/%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.transcript" \
    "$URL"; then
    c_ok "Subtitles downloaded successfully"
    return 0
  else
    local status=$?
    c_er "Failed to download subtitles (exit $status)"
    return $status
  fi
}

cmd_transcripts_batch(){
  [[ $# -ge 1 ]] || { c_er "transcripts batch: need a file path"; exit 1; }
  local list_file="$1"
  shift || true

  [[ -f "$list_file" ]] || { c_er "File not found: $list_file"; exit 1; }

  local format="${1:-vtt}"
  c_do "Batch downloading subtitles from: $list_file (format: $format)"

  mkdir -p pull 2>/dev/null || true

  local count=0
  local failed=0

  while read -r url; do
    [[ -z "$url" ]] && continue
    [[ "$url" =~ ^# ]] && continue

    ((++count))
    printf '\n'
    c_do "[$count] Processing: $url"

    # Match naming scheme from pull.sh: {id}__{upload_date} - {title}.transcript.{lang}.{format}
    if ! yt-dlp \
      --write-auto-subs \
      --sub-langs en \
      --sub-format "$format" \
      --skip-download \
      --ignore-no-formats-error \
      --no-warnings \
      -o "pull/%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.transcript" \
      "$url"; then
      ((++failed))
      c_wr "  → Failed (will continue with next)"
    else
      c_ok "  → Success"
    fi
  done < "$list_file"

  printf '\n'
  c_ok "Batch complete: $count processed, $failed failed"

  [[ $failed -eq 0 ]]
}

cmd_transcripts(){
  local action="${1:-}"
  shift || true

  case "$action" in
    subs-only)
      cmd_transcripts_subs_only "$@"
      ;;
    batch)
      cmd_transcripts_batch "$@"
      ;;
    help)
      cat <<'HELP'
dl-subs — Download auto-generated subtitles from YouTube videos

USAGE
  ./clips.sh transcripts subs-only <URL> [format]
  ./clips.sh transcripts batch <url-list.txt> [format]

FORMATS
  vtt     Video Text Tracks (default, includes timestamps)
  srt     SubRip format (includes timestamps)
  json3   JSON format (structured data with timestamps)

EXAMPLES
  # Download subs for a single video
  ./clips.sh transcripts subs-only "https://www.youtube.com/watch?v=Bldyb2JvaF0"

  # Batch download subs from a list
  ./clips.sh transcripts batch url_list.txt vtt

OUTPUT
  Subtitles saved to: pull/
  Filename format: {video_id}__{upload_date} - {title}.transcript.{lang}.{format}
  (Matches naming scheme of video files)
  Example: Bldyb2JvaF0__2019-05-21 - Title Here.transcript.en.vtt

HELP
      ;;
    *)
      c_er "Unknown transcripts action: $action"
      echo "Use: ./clips.sh transcripts help"
      exit 1
      ;;
  esac
}
