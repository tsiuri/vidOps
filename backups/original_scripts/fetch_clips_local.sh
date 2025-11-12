#!/usr/bin/env bash
set -euo pipefail
# fetch_clips_local.sh wanted_clips.tsv [outdir]
# Cuts clips using ffmpeg from local media (e.g., your .opus files).
# Requires each source to have a sibling <base>.src.json containing a YouTube URL or id.

here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[[ -f "$here/clips.env" ]] && source "$here/clips.env" || true

tsv="${1:-wanted_clips.tsv}"
outdir="${2:-${CLIPS_OUT:-clips}}"

mkdir -p "$outdir"
bash "$(dirname "$0")/validate_tsv.sh" "$tsv"

# find media by YT ID derived from the url column
id_from_url() {
  local u="$1"
  # supports v=ID and youtu.be/ID
  if [[ "$u" =~ v=([A-Za-z0-9_-]{11}) ]]; then echo "${BASH_REMATCH[1]}"; return 0; fi
  if [[ "$u" =~ youtu\.be/([A-Za-z0-9_-]{11}) ]]; then echo "${BASH_REMATCH[1]}"; return 0; fi
  echo ""
}

while IFS=$'\t' read -r url start end label caption; do
  [[ "$url" == "url" ]] && continue  # header
  id="$(id_from_url "$url")"
  if [[ -z "$id" ]]; then
    echo "WARN: no id in url: $url" >&2
    continue
  fi

  # find a src.json then media nearby
  src="$(fd -a -t f -g "${id}__*.src.json" . || true)"
  if [[ -z "$src" ]]; then
    # Try looser match: any .src.json containing the id
    src="$(rg -l "\"id\" *: *\"${id}\"" -g '*.src.json' || true)"
  fi
  if [[ -z "$src" ]]; then
    echo "WARN: no .src.json found for $id" >&2
  fi

  # candidate media next to src.json (prefer audio)
  base="${src%.*}"
  media=""
  for ext in opus m4a mp3 mkv mp4 mov avi wav; do
    cand="${base%.*}.${ext}"
    [[ -f "$cand" ]] && { media="$cand"; break; }
  done
  if [[ -z "$media" ]]; then
    # fallback: search by pattern
    media="$(fd -a -t f -g "${id}__*.*" . | head -n1 || true)"
  fi
  [[ -z "$media" ]] && { echo "WARN: no media found for $id" >&2; continue; }

  # pad
  s=$(python3 - <<PY "$start" "$PAD_START"
import sys
s=float(sys.argv[1])-float(sys.argv[2])
print(max(0.0,s))
PY
)
  e=$(python3 - <<PY "$end" "$PAD_END"
import sys
e=float(sys.argv[1])+float(sys.argv[2])
print(max(0.0,e))
PY
)

  # output name
  safe_label="$(echo "${label:-hit}" | tr -cd '[:alnum:]_-. ' | sed 's/ /_/g')"
  title="$(basename "${media}")"
  # trim title length
  short_title="$(echo "$title" | cut -c -${NAME_MAX_TITLE:-70})"
  out="${outdir}/${id}_${safe_label}_${short_title}.${CLIP_CONTAINER:-opus}"

  # re-encode for accurate cut; audio-only by default
  if [[ "${CLIP_CONTAINER:-opus}" == "mp4" ]]; then
    ffmpeg -nostdin -hide_banner -loglevel error -ss "$s" -to "$e" -i "$media" \
      -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 128k \
      -map_metadata 0 -y "$out"
  else
    ffmpeg -nostdin -hide_banner -loglevel error -ss "$s" -to "$e" -i "$media" \
      -vn -c:a libopus -b:a "${CLIP_AUDIO_BR:-96k}" \
      -map_metadata 0 -y "$out"
  fi

  # copy provenance if we had it
  if [[ -n "${src:-}" && -f "$src" ]]; then
    cp -f -- "$src" "${out%.*}.src.json"
  fi
  echo "Wrote: $out"
done < <(tail -n +2 "$tsv")
