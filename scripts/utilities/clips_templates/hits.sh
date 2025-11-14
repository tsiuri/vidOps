#!/usr/bin/env bash
# shellcheck shell=bash

cmd_hits(){
  local TERMS="like"; local WIN=""; local OUT="wanted_clips.tsv"; local SOURCE="auto"; local WIN_SET=0; local EXACT=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -q|--query) TERMS="${2:-$TERMS}"; shift 2;;
      -w|--window) WIN="${2:-$WIN}"; shift 2;;
      -o|--out) OUT="${2:-$OUT}"; shift 2;;
      --source)
        SOURCE="${2:-$SOURCE}"; shift 2;;
      --words|--from-words)
        SOURCE="words"; shift;;
      --exact)
        EXACT=1; shift;;
      -h|--help) cat <<E
Usage: clips.sh hits [-q "dog,prong collar"] [-w SECONDS] [-o wanted_clips.tsv] [--words] [--exact]
Find hits either from legacy *.tslog.txt or precise *.words.tsv files. With --exact, use exact word timestamps from words.tsv.

Sources
  auto (default): prefer *.words.tsv when present, else *.tslog.txt
  words: use per-word timestamps from files named *words.tsv or words.tsv
  tslog: use transcript logs (*.tslog.txt) with [HH:MM:SS] lines

Windows
  -w sets clip length in seconds. With words source, default is 1s.
  With tslog source, default is 7s. PAD_START/PAD_END are also applied.

Exact (words source only)
  --exact: instead of centering a fixed window, use the exact start→end span
           from words.tsv for the matched word, then apply PAD_START/PAD_END.
           If 'end' is missing for a token, use start as both bounds (plus padding).
E
        return 0;;
      *) c_er "hits: unknown arg $1"; exit 1;;
    esac
  done
  # Determine source and default window
  local HAVE_WORDS=0
  if find generated -type f \( -name '*words.tsv' -o -name 'words.tsv' \) -print -quit 2>/dev/null | grep -q .; then
    HAVE_WORDS=1
  elif find . -type f \( -name '*words.tsv' -o -name 'words.tsv' \) -print -quit 2>/dev/null | grep -q .; then
    HAVE_WORDS=1
  fi
  if [[ "$SOURCE" == "auto" ]]; then
    if [[ $HAVE_WORDS -eq 1 ]]; then SOURCE="words"; else SOURCE="tslog"; fi
  fi
  if [[ -z "$WIN" ]]; then
    if [[ "$SOURCE" == "words" ]]; then WIN="1"; else WIN="7"; fi
  fi

  if [[ $EXACT -eq 1 && "$SOURCE" != "words" ]]; then
    c_wr "--exact is only applicable with words source; ignoring if no words.tsv present."
  fi

  local WDESC
  if [[ $EXACT -eq 1 && "$SOURCE" == "words" ]]; then
    WDESC="exact"
  else
    WDESC="${WIN}s"
  fi
  c_do "Finding hits (source: $SOURCE | terms: $TERMS | window: ${WDESC}) → $OUT"
  local tmp; tmp="$(mktemp)"

  echo -e "url\tstart\tend\tlabel\tsource_caption" > "$tmp"
  IFS=',' read -r -a ARR <<< "$TERMS"

  if [[ "$SOURCE" == "words" ]]; then
    # Parse *.words.tsv files (flexible columns). Case-insensitive term match.
    # Accepted columns (any of): word|token|term, start|start_sec|time|ts, end|end_sec, id|ytid|video_id, url|webpage_url
    HITS_TERMS="$TERMS" EXACT="$EXACT" python3 - "$WIN" "$PAD_START" "$PAD_END" "$OUT" >> "$tmp" <<'PY'
import sys, os, csv, re
csv.field_size_limit(10 * 1024 * 1024)  # Increase limit to 10MB to handle large fields
WIN=float(sys.argv[1]); PAD_START=float(sys.argv[2]); PAD_END=float(sys.argv[3])
terms = [t.strip() for t in os.environ.get('HITS_TERMS','').split(',') if t.strip()]
terms_lower = [t.lower() for t in terms]
EXACT = int(os.environ.get('EXACT','0') or 0)

def find_cols(header):
    h=[c.strip().lower() for c in header]
    def pick(*names):
        for n in names:
            if n in h:
                return h.index(n)
        return -1
    return {
        'word': pick('word','token','term'),
        'start': pick('start','start_sec','time','ts','offset'),
        'end': pick('end','end_sec','stop','to'),
        'id': pick('ytid','video_id','id'),
        'url': pick('url','webpage_url'),
    }

def guess_id_from_path(path):
    base=os.path.basename(path)
    m=re.match(r'([A-Za-z0-9_-]{11})__', base)
    if m:
        return m.group(1)
    # try parent dir
    m=re.match(r'([A-Za-z0-9_-]{11})__', os.path.basename(os.path.dirname(path)))
    if m:
        return m.group(1)
    return ''

def emit(url, start, end, label, source):
    sys.stdout.write(f"{url}\t{start:.3f}\t{end:.3f}\t{label}\t{source}\n")

def iter_word_rows(path):
    with open(path,'r',encoding='utf-8',errors='ignore',newline='') as fh:
        sniffer = csv.Sniffer()
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.excel_tab
        try:
            dialect = sniffer.sniff(sample, delimiters='\t,')
        except Exception:
            pass
        # Disable quoting for TSV files to handle literal quotes in data
        reader = csv.reader(fh, dialect, quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            return
        cols = find_cols(header)
        for row in reader:
            if not row:
                continue
            def get(idx, default=''):
                if 0 <= idx < len(row):
                    return row[idx]
                return default
            w = get(cols['word']).strip() if cols['word']!=-1 else ''
            if w and terms_lower and all(t not in w.lower() for t in terms_lower):
                continue
            s_txt = get(cols['start'])
            e_txt = get(cols['end'])
            try:
                s = float(s_txt)
            except Exception:
                continue
            e = None
            try:
                e = float(e_txt)
            except Exception:
                e = None
            vid = get(cols['id']).strip()
            url = get(cols['url']).strip()
            if not url and not vid:
                vid = guess_id_from_path(path)
            if not url and vid:
                url = f"https://www.youtube.com/watch?v={vid}"
            if EXACT:
                # Use exact token span with small padding
                start = max(0.0, s - PAD_START)
                end = (e if e is not None else s) + PAD_END
            else:
                # Build fixed window centered on midpoint (or at time if no end)
                if e is None:
                    center = s
                else:
                    center = (s + e) / 2.0
                start = max(0.0, center - WIN/2.0 - PAD_START)
                end = center + WIN/2.0 + PAD_END
            label = w or (terms[0] if terms else 'hit')
            source = w or ''
            emit(url, start, end, label, source)

def find_files():
    roots = ['generated', '.']
    seen=set()
    for r in roots:
        for dirpath,_,files in os.walk(r):
            for name in files:
                if name == 'words.tsv' or name.endswith('words.tsv'):
                    p=os.path.join(dirpath,name)
                    if p in seen: continue
                    seen.add(p)
                    yield p

for p in find_files():
    iter_word_rows(p)
PY
  else
    # Legacy tslog search
    while IFS= read -r -d '' ts; do
      local stem="${ts%.tslog.txt}"
      local id=""
      if [[ "$(basename "$stem")" =~ ^([A-Za-z0-9_-]{11})__ ]]; then id="${BASH_REMATCH[1]}"; fi
      local url=""; [[ -n "$id" ]] && url="$(src_url_for_id "$id")"

      for term in "${ARR[@]}"; do
        local t; t="$(echo "$term" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [[ -z "$t" ]] && continue

        if have rg; then
          rg -n --fixed-strings "$t" "$ts" | while IFS=: read -r ln _; do
            local line; line="$(sed -n "${ln}p" "$ts")"
            [[ "$line" =~ \[([0-9]{2}):([0-9]{2}):([0-9]{2})\]\ (.*)$ ]] || continue
            local h="${BASH_REMATCH[1]}"; local m="${BASH_REMATCH[2]}"; local s="${BASH_REMATCH[3]}"
            local cap="${BASH_REMATCH[4]}"
            local sec=$((10#$h*3600 + 10#$m*60 + 10#$s))
            local start end
            start=$(python3 - <<PY "$sec" "$WIN" "$PAD_START"
import sys; sec=float(sys.argv[1]); win=float(sys.argv[2]); pad=float(sys.argv[3])
print(max(0.0, sec - win/2.0 - pad))
PY
)
            end=$(python3 - <<PY "$sec" "$WIN" "$PAD_END"
import sys; sec=float(sys.argv[1]); win=float(sys.argv[2]); pad=float(sys.argv[3])
print(sec + win/2.0 + pad)
PY
)
            printf "%s\t%.3f\t%.3f\t%s\t%s\n" "${url:-}" "$start" "$end" "$t" "$cap" >> "$tmp"
          done
        else
          grep -n -F "$t" "$ts" | while IFS=: read -r ln _; do
            local line; line="$(sed -n "${ln}p" "$ts")"
            [[ "$line" =~ \[([0-9]{2}):([0-9]{2}):([0-9]{2})\]\ (.*)$ ]] || continue
            local h="${BASH_REMATCH[1]}"; local m="${BASH_REMATCH[2]}"; local s="${BASH_REMATCH[3]}"
            local cap="${BASH_REMATCH[4]}"
            local sec=$((10#$h*3600 + 10#$m*60 + 10#$s))
            local start end
            start=$(python3 - <<PY "$sec" "$WIN" "$PAD_START"
import sys; sec=float(sys.argv[1]); win=float(sys.argv[2]); pad=float(sys.argv[3])
print(max(0.0, sec - win/2.0 - pad))
PY
)
            end=$(python3 - <<PY "$sec" "$WIN" "$PAD_END"
import sys; sec=float(sys.argv[1]); win=float(sys.argv[2]); pad=float(sys.argv[3])
print(sec + win/2.0 + pad)
PY
)
            printf "%s\t%.3f\t%.3f\t%s\t%s\n" "${url:-}" "$start" "$end" "$t" "$cap" >> "$tmp"
          done
        fi
      done
    done < <(find generated -type f -name '*.tslog.txt' -print0 2>/dev/null || find . -type f -name '*.tslog.txt' -print0)
  fi

  # Merge overlapping clips
  python3 - "$tmp" "$OUT" <<'PYMERGE'
import sys, csv
from collections import defaultdict

infile = sys.argv[1]
outfile = sys.argv[2]

# Read all clips
clips_by_url = defaultdict(list)
with open(infile, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    for row in reader:
        url = row['url']
        try:
            start = float(row['start'])
            end = float(row['end'])
            label = row['label']
            source = row.get('source_caption', '')
            clips_by_url[url].append({
                'start': start,
                'end': end,
                'label': label,
                'source': source
            })
        except (ValueError, KeyError):
            continue

# Merge overlapping clips for each URL
merged = []
for url, clips in clips_by_url.items():
    # Sort by start time
    clips.sort(key=lambda c: c['start'])

    # Merge overlapping intervals
    if not clips:
        continue

    current = clips[0].copy()
    current_labels = [current['label']]
    current_sources = [current['source']]

    for clip in clips[1:]:
        # Check if clips overlap (current.end >= clip.start means they touch or overlap)
        if current['end'] >= clip['start']:
            # Merge: extend end, combine labels
            current['end'] = max(current['end'], clip['end'])
            if clip['label'] not in current_labels:
                current_labels.append(clip['label'])
            if clip['source'] and clip['source'] not in current_sources:
                current_sources.append(clip['source'])
        else:
            # No overlap, save current and start new
            current['label'] = ', '.join(current_labels)
            current['source'] = current_sources[0] if current_sources else ''
            merged.append((url, current))
            current = clip.copy()
            current_labels = [current['label']]
            current_sources = [current['source']]

    # Don't forget last clip
    current['label'] = ', '.join(current_labels)
    current['source'] = current_sources[0] if current_sources else ''
    merged.append((url, current))

# Write output
with open(outfile, 'w', encoding='utf-8', newline='') as f:
    writer = csv.writer(f, delimiter='\t')
    writer.writerow(['url', 'start', 'end', 'label', 'source_caption'])
    for url, clip in merged:
        writer.writerow([
            url,
            f"{clip['start']:.3f}",
            f"{clip['end']:.3f}",
            clip['label'],
            clip['source']
        ])
PYMERGE
  rm -f "$tmp"
  c_ok "Wrote $OUT (overlapping clips merged)"
}
