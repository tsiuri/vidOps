#!/usr/bin/env bash
# clips.sh — scaffolding for clip workflows (pull → hits → slice)
# Deps: yt-dlp, ffprobe, ffmpeg, awk, sed, grep (ripgrep/fd optional)

set -euo pipefail

# Tool installation directory (where this script lives)
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
COMP_DIR="$SCRIPT_DIR/clips_templates"

# Tool root (where all scripts are installed)
TOOL_ROOT="${TOOL_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

# Project directory (where data lives)
PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"

# Export for child scripts
export TOOL_ROOT
export PROJECT_ROOT

# Initialize project structure if needed
if [[ ! -f "$PROJECT_ROOT/.vidops-project" ]]; then
    mkdir -p "$PROJECT_ROOT"/{pull,generated,logs/pull,data,results,cuts}
    touch "$PROJECT_ROOT/.vidops-project"
fi

# Stay in project directory - don't cd to tool directory

if [[ ! -d "$COMP_DIR" ]]; then
  printf '\033[1;31mxx  missing components directory: %s\033[0m\n' "$COMP_DIR" >&2
  exit 1
fi

# shellcheck source=clips/common.sh
source "$COMP_DIR/common.sh"

cmd_help(){
  cat <<'H'
clips.sh — CLI front-end

USAGE
  ./clips.sh pull <channel-or-video-URL> [--force|--no-download-archive]
  ./clips.sh hits [-q "term1,term2"] [-w SECONDS] [--words] [--exact] [-o wanted.tsv]
  ./clips.sh cut-local <wanted.tsv> [outdir]
  ./clips.sh cut-net   <wanted.tsv> [outdir]
  ./clips.sh cut-net   --from-words-exact <file|dir> -q "term,phrase" [outdir]
  ./clips.sh refine    -q WORD [indir] [outdir]
  ./clips.sh help

NOTES
  • Run from a directory containing media / transcripts — everything scans recursively.
  • "pull" writes .info.json and a provenance <base>.src.json per media file.
    Use --force or --no-download-archive to redownload videos already in the archive.
  • "hits" scans precise *.words.tsv (if present), then *.words.yt.tsv, and finally legacy *.tslog.txt, emits TSV.
    Add --exact (words source) to use the exact [start→end] per word from words.tsv (plus PAD_START/PAD_END),
    instead of a fixed window around the midpoint.
  • "cut-local" slices from already-downloaded media using ffmpeg (fast).
  • "cut-net" grabs precise windows from the network with yt-dlp --download-sections.
    In --from-words-exact mode, it scans words.tsv logs and pulls exact word/phrase spans.
  • "refine" re-trims existing clips by snapping tightly to a target word.

Sub-scripts
  This command dynamically sources and dispatches to components under
  scripts/utilities/clips_templates/: pull.sh, hits.sh, cut_local.sh,
  cut_net.sh, refine.sh

ENV KNOBS (override as needed)
  CLIP_CONTAINER=opus|mp3|mka|mp4   (default opus)
  CLIP_AUDIO_BR=96k                  audio bitrate for opus/mp3
  PAD_START=0 PAD_END=0              padding seconds added to each clip
  NAME_MAX_TITLE=70                  characters of title in filenames
  CLIPS_OUT=cuts                     default output dir for cut-local
  CLIP_NET_OUT=cuts                  default output dir for cut-net
  (refine) defaults: model=medium, pad-pre=30ms, pad-post=60ms, outdir=cuts_refined
  CLIP_ARCHIVE_FILE=.clips-download-archive.txt  path for yt-dlp archive

  # yt-dlp pacing defaults (safe-ish):
  YT_SLEEP_REQUESTS=0.35  YT_SLEEP_INTERVAL=0.35  YT_MAX_SLEEP_INTERVAL=1.5
  YT_RETRIES=15        YT_FRAG_RETRIES=20   YT_EXTRACTOR_RETRIES=10
  PULL_DISCOVERY_SLEEP=0              additional delay (seconds) during discovery
  YT_PULL_SLEEP_REQUESTS              override sleep just for pull downloads
  YT_PULL_SLEEP_INTERVAL              per-request sleep when downloading
  YT_PULL_MAX_SLEEP_INTERVAL          cap for randomised sleep when downloading
H
}

source_component(){
  local file="$COMP_DIR/$1.sh"
  [[ -r "$file" ]] || { c_er "missing component: $file"; exit 1; }
  # shellcheck disable=SC1090
  source "$file"
}

sub="${1:-help}"
if [[ $# -gt 0 ]]; then shift; fi

case "$sub" in
  help|-h|--help)
    cmd_help
    ;;
  pull)
    source_component "pull"
    cmd_pull "$@"
    ;;
  hits)
    source_component "hits"
    cmd_hits "$@"
    ;;
  cut-local)
    source_component "cut_local"
    cmd_cut_local "$@"
    ;;
  cut-net)
    source_component "cut_net"
    cmd_cut_net "$@"
    ;;
  refine)
    source_component "refine"
    cmd_refine "$@"
    ;;
  transcripts)
    source_component "transcripts"
    cmd_transcripts "$@"
    ;;
  *)
    c_er "unknown subcommand: $sub"
    cmd_help
    exit 1
    ;;
esac
