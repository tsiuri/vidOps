#!/usr/bin/env bash
# shellcheck shell=bash

discover_playlist_entries(){
  local url="$1" tmpfile="$2" sleep="${PULL_DISCOVERY_SLEEP:-0}"
  yt-dlp \
    --flat-playlist \
    --dump-json \
    --sleep-requests "$sleep" \
    --sleep-interval "$sleep" \
    --max-sleep-interval "$sleep" \
    --retries 5 \
    --ignore-no-formats-error \
    --no-warnings \
    "$url" >"$tmpfile"
}

parse_entries(){
  local source="$1"
  python3 - "$source" <<'PY'
import json, sys
from urllib.parse import urlparse

path = sys.argv[1]
try:
    with open(path, 'r', encoding='utf-8') as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
except FileNotFoundError:
    lines = []

def norm_extractor(candidate, url):
    if candidate:
        return candidate.strip().lower()
    if url:
        u = url.lower()
        if 'youtube.com' in u or 'youtu.be' in u:
            return 'youtube'
        netloc = urlparse(url).netloc.lower()
        if netloc:
            return netloc.split('.')[0]
    return 'youtube'

seen = set()
for raw in lines:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        continue
    entries = data.get('entries')
    if entries is None:
        entries = [data]
    for item in entries:
        if not item:
            continue
        vid = item.get('id')
        url = item.get('webpage_url') or item.get('url')
        if not vid or not url:
            continue
        sig = (vid, url)
        if sig in seen:
            continue
        seen.add(sig)
        extractor = item.get('ie_key') or item.get('extractor') or data.get('ie_key')
        extractor = norm_extractor(extractor, url)
        title = item.get('title') or data.get('title') or ''
        title = title.replace('\t', ' ').strip()
        print(f"{vid}\t{url}\t{extractor}\t{title}")
PY
}

check_file_completeness(){
  # Check what files exist for a given video ID
  # Returns: needs_video needs_transcript needs_info
  local vid="$1"
  local has_video=0
  local has_transcript=0
  local has_info=0

  shopt -s nullglob

  # Check for video file
  for f in pull/${vid}__*.{webm,opus,m4a,mp3,mp4,mkv,mka}; do
    [[ -f "$f" ]] && has_video=1 && break
  done

  # Check for transcript (both .transcript.en.vtt and .en.vtt patterns)
  for f in pull/${vid}__*.transcript.en.vtt pull/${vid}__*.en.vtt; do
    [[ -f "$f" ]] && has_transcript=1 && break
  done

  # Check for info.json
  for f in pull/${vid}__*.info.json; do
    [[ -f "$f" ]] && has_info=1 && break
  done

  # Return: 0=needs it, 1=has it
  echo "$((1-has_video)) $((1-has_transcript)) $((1-has_info))"
}

is_transcript_unavailable(){
  # Check if a video ID is in the no-transcripts list
  local vid="$1"
  local no_trans_file="logs/no_transcripts_available.txt"
  [[ -f "$no_trans_file" ]] && grep -qxF -- "$vid" "$no_trans_file"
}

mark_transcript_unavailable(){
  # Add a video ID to the no-transcripts list
  local vid="$1"
  local no_trans_file="logs/no_transcripts_available.txt"
  mkdir -p logs 2>/dev/null || true
  # Only add if not already present
  if [[ -f "$no_trans_file" ]]; then
    grep -qxF -- "$vid" "$no_trans_file" && return 0
  fi
  echo "$vid" >> "$no_trans_file"
}

fallback_single_entry(){
  local url="$1"
  yt-dlp --dump-json --no-warnings --ignore-no-formats-error "$url" 2>/dev/null | python3 <<'PY'
import json, sys
from urllib.parse import urlparse

def norm_extractor(candidate, url):
    if candidate:
        return candidate.strip().lower()
    if url:
        u = url.lower()
        if 'youtube.com' in u or 'youtu.be' in u:
            return 'youtube'
        netloc = urlparse(url).netloc.lower()
        if netloc:
            return netloc.split('.')[0]
    return 'youtube'

raw = sys.stdin.read()
if not raw.strip():
    sys.exit(0)
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(0)
vid = data.get('id')
url = data.get('webpage_url') or data.get('original_url') or data.get('url')
if not (vid and url):
    sys.exit(0)
extractor = norm_extractor(data.get('extractor') or data.get('extractor_key'), url)
title = (data.get('title') or '').replace('\t', ' ').strip()
print(f"{vid}\t{url}\t{extractor}\t{title}")
PY
}

cmd_pull(){
  [[ $# -ge 1 ]] || { c_er "pull: need a URL"; exit 1; }
  local URL="$1"
  shift

  # Check for --force or --no-download-archive flags
  local force_download=0
  for arg in "$@"; do
    if [[ "$arg" == "--force" || "$arg" == "--no-download-archive" ]]; then
      force_download=1
      break
    fi
  done

  local archive; archive="$(clips_archive_file)"

  # Try to extract video ID directly from URL (YouTube format: v=XXXXX or youtu.be/XXXXX)
  local direct_vid_id=""
  if [[ "$URL" =~ v=([a-zA-Z0-9_-]{11}) ]]; then
    direct_vid_id="${BASH_REMATCH[1]}"
  elif [[ "$URL" =~ youtu\.be/([a-zA-Z0-9_-]{11}) ]]; then
    direct_vid_id="${BASH_REMATCH[1]}"
  fi

  # If we extracted a video ID and it's not forced, check if files are already complete
  if [[ -n "$direct_vid_id" && $force_download -eq 0 ]]; then
    read -r needs_video needs_transcript needs_info < <(check_file_completeness "$direct_vid_id")
    # If transcript is known to be unavailable, don't require it
    if is_transcript_unavailable "$direct_vid_id"; then
      needs_transcript=0
    fi
    if [[ $needs_video -eq 0 && $needs_transcript -eq 0 && $needs_info -eq 0 ]]; then
      c_ok "Files already complete for $direct_vid_id, skipping."
      return 0
    fi
  fi

  c_do "Discovering entries for: $URL"
  local tmp; tmp="$(mktemp)"
  if ! discover_playlist_entries "$URL" "$tmp"; then
    local disc_status=$?
    rm -f "$tmp"
    c_wr "Discovery failed (exit $disc_status); attempting direct fetch."
    mapfile -t ENTRIES < <(fallback_single_entry "$URL")
  else
    mapfile -t ENTRIES < <(parse_entries "$tmp")
    rm -f "$tmp"
    if [[ ${#ENTRIES[@]} -eq 0 ]]; then
      c_wr "No entries discovered via flat playlist; falling back to direct fetch."
      mapfile -t ENTRIES < <(fallback_single_entry "$URL")
    fi
  fi

  if [[ ${#ENTRIES[@]} -eq 0 ]]; then
    c_er "pull: unable to enumerate entries for $URL"
    return 1
  fi

  c_ok "Discovered ${#ENTRIES[@]} entries."

  # Skip archive sync - we'll check actual files instead
  declare -A PENDING_KEYS=()
  declare -a INCOMPLETE_ENTRIES=()  # entries: vid\turl\textractor\ttitle\tneeds_video\tneeds_transcript\tneeds_info
  local total="${#ENTRIES[@]}"
  local complete=0
  local incomplete=0

  c_do "Checking file completeness..."

  for entry in "${ENTRIES[@]}"; do
    IFS=$'\t' read -r vid entry_url extractor title <<<"$entry" || true
    [[ -n "$vid" && -n "$entry_url" ]] || continue
    local key="${extractor:-youtube} ${vid}"
    if [[ -n "${PENDING_KEYS[$key]+x}" ]]; then
      continue
    fi
    PENDING_KEYS["$key"]=1

    # Check what files exist for this video
    read -r needs_video needs_transcript needs_info < <(check_file_completeness "$vid")

    # If transcript is known to be unavailable, don't require it
    if is_transcript_unavailable "$vid"; then
      needs_transcript=0
    fi

    # If --force, re-download everything
    if [[ $force_download -eq 1 ]]; then
      needs_video=1
      needs_transcript=1
      needs_info=1
    fi

    # Check if complete (has video + all expected metadata)
    if [[ $needs_video -eq 0 && $needs_transcript -eq 0 && $needs_info -eq 0 ]]; then
      ((++complete))
    else
      ((++incomplete))
      INCOMPLETE_ENTRIES+=("$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s' "$vid" "$entry_url" "$extractor" "$title" "$needs_video" "$needs_transcript" "$needs_info")")
    fi
  done

  c_do "File check complete: $complete complete, $incomplete incomplete."

  if [[ $incomplete -eq 0 ]]; then
    c_ok "All files complete. Nothing to download. (Complete: $complete)"
    # Emit requested/ok (likely empty) and compute a convenience failed_urls list
    local TS APPEND
    TS="$(date -u '+%Y%m%d_%H%M%SZ')"
    mkdir -p logs/pull 2>/dev/null || true
    local base
    if [[ -n "${PULL_LOG_PREFIX:-}" ]]; then
      base="$PULL_LOG_PREFIX"
      APPEND=1
    else
      base="logs/pull/${TS}"
      APPEND=0
    fi
    local req_tsv="${base}.requested.tsv"
    local ok_tsv="${base}.succeeded.tsv"
    local fail_tsv="${base}.failed.tsv"
    local fail_urls="${base}.failed_urls.txt"
    # Ensure files exist so our message paths are valid
    touch "$req_tsv" "$ok_tsv" "$fail_tsv" "$fail_urls"
    # Build set of media IDs present
    declare -A HAVE_MEDIA=()
    local f bn id
    shopt -s nullglob
    for f in pull/*.opus pull/*.m4a pull/*.mp3 pull/*.mp4 pull/*.mkv pull/*.webm pull/*.mka; do
      bn="$(basename "$f")"; id="${bn%%__*}"
      [[ -n "$id" ]] && HAVE_MEDIA["$id"]=1
    done
    # For each info.json, if no media, emit as failed with URL
    local info url title extractor
    for info in pull/*.info.json; do
      bn="$(basename "$info")"; id="${bn%%__*}"
      [[ -n "$id" ]] || continue
      [[ -n "${HAVE_MEDIA[$id]:-}" ]] && continue
      # Try to get URL from src json; fallback to info.json; else construct
      url="$(src_url_for_id "$id")"
      if [[ -z "$url" ]]; then
        # fallback: try to parse webpage_url from info
        url="$(python3 - "$info" <<'PY' 2>/dev/null || true
import json,sys
try:
  d=json.load(open(sys.argv[1],'r',encoding='utf-8',errors='ignore'))
  print(d.get('webpage_url') or d.get('original_url') or '')
except Exception:
  pass
PY
)"
        [[ -z "$url" ]] && url="https://www.youtube.com/watch?v=${id}"
      fi
      printf '%s\t%s\t\t\n' "$id" "$url" >> "$fail_tsv"
      printf '%s\n' "$url" >> "$fail_urls"
    done
    c_ok "Wrote pull logs: $req_tsv, $ok_tsv, $fail_tsv, $fail_urls"
    return 0
  fi

  c_do "Need to download/complete $incomplete entries (complete: $complete / $total)."

  # Build URLs list from incomplete entries (extract just the URL for yt-dlp)
  declare -a MISSING_URLS=()
  for entry in "${INCOMPLETE_ENTRIES[@]}"; do
    IFS=$'\t' read -r vid entry_url extractor title needs_video needs_transcript needs_info <<<"$entry" || true
    MISSING_URLS+=("$entry_url")
  done

  # Remove incomplete entries from archive so yt-dlp will re-download them
  if [[ ${#INCOMPLETE_ENTRIES[@]} -gt 0 && -f "$archive" ]]; then
    local temp_archive; temp_archive="$(mktemp)"
    for entry in "${INCOMPLETE_ENTRIES[@]}"; do
      IFS=$'\t' read -r vid entry_url extractor title needs_video needs_transcript needs_info <<<"$entry" || true
      local key="${extractor:-youtube} ${vid}"
      # Remove lines matching this video ID from archive
      grep -vF "$key" "$archive" > "$temp_archive" 2>/dev/null || true
      mv "$temp_archive" "$archive"
    done
    rm -f "$temp_archive"
  fi

  # Ensure dirs and log base exist
  mkdir -p pull logs/pull 2>/dev/null || true
  local TS APPEND base
  TS="$(date -u '+%Y%m%d_%H%M%SZ')"
  if [[ -n "${PULL_LOG_PREFIX:-}" ]]; then
    base="$PULL_LOG_PREFIX"
    APPEND=1
  else
    base="logs/pull/${TS}"
    APPEND=0
  fi
  local req_tsv="${base}.requested.tsv"
  local ok_tsv="${base}.succeeded.tsv"
  local fail_tsv="${base}.failed.tsv"
  local fail_urls="${base}.failed_urls.txt"
  # Initialize requested file
  : > "$req_tsv"
  local line
  for entry in "${INCOMPLETE_ENTRIES[@]}"; do
    IFS=$'\t' read -r vid url ex title needs_video needs_transcript needs_info <<<"$entry" || true
    printf '%s\t%s\t%s\t%s\n' "$vid" "$url" "$ex" "$title" >> "$req_tsv"
  done

  local sleep_req="${YT_PULL_SLEEP_REQUESTS:-$YT_SLEEP_REQUESTS}"
  local sleep_interval="${YT_PULL_SLEEP_INTERVAL:-$YT_SLEEP_INTERVAL}"
  local sleep_max="${YT_PULL_MAX_SLEEP_INTERVAL:-$YT_MAX_SLEEP_INTERVAL}"

  # Optional cookies-first mode: discover cookies up-front and use them in the first pass
  local COOKIES_FIRST="${PULL_COOKIES_FIRST:-0}"
  COOKIE_ARG=()
  if [[ "$COOKIES_FIRST" -eq 1 ]]; then
    # Prioritize Firefox cookies unless caller already specified a preference
    if [[ -z "${COOKIE_BROWSER:-}${YTDLP_COOKIES_BROWSER:-}${COOKIE_REQUIRE:-}${COOKIE_BROWSER_ORDER:-}" ]]; then
      export COOKIE_BROWSER_ORDER="firefox chrome chromium edge brave"
      c_do "Cookies-first: prioritizing Firefox cookies"
    fi
    if declare -F clips_discover_cookies >/dev/null 2>&1; then
      clips_discover_cookies || true
    fi
  fi

  # Build args for first pass
  local -a yargs1=( yt-dlp --ignore-config -4 --no-playlist )
  # Only use archive if not in force mode
  if [[ $force_download -eq 0 ]]; then
    yargs1+=(--download-archive "$archive")
  fi
  if [[ "$COOKIES_FIRST" -eq 1 && ${#COOKIE_ARG[@]} -gt 0 ]]; then
    # cookies-first: broader client set, include cookies
    yargs1+=(
      --concurrent-fragments "${CONCURRENT_FRAGMENTS:-1}"
      --sleep-requests "$sleep_req"
      --sleep-interval "$sleep_interval"
      --max-sleep-interval "$sleep_max"
      --retries "$YT_RETRIES"
      --fragment-retries "$YT_FRAG_RETRIES"
      --extractor-retries "$YT_EXTRACTOR_RETRIES"
      --extractor-args "youtube:player_client=android,ios,web_safari,web,web_embedded,default"
      -x --audio-format opus --audio-quality 0
      --embed-metadata
      --write-info-json
      --write-auto-subs --sub-langs en
      --ignore-no-formats-error -ciw --no-overwrites
      --exec "bash scripts/utilities/mark_success.sh '%(id)s' '$base'"
      -o "pull/%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.%(ext)s"
    )
    yargs1+=( "${COOKIE_ARG[@]}" )
  else
    # default: no-cookies first
    yargs1+=(
      --no-cookies
      --concurrent-fragments "${CONCURRENT_FRAGMENTS:-1}"
      --sleep-requests "$sleep_req"
      --sleep-interval "$sleep_interval"
      --max-sleep-interval "$sleep_max"
      --retries "$YT_RETRIES"
      --fragment-retries "$YT_FRAG_RETRIES"
      --extractor-retries "$YT_EXTRACTOR_RETRIES"
      --extractor-args "youtube:player_client=android,web_embedded,default,-tv"
      -x --audio-format opus --audio-quality 0
      --embed-metadata
      --write-info-json
      --write-auto-subs --sub-langs en
      --ignore-no-formats-error -ciw --no-overwrites
      --exec "bash scripts/utilities/mark_success.sh '%(id)s' '$base'"
      -o "pull/%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.%(ext)s"
    )
  fi
  yargs1+=("${MISSING_URLS[@]}")

  if [[ "$COOKIES_FIRST" -eq 1 && ${#COOKIE_ARG[@]} -gt 0 ]]; then
    c_do "Starting yt-dlp download job (cookies-first)…"
  else
    c_do "Starting yt-dlp download job (no cookies)…"
  fi
  if ! "${yargs1[@]}"; then
    local status=$?
    c_wr "No-cookies pass failed (exit $status); attempting cookies fallback…"
    # Discover cookies and broaden client set
    COOKIE_ARG=()
    if declare -F clips_discover_cookies >/dev/null 2>&1; then
      clips_discover_cookies || true
    fi
    local -a yargs2=(
      yt-dlp --ignore-config -4 --no-playlist
    )
    # Only use archive if not in force mode
    if [[ $force_download -eq 0 ]]; then
      yargs2+=(--download-archive "$archive")
    fi
    yargs2+=(
      --concurrent-fragments "${CONCURRENT_FRAGMENTS:-1}"
      --sleep-requests "$sleep_req"
      --sleep-interval "$sleep_interval"
      --max-sleep-interval "$sleep_max"
      --retries "$YT_RETRIES"
      --fragment-retries "$YT_FRAG_RETRIES"
      --extractor-retries "$YT_EXTRACTOR_RETRIES"
      --extractor-args "youtube:player_client=android,ios,web_safari,web,web_embedded,default"
      -x --audio-format opus --audio-quality 0
      --embed-metadata
      --write-info-json
      --write-auto-subs --sub-langs en
      --ignore-no-formats-error -ciw --no-overwrites
      --exec "bash scripts/utilities/mark_success.sh '%(id)s' '$base'"
      -o "pull/%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.%(ext)s"
    )
    if [[ ${#COOKIE_ARG[@]} -gt 0 ]]; then yargs2+=( "${COOKIE_ARG[@]}" ); fi
    yargs2+=("${MISSING_URLS[@]}")
    if ! "${yargs2[@]}"; then
      local status2=$?
      c_er "yt-dlp cookies fallback failed (exit $status2)."
      return $status2
    fi
  fi
  c_ok "yt-dlp finished successfully."

  # Rename subtitle files to match transcript naming convention
  shopt -s nullglob
  for subtitle in pull/*.en.vtt; do
    [[ -f "$subtitle" ]] || continue
    # Skip if already has .transcript in name
    [[ "$subtitle" == *.transcript.en.vtt ]] && continue
    local newname="${subtitle%.en.vtt}.transcript.en.vtt"
    mv "$subtitle" "$newname" 2>/dev/null || true
  done

  # Detect and log videos that have no transcripts available
  c_do "Checking for videos without available transcripts..."
  for entry in "${INCOMPLETE_ENTRIES[@]}"; do
    IFS=$'\t' read -r vid entry_url extractor title needs_video needs_transcript needs_info <<<"$entry" || true
    # Skip if we weren't looking for a transcript for this video
    [[ "$needs_transcript" -eq 0 ]] && continue
    # Check if video was downloaded but transcript wasn't
    local has_video=0 has_transcript=0
    for f in pull/${vid}__*.{webm,opus,m4a,mp3,mp4,mkv,mka}; do
      [[ -f "$f" ]] && has_video=1 && break
    done
    for f in pull/${vid}__*.transcript.en.vtt pull/${vid}__*.en.vtt; do
      [[ -f "$f" ]] && has_transcript=1 && break
    done
    # If we have video but no transcript, mark transcript as unavailable
    if [[ $has_video -eq 1 && $has_transcript -eq 0 ]]; then
      mark_transcript_unavailable "$vid"
      c_wr "No transcript available for $vid"
    fi
  done

  # --- Post-run logging: derive failed from requested minus successful ---
  : > "$fail_tsv"; : > "$fail_urls"
  touch "$ok_tsv"
  # Compute failures: items in req_tsv but not in ok_tsv
  if [[ -s "$req_tsv" ]]; then
    # Extract video IDs from ok_tsv into a temporary file
    local ok_ids; ok_ids="$(mktemp)"
    awk -F '\t' '{print $1}' "$ok_tsv" | sort -u > "$ok_ids" 2>/dev/null || true
    # For each requested item, check if it's NOT in ok_ids
    while IFS=$'\t' read -r vid url ex title; do
      if ! grep -qxF -- "$vid" "$ok_ids"; then
        printf '%s\t%s\t%s\t%s\n' "$vid" "$url" "$ex" "$title" >> "$fail_tsv"
        printf '%s\n' "$url" >> "$fail_urls"
      fi
    done < "$req_tsv"
    rm -f "$ok_ids"
  fi
  c_ok "Wrote pull logs: $req_tsv, $ok_tsv, $fail_tsv, $fail_urls"

  # regenerate provenance for anything lacking it in pull directory
  while IFS= read -r -d '' f; do
    ensure_src_json "$f"
  done < <(find pull -type f \( -name "*.opus" -o -name "*.m4a" -o -name "*.mp3" -o -name "*.mkv" -o -name "*.mp4" \) -print0 2>/dev/null || true)

  c_do "Refreshing archive metadata..."
  if ! sync_archive_from_media "$archive"; then
    c_er "Post-download archive sync failed."
    return 1
  fi
  c_ok "Pull complete."
}
