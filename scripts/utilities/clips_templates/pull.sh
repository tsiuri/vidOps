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
        url = item.get('url') or item.get('webpage_url')
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
url = data.get('original_url') or data.get('webpage_url') or data.get('url')
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
  c_do "Syncing download archive: $archive"
  if ! sync_archive_from_media "$archive"; then
    c_er "Initial archive sync failed."
    return 1
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

  declare -A ARCHIVE_SEEN=()
  if [[ -f "$archive" ]]; then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      ARCHIVE_SEEN["$line"]=1
    done < "$archive"
  fi
  c_do "Archive currently tracks ${#ARCHIVE_SEEN[@]} entries."

  declare -A PENDING_KEYS=()
  declare -a MISSING_URLS=()
  local total="${#ENTRIES[@]}"
  local skipped=0

  if [[ $force_download -eq 1 ]]; then
    # Force mode: download everything, skip archive filtering
    c_do "Force download enabled - bypassing archive check"
    for entry in "${ENTRIES[@]}"; do
      IFS=$'\t' read -r vid entry_url extractor title <<<"$entry" || true
      [[ -n "$vid" && -n "$entry_url" ]] || continue
      local key="${extractor:-youtube} ${vid}"
      if [[ -n "${PENDING_KEYS[$key]+x}" ]]; then
        continue
      fi
      PENDING_KEYS["$key"]=1
      MISSING_URLS+=("$entry_url")
    done
  else
    # Normal mode: filter against archive
    for entry in "${ENTRIES[@]}"; do
      IFS=$'\t' read -r vid entry_url extractor title <<<"$entry" || true
      [[ -n "$vid" && -n "$entry_url" ]] || continue
      local key="${extractor:-youtube} ${vid}"
      if [[ -n "${ARCHIVE_SEEN[$key]+x}" ]]; then
        ((++skipped))
        continue
      fi
      if [[ -n "${PENDING_KEYS[$key]+x}" ]]; then
        continue
      fi
      PENDING_KEYS["$key"]=1
      MISSING_URLS+=("$entry_url")
    done
  fi
  c_do "After filtering, ${#MISSING_URLS[@]} entries remain to download (skipped $skipped)."

  # Prepare logging of requested/succeeded/failed
  # Build mapping from URL -> (vid, extractor, title) for the ones we will attempt
  declare -A REQ_META_URL=()   # url -> $vid\t$extractor\t$title
  declare -a REQ_LIST=()       # lines: vid\turl\textractor\ttitle
  # helper to lookup meta by URL from ENTRIES list (tab-separated fields)
  for entry in "${ENTRIES[@]}"; do
    IFS=$'\t' read -r e_vid e_url e_extractor e_title <<<"$entry" || true
    [[ -n "$e_vid" && -n "$e_url" ]] || continue
    REQ_META_URL["$e_url"]="${e_vid}\t${e_extractor}\t${e_title}"
  done
  for u in "${MISSING_URLS[@]}"; do
    meta="${REQ_META_URL[$u]:-}"
    if [[ -n "$meta" ]]; then
      IFS=$'\t' read -r m_vid m_ex m_title <<<"$meta" || true
      REQ_LIST+=("${m_vid}\t${u}\t${m_ex}\t${m_title}")
    else
      # Fallback: try to derive id from URL, minimal fields
      local _id; _id="$(id_from_url "$u")"
      REQ_LIST+=("${_id}\t${u}\t\t")
    fi
  done

  if [[ ${#MISSING_URLS[@]} -eq 0 ]]; then
    c_ok "Archive up to date. Skipped $skipped of $total entries."
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

  c_do "Need to download ${#MISSING_URLS[@]} new entries (skipped $skipped / $total)."

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
  local planned_tsv="${base}.planned.tsv"
  local pending_tsv="${base}.pending.tsv"
  local req_tsv="${base}.requested.tsv"
  local ok_tsv="${base}.succeeded.tsv"
  local fail_tsv="${base}.failed.tsv"
  local fail_urls="${base}.failed_urls.txt"
  # Initialize planned/pending/requested; do not pre-create failed files
  : > "$planned_tsv"; : > "$pending_tsv"; : > "$req_tsv"
  local line
  for line in "${REQ_LIST[@]}"; do
    printf '%s\n' "$line" >> "$planned_tsv"
    printf '%s\n' "$line" >> "$pending_tsv"
    printf '%s\n' "$line" >> "$req_tsv"
  done
  # Pre-mark already-present media as succeeded and remove from pending
  has_media_for_id(){
    local vid="$1"
    if find pull -maxdepth 1 -type f \
         \( -iname "${vid}__*.opus" -o -iname "${vid}__*.m4a" -o -iname "${vid}__*.mp3" -o -iname "${vid}__*.mp4" -o -iname "${vid}__*.mkv" -o -iname "${vid}__*.webm" -o -iname "${vid}__*.mka" \) \
         -print -quit >/dev/null 2>&1; then
      return 0
    fi
    return 1
  }
  local vid url ex title
  for line in "${REQ_LIST[@]}"; do
    IFS=$'\t' read -r vid url ex title <<<"$line" || true
    if [[ -n "$vid" ]] && has_media_for_id "$vid"; then
      bash scripts/utilities/mark_success.sh "$vid" "$base" || true
    fi
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
      --concurrent-fragments 1
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
      --concurrent-fragments 1
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
      --concurrent-fragments 1
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

  # --- Post-run logging: derive failed from pending; ok is whatever mark_success recorded ---
  : > "$fail_tsv"; : > "$fail_urls"
  if [[ -s "$pending_tsv" ]]; then
    cp "$pending_tsv" "$fail_tsv" 2>/dev/null || true
    # extract urls (2nd field)
    awk -F '\t' 'NF>=2 { print $2 }' "$pending_tsv" > "$fail_urls" 2>/dev/null || true
  fi
  # Ensure ok_tsv exists
  touch "$ok_tsv"
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
