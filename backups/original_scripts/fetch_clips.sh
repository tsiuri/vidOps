#!/usr/bin/env bash
set -euo pipefail
TSV="${1:?usage: fetch_clips.sh wanted_clips.tsv [outdir]}"
OUTDIR="${2:-clips}"
PADDING="${PADDING:-0}"   # optional extra seconds added to both start/end

mkdir -p "$OUTDIR"

# Favor 4K when available, fallback cleanly
FMT='bestvideo[height=2160][ext=mp4]+bestaudio/best/bestvideo[height=2160]+bestaudio/best/best'

# If you need cookies, set COOKIES=("--cookies-from-browser" "firefox")
COOKIES=()

is_float() { awk -v x="$1" 'BEGIN{if (x ~ /^-?[0-9]+(\.[0-9]+)?$/) exit 0; exit 1}'; }

while IFS=$'\t' read -r url start end label src; do
  # skip empty lines and comments
  [[ -z "${url:-}" ]] && continue
  [[ "${url:0:1}" == "#" ]] && continue

  # skip header line(s)
  if [[ "$url" == "url" || "$start" == "start" || "$end" == "end" ]]; then
    continue
  fi

  # basic validation
  if ! is_float "${start:-}" || ! is_float "${end:-}"; then
    echo "!!  Skipping non-numeric row: $url  $start  $end" >&2
    continue
  fi

  # pad
  s=$(python3 - <<PY
s=float("$start")+float("$PADDING")
print(max(0.0,s))
PY
)
  e=$(python3 - <<PY
e=float("$end")+float("$PADDING")
print(max(0.001,e))
PY
)

  # ensure s<e
  awk -v s="$s" -v e="$e" 'BEGIN{exit !(s<e)}' || { echo "!!  Bad window (s>=e) for $url ($s..$e)" >&2; continue; }

  # safe-ish filename bits (strip slashes and control chars)
  safe_label=$(printf '%s' "${label:-hit}" | tr '/\000-\037' '_')
  yt-dlp "${COOKIES[@]}" -N 8 -f "$FMT" --merge-output-format mp4 \
    --download-sections "*${s}-${e}" \
    -o "${OUTDIR}/%(id)s_%(title).150B_${s}-${e}_${safe_label}.%(ext)s" \
    -- "$url"

done < "$TSV"
