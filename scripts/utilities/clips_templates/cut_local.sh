#!/usr/bin/env bash
# shellcheck shell=bash

cmd_cut_local(){
  [[ $# -ge 1 ]] || { c_er "cut-local: need TSV"; exit 1; }
  local TSV="$1"; local OUTDIR="${2:-$CLIPS_OUT}"
  [[ -r "$TSV" ]] || { c_er "not readable: $TSV"; exit 1; }
  mkdir -p "$OUTDIR"
  c_do "Cutting locally → $OUTDIR"

  tail -n +2 "$TSV" | while IFS=$'\t' read -r url start end label caption; do
    local id=""; [[ -n "${url:-}" ]] && id="$(id_from_url "$url")"
    if [[ -z "$id" ]]; then
      c_wr "No URL/ID in row; skipping: $label | $start-$end"
      continue
    fi
    local media; media="$(media_for_id "$id")"
    [[ -z "$media" ]] && { c_wr "no media for $id"; continue; }
    ensure_src_json "$media"

    local s="$start"; local e="$end"
    # Note: put '-' first in the tr set to avoid it being parsed as a range
    local safe_label; safe_label="$(echo "${label:-hit}" | tr -cd -- '-[:alnum:]_. ' | sed 's/ \+/_/g')"
    local short_title; short_title="$(title_tail "$media")"
    local out="${OUTDIR}/${id}_${safe_label}_${short_title}.${CLIP_CONTAINER}"

    if [[ "$CLIP_CONTAINER" == "mp4" ]]; then
      ffmpeg -nostdin -hide_banner -loglevel error -ss "$s" -to "$e" -i "$media" \
        -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 128k -map_metadata 0 -y "$out"
    elif [[ "$CLIP_CONTAINER" == "mp3" ]]; then
      ffmpeg -nostdin -hide_banner -loglevel error -ss "$s" -to "$e" -i "$media" \
        -vn -c:a libmp3lame -b:a "$CLIP_AUDIO_BR" -map_metadata 0 -y "$out"
    elif [[ "$CLIP_CONTAINER" == "mka" ]]; then
      ffmpeg -nostdin -hide_banner -loglevel error -ss "$s" -to "$e" -i "$media" \
        -vn -c:a copy -map_metadata 0 -y "$out"
    else # opus default
      ffmpeg -nostdin -hide_banner -loglevel error -ss "$s" -to "$e" -i "$media" \
        -vn -c:a libopus -b:a "$CLIP_AUDIO_BR" -map_metadata 0 -y "$out"
    fi
    [[ -f "${media%.*}.src.json" ]] && cp -f -- "${media%.*}.src.json" "${out%.*}.src.json" || true
    echo "Wrote: $out"
  done
  c_ok "Local cutting complete."
}
