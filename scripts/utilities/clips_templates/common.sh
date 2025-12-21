#!/usr/bin/env bash
# Common helpers and defaults for the clips workflow.
# shellcheck shell=bash

# -------- Defaults (override via env) --------
: "${CLIP_CONTAINER:=opus}"        # opus|mp3|mka|mp4
: "${CLIP_AUDIO_BR:=96k}"
: "${PAD_START:=0}"                # seconds to prepend to each cut
: "${PAD_END:=0}"                  # seconds to append to each cut
: "${NAME_MAX_TITLE:=70}"          # title chars included in output filename
: "${CLIPS_OUT:=${PROJECT_ROOT}/cuts}"
: "${CLIP_NET_OUT:=${PROJECT_ROOT}/cuts}"        # default output dir for cut-net
: "${CLIP_ARCHIVE_FILE:=${PROJECT_ROOT}/.clips-download-archive.txt}"

# yt-dlp pacing (safe-ish)
: "${YT_SLEEP_REQUESTS:=0.20}"
: "${YT_SLEEP_INTERVAL:=0.20}"
: "${YT_MAX_SLEEP_INTERVAL:=1.5}"
: "${YT_RETRIES:=15}"
: "${YT_FRAG_RETRIES:=20}"
: "${YT_EXTRACTOR_RETRIES:=10}"

# -------- Pretty logging --------
c_do(){ printf '\033[1;34m==> %s\033[0m\n' "$*"; }
c_ok(){ printf '\033[1;32m%s\033[0m\n' "$*"; }
c_wr(){ printf '\033[1;33m!!  %s\033[0m\n' "$*" >&2; }
c_er(){ printf '\033[1;31mxx  %s\033[0m\n' "$*" >&2; }

# -------- Helpers --------
have(){ command -v "$1" >/dev/null 2>&1; }

fd_find(){
  local searchdir="${2:-.}"
  if have fd; then
    fd -a -t f -g "$1" "$searchdir"
  else
    find "$searchdir" -type f -name "$1" -print
  fi
}

id_from_url(){
  local u="${1:-}"
  if [[ "$u" =~ v=([A-Za-z0-9_-]{11}) ]]; then
    echo "${BASH_REMATCH[1]}"
    return
  fi
  if [[ "$u" =~ youtu\.be/([A-Za-z0-9_-]{11}) ]]; then
    echo "${BASH_REMATCH[1]}"
    return
  fi
  echo ""
}

ensure_src_json(){
  # ensure_src_json <media-path-with-ext> (creates <base>.src.json if missing)
  local media="$1"; local base="${media%.*}"; local src="${base}.src.json"
  [[ -f "$src" ]] && return 0

  local info="${base}.info.json"
  if [[ -f "$info" ]]; then
    python3 - "$info" "$src" <<'PY' || true
import json, sys
info, out = sys.argv[1], sys.argv[2]
with open(info, 'r', encoding='utf-8', errors='ignore') as f:
    d = json.load(f)
vid = {
  "platform": d.get("extractor_key") or "YouTube",
  "id": d.get("id"),
  "url": d.get("webpage_url") or (f"https://www.youtube.com/watch?v={d.get('id')}" if d.get('id') else None),
  "title": d.get("title"),
  "uploader": d.get("uploader"),
  "upload_date": d.get("upload_date"),
  "duration": d.get("duration"),
  "base_offset": 0.0
}
with open(out, 'w', encoding='utf-8') as o:
  json.dump(vid, o, ensure_ascii=False, indent=2)
PY
    return 0
  fi

  # Try ffprobe purl tag
  local purl=""
  if have ffprobe; then
    purl="$(ffprobe -v error -show_entries format_tags=purl -of default=nk=1:nw=1 -- "$media" 2>/dev/null || true)"
  fi
  local id=""; local url=""
  if [[ -n "$purl" ]]; then
    url="$purl"
    if [[ "$purl" =~ v=([A-Za-z0-9_-]{11}) ]]; then id="${BASH_REMATCH[1]}"; fi
  else
    # filename prefix "ID__" (if present)
    local bn; bn="$(basename "$media")"
    if [[ "$bn" =~ ^([A-Za-z0-9_-]{11})__ ]]; then
      id="${BASH_REMATCH[1]}"
      url="https://www.youtube.com/watch?v=${id}"
    fi
  fi

  cat > "$src" <<EOF
{
  "platform": "YouTube",
  "id": ${id:+\"$id\"},
  "url": ${url:+\"$url\"},
  "title": null,
  "uploader": null,
  "upload_date": null,
  "duration": null,
  "base_offset": 0.0
}
EOF
}

src_url_for_id(){
  # src_url_for_id <ytid> ; print url or blank
  local ytid="$1"
  local f=""
  if have rg; then
    f="$(rg -l "\"id\" *: *\"${ytid}\"" -g '**/*.src.json' "${PROJECT_ROOT}/pull" 2>/dev/null | head -n1 || true)"
  else
    f="$(grep -Rsl "\"id\" *: *\"${ytid}\"" -- "${PROJECT_ROOT}/pull"/*.src.json 2>/dev/null | head -n1 || true)"
  fi
  if [[ -n "$f" && -r "$f" ]]; then
    python3 - "$f" <<'PY' || true
import json, sys
with open(sys.argv[1],'r',encoding='utf-8',errors='ignore') as f:
  d=json.load(f)
print(d.get('url') or '', end='')
PY
    return 0
  fi
  [[ -n "$ytid" ]] && printf 'https://www.youtube.com/watch?v=%s' "$ytid" || printf ''
}

media_for_id(){
  # media_for_id <ytid> ; echo best-guess media file path (not .json)
  local ytid="$1"
  local cand
  # Prefer real media extensions and ignore JSON sidecars
  while IFS= read -r cand; do
    case "${cand,,}" in
      *.opus|*.m4a|*.mp3|*.mp4|*.mkv|*.webm|*.mka)
        echo "$cand"; return 0 ;;
      *.info.json|*.src.json)
        ;; # skip
      *)
        ;; # unknown ext; keep scanning
    esac
  done < <(fd_find "${ytid}__*.*" "${PROJECT_ROOT}/pull")

  # Fallback: derive from a matching .src.json and pick an existing media ext
  local src=""
  if have rg; then
    src="$(rg -l "\"id\" *: *\"${ytid}\"" -g '**/*.src.json' "${PROJECT_ROOT}/pull" 2>/dev/null | head -n1 || true)"
  else
    src="$(grep -Rsl "\"id\" *: *\"${ytid}\"" -- "${PROJECT_ROOT}/pull"/*.src.json 2>/dev/null | head -n1 || true)"
  fi
  if [[ -n "$src" ]]; then
    local base="${src%.*}"
    for ext in .opus .m4a .mp3 .mp4 .mkv .webm .mka; do
      [[ -f "$base$ext" ]] && { echo "$base$ext"; return 0; }
    done
    # Last resort: suggest an .opus path even if not present
    echo "$base.opus"; return 0
  fi
  return 1
}

title_tail(){
  local p="$1"
  local b; b="$(basename "$p")"
  echo "$b" | cut -c -"${NAME_MAX_TITLE}"
}

clips_archive_file(){
  printf '%s\n' "${CLIP_ARCHIVE_FILE}"
}

collect_existing_archive_keys(){
  python3 <<'PY'
import json, os, re, sys
root = os.environ.get('PROJECT_ROOT', '.') + '/pull'
if not os.path.exists(root):
    root = os.environ.get('PROJECT_ROOT', '.')
emit = set()

id_prefix = re.compile(r'^([A-Za-z0-9_-]{11})__')

def norm_extractor(value, url=""):
    if value:
        return value.strip().lower()
    if url:
        u = url.lower()
        if "youtube.com" in u or "youtu.be" in u:
            return "youtube"
        host = u.split("//")[-1].split("/")[0]
        if host:
            host = host.split(":")[0]
            if host:
                host = host.split(".")
                if host:
                    return host[0]
    return "youtube"

def add_key(extractor, vid, url=""):
    if not vid:
        return
    extractor = norm_extractor(extractor, url)
    key = f"{extractor} {vid}"
    if key in emit:
        return
    emit.add(key)
    print(key)

def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return json.load(f)
    except Exception:
        return {}

media_exts = {".opus", ".m4a", ".mp3", ".mp4", ".mkv"}

for dirpath, _, filenames in os.walk(root):
    for name in filenames:
        lower = name.lower()
        path = os.path.join(dirpath, name)
        if lower.endswith('.src.json'):
            data = load_json(path)
            vid = data.get('id')
            if not vid:
                m = id_prefix.match(name)
                if m:
                    vid = m.group(1)
            base = path[:-9]
            info = load_json(base + '.info.json')
            extractor = info.get('extractor_key') or info.get('extractor') or info.get('extractor_id') \
                or data.get('platform') or data.get('extractor') or data.get('extractor_key')
            url = data.get('url') or info.get('webpage_url') or ''
            add_key(extractor, vid, url)
            continue
        if lower.endswith('.info.json'):
            data = load_json(path)
            vid = data.get('id')
            if not vid:
                continue
            extractor = data.get('extractor_key') or data.get('extractor') or data.get('extractor_id')
            url = data.get('webpage_url') or data.get('original_url') or ''
            add_key(extractor, vid, url)
            continue
        rootname, ext = os.path.splitext(name)
        if ext.lower() in media_exts:
            m = id_prefix.match(name)
            if not m:
                continue
            vid = m.group(1)
            add_key("youtube", vid)
PY
}

sync_archive_from_media(){
  local archive="$1"
  local dir
  dir=$(dirname "$archive")
  if [[ "$dir" != "." ]]; then
    mkdir -p "$dir"
  fi
  touch "$archive"
  local tmp; tmp=$(mktemp)
  collect_existing_archive_keys >"$tmp"
  if [[ -s "$tmp" ]]; then
    awk 'NF && !seen[$0]++' "$archive" "$tmp" > "${archive}.tmp"
    mv "${archive}.tmp" "$archive"
  fi
  rm -f "$tmp"
}

# -------- Cookie discovery (shared) --------
# Sets global array COOKIE_ARG with either (--cookies FILE) or (--cookies-from-browser SPEC)
# Env:
#   COOKIE_BROWSER: force browser (e.g. firefox)
#   YTDLP_COOKIES_BROWSER: alias of COOKIE_BROWSER
#   FIREFOX_PROFILE, CHROME_PROFILE, CHROMIUM_PROFILE, EDGE_PROFILE, BRAVE_PROFILE
#   COOKIE_REQUIRE: if set (e.g. firefox), only attempt that browser
#   COOKIE_BROWSER_ORDER: space-separated order (default: firefox chrome chromium edge brave)
#   COOKIE_PROBE_TIMEOUT: seconds per probe (default 8)
clips_discover_cookies(){
  COOKIE_ARG=()
  local probe="${COOKIE_PROBE_TIMEOUT:-8}s"

  # Prefer explicit cookies file
  local f
  for f in ${YTDLP_COOKIES:-} ${COOKIES_TXT:-} ./cookies.txt "$HOME/.config/yt-dlp/cookies.txt"; do
    [[ -n "$f" && -r "$f" && -s "$f" ]] || continue
    COOKIE_ARG=( --cookies "$f" )
    c_ok "Using cookies file: $f"
    return 0
  done

  # Build browser list
  local -a browsers=()
  local forced_b="${COOKIE_BROWSER:-${YTDLP_COOKIES_BROWSER:-}}"
  if [[ -n "$forced_b" ]]; then
    browsers+=("$forced_b")
  elif [[ -n "${COOKIE_REQUIRE:-}" ]]; then
    browsers+=("${COOKIE_REQUIRE}")
  else
    # default order prefers firefox
    local order="${COOKIE_BROWSER_ORDER:-firefox chrome chromium edge brave}"
    # shellcheck disable=SC2206
    browsers=($order)
  fi

  # Explicit Firefox profile probing
  local -a firefox_specs=()
  if [[ -n "${FIREFOX_PROFILE:-}" ]]; then
    firefox_specs+=("firefox:profile=${FIREFOX_PROFILE}")
  else
    local pf
    for pf in default-release default-esr dev-edition-default default; do
      firefox_specs+=("firefox:profile=${pf}")
    done
  fi
  firefox_specs+=("firefox")

  local b spec
  for b in "${browsers[@]}"; do
    local -a specs=("$b")
    [[ "$b" == "firefox" ]] && specs=("${firefox_specs[@]}")
    for spec in "${specs[@]}"; do
      if timeout "$probe" yt-dlp --ignore-config --cookies-from-browser "$spec" --dump-json "https://www.youtube.com" >/dev/null 2>&1; then
        c_ok "Using cookies from: $spec"
        COOKIE_ARG=( --cookies-from-browser "$spec" )
        return 0
      fi
    done
  done
  return 1
}
