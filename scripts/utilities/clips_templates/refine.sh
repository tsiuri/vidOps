#!/usr/bin/env bash
# shellcheck shell=bash

# Refine already-cut clips by snapping tightly to a given word/phrase
# using faster-whisper word-level timestamps, then trimming with ffmpeg.
#
# Usage:
#   clips.sh refine -q WORD [indir] [outdir]
#   clips.sh refine -q "two words" --occurrence best --pad-pre-ms 30 --pad-post-ms 60 [indir] [outdir]
#
# Requirements:
#   - python3 with faster-whisper installed
#   - ffmpeg, ffprobe

cmd_refine(){
  local WORD=""; local INDIR=""; local OUTDIR=""; local MODEL="${WHISPER_MODEL:-medium}";
  local OCCUR="best"; local MINCONF="0.0"; local PAD_PRE_MS=30; local PAD_POST_MS=60; local COPY=0; local DRY=0
  local EXTRA_FFMPEG=""; local KEEP_EXT=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -q|--query|--word|--phrase) WORD="${2:-}"; shift 2;;
      --model) MODEL="${2:-$MODEL}"; shift 2;;
      --occurrence) OCCUR="${2:-$OCCUR}"; shift 2;;
      --min-conf|--minconf) MINCONF="${2:-$MINCONF}"; shift 2;;
      --pad-pre-ms) PAD_PRE_MS="${2:-$PAD_PRE_MS}"; shift 2;;
      --pad-post-ms) PAD_POST_MS="${2:-$PAD_POST_MS}"; shift 2;;
      --copy) COPY=1; shift;;
      --dry-run|--dry) DRY=1; shift;;
      --ffmpeg-args) EXTRA_FFMPEG="${2:-}"; shift 2;;
      --keep-ext) KEEP_EXT=1; shift;;
      -h|--help)
        cat <<H
Usage: clips.sh refine -q WORD [indir] [outdir]
Refine clips by locating WORD/phrase precisely via faster-whisper.

Options
  -q, --query WORD        Required target word or phrase to locate
  --model NAME            Whisper model (default: ${MODEL})
  --occurrence MODE       first|last|best|N (1-based index). Default: best
  --min-conf FLOAT        Minimum avg confidence for match (default: 0.0)
  --pad-pre-ms N          Extra ms before word start (default: 30)
  --pad-post-ms N         Extra ms after word end (default: 60)
  --copy                  Try stream copy instead of re-encode (keyframe-limited)
  --ffmpeg-args "..."     Extra args appended to ffmpeg
  --keep-ext              Keep original extension (else force .mp4 for video)
  --dry-run               Only report spans; do not write refined media

Inputs
  indir   Directory to scan for media (default: cuts)
  outdir  Output directory (default: cuts_refined)

Outputs
  - Refined media in outdir, named <basename>.refined<ext>
  - Per-file JSON span sidecar: <basename>.wordspan.json
H
        return 0;;
      --) shift; break;;
      *) break;;
    esac
  done

  INDIR="${1:-${CLIPS_OUT:-cuts}}"
  OUTDIR="${2:-cuts_refined}"

  [[ -n "$WORD" ]] || { c_er "refine: --query WORD is required"; exit 1; }
  [[ -d "$INDIR" ]] || { c_er "refine: input dir not found: $INDIR"; exit 1; }
  mkdir -p "$OUTDIR"

  c_do "Refining clips in $INDIR → $OUTDIR (word: $WORD)"

  # Preflight deps
  if ! have ffmpeg; then c_er "refine: ffmpeg not found"; exit 1; fi
  if ! have ffprobe; then c_er "refine: ffprobe not found"; exit 1; fi
  if ! python3 - <<'PY' >/dev/null 2>&1; then
try:
    import faster_whisper  # noqa: F401
except Exception:
    raise SystemExit(1)
PY
  then
    c_er "refine: python module 'faster-whisper' not available. Try: pip install faster-whisper"
    exit 1
  fi

  # Find candidate media files (prefer video containers)
  mapfile -t FILES < <(find "$INDIR" -maxdepth 1 -type f \( -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.webm' -o -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.opus' \) -printf '%p\n' | sort)
  if [[ ${#FILES[@]} -eq 0 ]]; then
    c_er "refine: no media files in $INDIR"; exit 1
  fi

  local p
  for p in "${FILES[@]}"; do
    local bn base ext out spanjson
    bn="$(basename "$p")"; base="${bn%.*}"; ext=".${bn##*.}"
    if [[ $KEEP_EXT -eq 0 ]]; then
      # force .mp4 for any video source; keep audio-only as is
      case "${ext,,}" in
        .mp4|.mkv|.webm) ext=.mp4;;
      esac
    fi
    out="$OUTDIR/${base}.refined${ext}"
    spanjson="$OUTDIR/${base}.wordspan.json"

    # Compute word span via Python/faster-whisper
    local span ssec esec conf
    span=$(python3 - "$p" "$WORD" "$MODEL" "$OCCUR" "$MINCONF" <<'PY' 2>/dev/null || true)
import sys, json, re
from math import inf

path, target, model_name, occur, min_conf = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])

def norm_token(w):
    # normalize word tokens for matching
    w = w.strip().lower()
    # keep alnum and apostrophes, remove punctuation
    w = re.sub(r"[^a-z0-9']+", "", w)
    return w

target_tokens = [t for t in (norm_token(x) for x in re.split(r"\s+", target)) if t]
if not target_tokens:
    print("")
    sys.exit(0)

try:
    from faster_whisper import WhisperModel
except Exception as e:
    sys.stderr.write("faster-whisper not available: %s\n" % (e,))
    print("")
    sys.exit(0)

device = 'cuda' if False else 'auto'
compute_type = 'auto'
model = WhisperModel(model_name, device=device, compute_type=compute_type)

# Run transcription with word timestamps and VAD filtering
segments, info = model.transcribe(
    path,
    word_timestamps=True,
    vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 100}
)

# Gather words into a flat list
words = []  # (word, start, end, prob)
for seg in segments:
    for w in getattr(seg, 'words', []) or []:
        if w.start is None or w.end is None or not w.word:
            continue
        words.append((w.word, float(w.start), float(w.end), float(getattr(w, 'probability', 0.0))))

if not words:
    print("")
    sys.exit(0)

# Build normalized tokens and indices
norm = [(norm_token(w), s, e, p) for (w, s, e, p) in words]

matches = []  # (start, end, avg_prob, i, j)
N = len(norm)
m = len(target_tokens)
for i in range(0, N - m + 1):
    ok = True
    probs = []
    if norm[i][0] != target_tokens[0]:
        continue
    for k in range(m):
        tk = target_tokens[k]
        if norm[i + k][0] != tk:
            ok = False
            break
        probs.append(norm[i + k][3])
    if ok:
        s = norm[i][1]
        e = norm[i + m - 1][2]
        avg = sum(probs) / max(1, len(probs))
        if avg >= min_conf:
            matches.append((s, e, avg, i, i + m - 1))

if not matches:
    print("")
    sys.exit(0)

def pick_match(matches, occur):
    if occur == 'first':
        return min(matches, key=lambda x: (x[0], -x[2]))
    if occur == 'last':
        return max(matches, key=lambda x: (x[0], x[2]))
    if occur == 'best':
        return max(matches, key=lambda x: (x[2], -x[0]))
    # numeric occurrence (1-based)
    try:
        n = int(occur)
        if 1 <= n <= len(matches):
            return sorted(matches, key=lambda x: x[0])[n-1]
    except Exception:
        pass
    return max(matches, key=lambda x: (x[2], -x[0]))

best = pick_match(matches, occur)
out = {
    'start': best[0],
    'end': best[1],
    'avg_conf': best[2],
    'words': [{'w': words[i][0], 's': words[i][1], 'e': words[i][2], 'p': words[i][3]} for i in range(best[3], best[4]+1)],
    'model': model_name,
}
print(json.dumps(out))
PY
)

    if [[ -z "$span" ]]; then
      c_wr "Skip (no match): $bn"
      continue
    fi

    # Parse JSON
    ssec=$(python3 - <<PY "$span"
import sys, json; d=json.loads(sys.argv[1]); print(d['start'])
PY
)
    esec=$(python3 - <<PY "$span"
import sys, json; d=json.loads(sys.argv[1]); print(d['end'])
PY
)
    conf=$(python3 - <<PY "$span"
import sys, json; d=json.loads(sys.argv[1]); print(d.get('avg_conf',0.0))
PY
)

    # Apply micro padding
    local pad_pre_s pad_post_s ss ee
    pad_pre_s=$(python3 - <<PY "$PAD_PRE_MS"
import sys; print(max(0.0, float(sys.argv[1]) / 1000.0))
PY
)
    pad_post_s=$(python3 - <<PY "$PAD_POST_MS"
import sys; print(max(0.0, float(sys.argv[1]) / 1000.0))
PY
)
    ss=$(python3 - <<PY "$ssec" "$pad_pre_s"
import sys; s=float(sys.argv[1]); p=float(sys.argv[2]); print(max(0.0, s - p))
PY
)
    ee=$(python3 - <<PY "$esec" "$pad_post_s"
import sys; e=float(sys.argv[1]); p=float(sys.argv[2]); print(e + p)
PY
)

    # Probe fps to compute frame indices
    local rate fps start_frame end_frame
    rate=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=nk=1:nw=1 -- "$p" 2>/dev/null || true)
    fps=$(python3 - <<PY "$rate"
import sys
val=sys.argv[1].strip()
if not val:
    print(0.0)
else:
    if '/' in val:
        a,b=val.split('/')
        try:
            print(float(a)/float(b))
        except Exception:
            print(0.0)
    else:
        try:
            print(float(val))
        except Exception:
            print(0.0)
PY
)
    start_frame=$(python3 - <<PY "$ss" "$fps"
import sys, math
s=float(sys.argv[1]); fps=float(sys.argv[2])
print(int(math.floor(s*fps)) if fps>0 else -1)
PY
)
    end_frame=$(python3 - <<PY "$ee" "$fps"
import sys, math
e=float(sys.argv[1]); fps=float(sys.argv[2])
print(int(math.ceil(e*fps)) if fps>0 else -1)
PY
)

    # Emit sidecar JSON span
    python3 - <<PY "$spanjson" "$WORD" "$ss" "$ee" "$fps" "$start_frame" "$end_frame" "$p" "$MODEL"
import json, sys, os
out, word, ss, ee, fps, sf, ef, inp, model = sys.argv[1:]
data = {
  'target': word,
  'start_sec': float(ss),
  'end_sec': float(ee),
  'fps': float(fps),
  'start_frame': int(sf),
  'end_frame': int(ef),
  'input': inp,
  'model': model,
}
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, 'w', encoding='utf-8') as f:
  json.dump(data, f, ensure_ascii=False, indent=2)
PY

    if [[ $DRY -eq 1 ]]; then
      c_ok "Found span: $bn :: ${ss}s → ${ee}s (fps ${fps})"
      continue
    fi

    # Perform precise trim (prefer re-encode for accuracy)
    if [[ $COPY -eq 1 ]]; then
      ffmpeg -y -hide_banner -loglevel error -ss "$ss" -to "$ee" -i "$p" -c copy -copyts -avoid_negative_ts 1 $EXTRA_FFMPEG "$out" || {
        c_wr "copy-cut failed, falling back to re-encode: $bn"
        if awk 'BEGIN{exit(ARGV[1]==0)}' "$fps"; then
          ffmpeg -y -hide_banner -loglevel error -ss "$ss" -to "$ee" -i "$p" -sn -dn -c:v libx264 -preset veryfast -crf 18 -c:a aac -b:a 160k $EXTRA_FFMPEG "$out"
        else
          ffmpeg -y -hide_banner -loglevel error -ss "$ss" -to "$ee" -i "$p" -sn -dn -c:a aac -b:a 160k $EXTRA_FFMPEG "$out"
        fi
      }
    else
      if awk 'BEGIN{exit(ARGV[1]==0)}' "$fps"; then
        ffmpeg -y -hide_banner -loglevel error -ss "$ss" -to "$ee" -i "$p" -sn -dn -c:v libx264 -preset veryfast -crf 18 -c:a aac -b:a 160k $EXTRA_FFMPEG "$out"
      else
        ffmpeg -y -hide_banner -loglevel error -ss "$ss" -to "$ee" -i "$p" -sn -dn -c:a aac -b:a 160k $EXTRA_FFMPEG "$out"
      fi
    fi

    c_ok "Refined: $bn → $(basename "$out")"
  done

  c_ok "Refine completed."
}
