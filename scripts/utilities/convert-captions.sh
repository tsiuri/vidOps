#!/usr/bin/env bash
# convert-captions.sh - Convert YouTube VTT captions to words.yt.tsv format
#
# Usage:
#   scripts/utilities/convert-captions.sh [options] [files...]
#
# Options:
#   --source-dir <dir>     Directory to scan for *.transcript.en.vtt (default: pull)
#   --dest-dir <dir>       Directory to write outputs (default: generated)
#   --overwrite            Overwrite existing *.words.yt.tsv (default: off)
#   --dry-run              Show actions without writing files
#   -h, --help             Show help
#
# If one or more files are provided, only those VTT files will be converted.
# Otherwise, scans --source-dir for *\.transcript.en.vtt files.

set -euo pipefail

SOURCE_DIR="pull"
DEST_DIR="generated"
OUT_SUFFIX=".words.yt.tsv"
OVERWRITE=0
DRY_RUN=0

print_help() {
  sed -n '2,35p' "$0"
}

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir)
      SOURCE_DIR="${2:-pull}"; shift 2 || true ;;
    --dest-dir)
      DEST_DIR="${2:-generated}"; shift 2 || true ;;
    --overwrite)
      OVERWRITE=1; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      print_help; exit 0 ;;
    --)
      shift; while [[ $# -gt 0 ]]; do ARGS+=("$1"); shift; done ;;
    -*)
      echo "Unknown option: $1" >&2; print_help; exit 1 ;;
    *)
      ARGS+=("$1"); shift ;;
  esac
done

mkdir -p "$DEST_DIR" 2>/dev/null || true

# Resolve list of VTT files
declare -a VTT_FILES
if [[ ${#ARGS[@]} -gt 0 ]]; then
  for p in "${ARGS[@]}"; do
    if [[ -d "$p" ]]; then
      while IFS= read -r -d '' f; do VTT_FILES+=("$f"); done < <(find "$p" -type f -name "*.transcript.en.vtt" -print0)
    else
      VTT_FILES+=("$p")
    fi
  done
else
  if [[ -d "$SOURCE_DIR" ]]; then
    while IFS= read -r -d '' f; do VTT_FILES+=("$f"); done < <(find "$SOURCE_DIR" -maxdepth 1 -type f -name "*.transcript.en.vtt" -print0)
  fi
fi

if [[ ${#VTT_FILES[@]} -eq 0 ]]; then
  echo "No VTT files found. Checked: ${ARGS[*]:-$SOURCE_DIR}" >&2
  exit 1
fi

convert_one() {
  local vtt="$1"
  local base; base="$(basename "$vtt" .transcript.en.vtt)"
  local out="$DEST_DIR/${base}${OUT_SUFFIX}"

  if [[ -f "$out" && $OVERWRITE -eq 0 ]]; then
    echo "Skip (exists): $out"
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "Would convert: $vtt -> $out"
    return 0
  fi

  # Use embedded Python for robust VTT parsing and words.yt.tsv writing
  python3 - "$vtt" "$out" <<'PY'
import sys, re
from pathlib import Path

def time_to_sec(s: str) -> float:
    s = s.strip().replace(',', '.')
    m = re.match(r"^(\d{2}):(\d{2}):(\d{2})(?:[\.,](\d{1,3}))?", s)
    if not m:
        return 0.0
    h, m_, s_, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    sec = h*3600 + m_*60 + s_
    if ms is not None:
        sec += float(f"0.{ms:0<3}")
    return float(sec)

def parse_vtt(path: Path):
    lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
    i = 0
    segs = []
    while i < len(lines):
        line = lines[i].strip()
        confidence = None
        # Optional NOTE confidence before timestamp
        if line.startswith('NOTE Confidence:'):
            try:
                confidence = float(line.split(':',1)[1].strip())
            except Exception:
                confidence = None
            i += 1
            if i >= len(lines): break
            line = lines[i].strip()

        if '-->' in line:
            # split timestamp, drop trailing style tokens (align:, position:, etc)
            left, right = line.split('-->', 1)
            start_str = left.strip().split()[0]
            end_str = right.strip().split()[0]
            start = time_to_sec(start_str)
            end = time_to_sec(end_str)

            # Capture subsequent non-empty text lines
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].rstrip())
                i += 1
            # Join with space, collapse multiple spaces
            raw = re.sub(r"\s+", " ", " ".join(text_lines)).strip()
            # Strip WebVTT inline tags like <c>...</c>, <\d\d:..> and any <...>
            text = re.sub(r"<[^>]+>", "", raw)
            segs.append({
                'start': start,
                'end': end if end >= start else start,
                'text': text,
                'confidence': confidence if confidence is not None else 0.0,
            })
        i += 1
    return segs

def write_words_tsv(out: Path, segs):
    with out.open('w', encoding='utf-8') as f:
        f.write('start\tend\tword\tseg\tconfidence\tretried\n')
        for si, s in enumerate(segs):
            text = s['text']
            if not text:
                continue
            # Split words on whitespace; preserve punctuation tokens as-is
            tokens = re.findall(r"\S+", text)
            if not tokens:
                continue
            dur = max(0.0, float(s['end']) - float(s['start']))
            step = dur / len(tokens) if len(tokens) > 0 else 0.0
            for idx, tok in enumerate(tokens):
                w_start = float(s['start']) + step * idx
                w_end = w_start + (step if step > 0 else 0.0)
                # In zero-duration segments, write identical start/end
                f.write(f"{w_start:.3f}\t{w_end:.3f}\t{tok}\t{si}\t{float(s['confidence']):.3f}\t0\n")

vtt = Path(sys.argv[1])
out = Path(sys.argv[2])
out.parent.mkdir(parents=True, exist_ok=True)
segs = parse_vtt(vtt)
write_words_tsv(out, segs)
PY

  if [[ -f "$out" ]]; then
    echo "Wrote: $out"
  else
    echo "Failed: $vtt" >&2
    return 1
  fi
}

ec=0
for v in "${VTT_FILES[@]}"; do
  convert_one "$v" || ec=$?
done

exit $ec
