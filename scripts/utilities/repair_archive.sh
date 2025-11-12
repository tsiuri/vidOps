#!/usr/bin/env bash
# repair_archive.sh — identify metadata-only entries and clean the download archive
#
# Usage:
#   scripts/utilities/repair_archive.sh [--apply] [--report]
#     [--archive <file>] [--pull-dir <dir>] [--unfinished <file>]
#
# Notes:
#   - Default archive: .clips-download-archive.txt
#   - Default pull dir: pull
#   - Default unfinished: .clips-download-archive-from-unfinished.txt (if present)
#   - Without --apply, writes cleaned copies with .cleaned suffix (originals untouched)

set -euo pipefail

ARCHIVE=".clips-download-archive.txt"
UNFINISHED=".clips-download-archive-from-unfinished.txt"
PULL_DIR="pull"
APPLY=0
REPORT=0

print_help() {
cat <<'H'
repair_archive.sh — identify metadata-only entries and clean the download archive

Usage:
  scripts/utilities/repair_archive.sh [--apply] [--report]
    [--archive <file>] [--pull-dir <dir>] [--unfinished <file>]

Options:
  --archive <file>     Path to main archive (default: .clips-download-archive.txt)
  --unfinished <file>  Path to unfinished archive (default: .clips-download-archive-from-unfinished.txt, if exists)
  --pull-dir <dir>     Directory containing pulled files (default: pull)
  --apply              Replace archives in-place (otherwise writes .cleaned files)
  --report             Print “why” breakdown from .info.json (availability/live_status/formats)
  --regen-provenance   Generate missing .src.json for existing media in --pull-dir
  --emit-lists         Write URL/ID lists for re-download of info-only items
  --out-prefix <name>  Prefix for emitted lists (default: info_only)
  -h, --help           Show this help
H
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive) ARCHIVE="${2:-}"; shift 2 ;;
    --unfinished) UNFINISHED="${2:-}"; shift 2 ;;
    --pull-dir) PULL_DIR="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --report) REPORT=1; shift ;;
    --regen-provenance) REGEN_PROV=1; shift ;;
    --emit-lists) EMIT_LISTS=1; shift ;;
    --out-prefix) OUT_PREFIX="${2:-}"; shift 2 ;;
    -h|--help) print_help; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; print_help; exit 1 ;;
  esac
done

[[ -d "$PULL_DIR" ]] || { echo "pull dir not found: $PULL_DIR" >&2; exit 1; }
[[ -f "$ARCHIVE" ]] || { echo "archive not found: $ARCHIVE" >&2; exit 1; }
[[ -f "$UNFINISHED" ]] || UNFINISHED=""

tmp_root="${TMPDIR:-./tmp}"
mkdir -p "$tmp_root" 2>/dev/null || true
_rand() { echo "$RANDOM$RANDOM$(date +%s%N 2>/dev/null || date +%s)"; }
tmp_info="$tmp_root/repair_info.$(_rand)"; : > "$tmp_info"
tmp_media="$tmp_root/repair_media.$(_rand)"; : > "$tmp_media"
tmp_only="$tmp_root/repair_only.$(_rand)"; : > "$tmp_only"
trap 'rm -f "$tmp_info" "$tmp_media" "$tmp_only"' EXIT

# Enable nullglob so empty patterns don’t leak as literals
shopt -s nullglob

# Collect IDs from filenames: PREFIX__....ext
# info.json IDs
info_files=( "$PULL_DIR"/*.info.json )
: > "$tmp_info"
for f in "${info_files[@]}"; do
  bn="$(basename "$f")"
  awk -F'__' 'NF>=2{print $1}' <<<"$bn"
done | sort -u > "$tmp_info"

# media IDs (any valid A/V ext)
: > "$tmp_media"
media_exts=( opus m4a mp3 mp4 mkv webm mka )
for ext in "${media_exts[@]}"; do
  media_files=( "$PULL_DIR"/*."$ext" )
  for f in "${media_files[@]}"; do
    bn="$(basename "$f")"
    awk -F'__' 'NF>=2{print $1}' <<<"$bn"
  done
done | sort -u > "$tmp_media"

# info-only = info_ids - media_ids
comm -23 "$tmp_info" "$tmp_media" > "$tmp_only"

INFO_CT=$(wc -l < "$tmp_info" | tr -d ' ')
MEDIA_CT=$(wc -l < "$tmp_media" | tr -d ' ')
ONLY_CT=$(wc -l < "$tmp_only" | tr -d ' ')

echo "info.json videos:  $INFO_CT"
echo "media files:       $MEDIA_CT"
echo "info-only videos:  $ONLY_CT"

if [[ "$ONLY_CT" -eq 0 ]]; then
  echo "Nothing to repair."
  exit 0
fi

# Create cleaned archive copies (do not mutate originals unless --apply)
CLEAN_MAIN="${ARCHIVE}.cleaned"
awk 'NR==FNR{bad[$0]=1; next} { if (($1=="youtube") && (NF>=2) && ($2 in bad)) next; print }' \
  "$tmp_only" "$ARCHIVE" > "$CLEAN_MAIN"
echo "Wrote cleaned main archive: $CLEAN_MAIN"

CLEAN_UNFIN=""
if [[ -n "$UNFINISHED" ]]; then
  CLEAN_UNFIN="${UNFINISHED}.cleaned"
  awk 'NR==FNR{bad[$0]=1; next} { if (($1=="youtube") && (NF>=2) && ($2 in bad)) next; print }' \
    "$tmp_only" "$UNFINISHED" > "$CLEAN_UNFIN"
  echo "Wrote cleaned unfinished archive: $CLEAN_UNFIN"
fi

# Optional provenance regeneration (before any reporting/cleaning)
if [[ "${REGEN_PROV:-0}" -eq 1 ]]; then
  # Source common helpers to get ensure_src_json
  if [[ -f scripts/utilities/clips_templates/common.sh ]]; then
    # shellcheck source=/dev/null
    source scripts/utilities/clips_templates/common.sh
  else
    echo "common.sh not found at scripts/utilities/clips_templates/common.sh" >&2
    exit 1
  fi
  created=0; present=0; checked=0
  while IFS= read -r -d '' f; do
    ((checked++))
    base="${f%.*}"; src="$base.src.json"
    if [[ -f "$src" ]]; then
      ((present++))
      continue
    fi
    ensure_src_json "$f" || true
    if [[ -f "$src" ]]; then ((created++)); fi
  done < <(find "$PULL_DIR" -type f \( -iname '*.opus' -o -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.webm' -o -iname '*.mka' \) -print0)
  echo "Provenance regen: scanned=$checked created=$created already_present=$present"
fi

# Optional report explaining “why”
if [[ "$REPORT" -eq 1 ]]; then
  python3 - "$PULL_DIR" "$tmp_only" << 'PY'
import json, os, sys
pull, ids_file = sys.argv[1], sys.argv[2]
ids = set(x.strip() for x in open(ids_file, 'r', encoding='utf-8') if x.strip())
stats, examples = {}, {}
total = 0
try:
  names = os.listdir(pull)
except FileNotFoundError:
  names = []
for name in names:
    if not name.endswith('.info.json'): continue
    if '__' not in name: continue
    vid = name.split('__',1)[0]
    if vid not in ids: continue
    total += 1
    path = os.path.join(pull, name)
    try:
        d = json.load(open(path,'r',encoding='utf-8',errors='ignore'))
    except Exception:
        continue
    avail = (d.get('availability') or 'none').lower()
    live = (d.get('live_status') or 'none').lower()
    fmts = d.get('formats') or []
    has_audio = any((f.get('acodec') not in (None,'none')) for f in fmts)
    key = (avail, live, 'audio' if has_audio else 'no_formats')
    stats[key] = stats.get(key, 0) + 1
    if key not in examples: examples[key] = name
print("Report (info-only items):", total)
for k,v in sorted(stats.items(), key=lambda kv: -kv[1]):
    print(f"{v:5d}  availability={k[0]}  live_status={k[1]}  {k[2]}")
print("Examples:")
for k, ex in examples.items():
    print(f"  availability={k[0]} live_status={k[1]} {k[2]}  -> {ex}")
PY
fi

# Apply in-place if requested
if [[ "$APPLY" -eq 1 ]]; then
  cp "$CLEAN_MAIN" "$ARCHIVE"
  echo "Replaced archive: $ARCHIVE"
  if [[ -n "$CLEAN_UNFIN" ]]; then
    cp "$CLEAN_UNFIN" "$UNFINISHED"
    echo "Replaced unfinished archive: $UNFINISHED"
  fi
  echo "Done."
else
  echo "Dry-run only. Re-run with --apply to replace archives."
fi
# Optional: emit re-download lists
if [[ "${EMIT_LISTS:-0}" -eq 1 ]]; then
  OUT_PREFIX="${OUT_PREFIX:-info_only}"
  ids_out="${OUT_PREFIX}_ids.txt"
  urls_out="${OUT_PREFIX}_urls.txt"
  needs_out="${OUT_PREFIX}_needs_auth_urls.txt"
  public_out="${OUT_PREFIX}_public_urls.txt"
  none_out="${OUT_PREFIX}_none_urls.txt"
  retry_out="${OUT_PREFIX}_retry_urls.txt"  # default "likely to succeed" (needs_auth)

  cp "$tmp_only" "$ids_out"
  python3 - "$PULL_DIR" "$tmp_only" "$urls_out" "$needs_out" "$public_out" "$none_out" "$retry_out" << 'PY'
import json, os, sys
pull, ids_file, urls_out, needs_out, public_out, none_out, retry_out = sys.argv[1:8]
ids = [x.strip() for x in open(ids_file,'r',encoding='utf-8') if x.strip()]

def first_info_path(vid):
    # Return first matching .info.json for this id
    prefix = vid + '__'
    for name in os.listdir(pull):
        if name.startswith(prefix) and name.endswith('.info.json'):
            return os.path.join(pull, name)
    return None

def url_from_info(p):
    try:
        d = json.load(open(p,'r',encoding='utf-8',errors='ignore'))
    except Exception:
        return None, 'none'
    url = d.get('webpage_url') or d.get('original_url')
    avail = (d.get('availability') or 'none').lower()
    return url, avail

urls_all = []
urls_needs = []
urls_public = []
urls_none = []

for vid in ids:
    p = first_info_path(vid)
    url, avail = (None, 'none')
    if p:
        url, avail = url_from_info(p)
    if not url:
        url = f"https://www.youtube.com/watch?v={vid}"
    urls_all.append(url)
    if avail == 'needs_auth':
        urls_needs.append(url)
    elif avail == 'public':
        urls_public.append(url)
    else:
        urls_none.append(url)

def write_list(path, lines):
    with open(path,'w',encoding='utf-8') as f:
        for ln in lines:
            f.write(ln+"\n")

write_list(urls_out, urls_all)
write_list(needs_out, urls_needs)
write_list(public_out, urls_public)
write_list(none_out, urls_none)
write_list(retry_out, urls_needs)

print(f"Emitted: {urls_out} ({len(urls_all)})")
print(f"         {needs_out} ({len(urls_needs)})")
print(f"         {public_out} ({len(urls_public)})")
print(f"         {none_out} ({len(urls_none)})")
print(f"         {retry_out} ({len(urls_needs)})  [default retry set]")
PY
fi
