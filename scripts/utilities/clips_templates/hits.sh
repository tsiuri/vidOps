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
Find hits from *.words.tsv or *.tslog.txt files. With --exact, use exact word timestamps from words(.yt).tsv.

Sources
  auto (default): per video ID, prefer *.words.tsv, then *.tslog.txt, then *.words.yt.tsv
  words: use per-word timestamps from files named *words.tsv, *words.yt.tsv, or words.tsv
  tslog: use transcript logs (*.tslog.txt) with [HH:MM:SS] lines

Windows
  -w sets clip length in seconds. With words source, default is 1s.
  With tslog source, default is 7s. PAD_START/PAD_END are also applied.

Exact (words source only)
  --exact: instead of centering a fixed window, use the exact start→end span
           from words(.yt).tsv for the matched word, then apply PAD_START/PAD_END.
           If 'end' is missing for a token, use start as both bounds (plus padding).
E
        return 0;;
      *) c_er "hits: unknown arg $1"; exit 1;;
    esac
  done
  # Set default window if not specified
  if [[ -z "$WIN" ]]; then
    if [[ "$SOURCE" == "words" || "$SOURCE" == "auto" ]]; then WIN="1"; else WIN="7"; fi
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

  if [[ "$SOURCE" == "auto" ]]; then
    c_do "Finding hits (source: auto-detect per video | terms: $TERMS | window: ${WDESC}) → $OUT"
  else
    c_do "Finding hits (source: $SOURCE | terms: $TERMS | window: ${WDESC}) → $OUT"
  fi

  local tmp; tmp="$(mktemp)"
  # Internal tmp includes hit_start/hit_end for smarter merging
  echo -e "url\tstart\tend\tlabel\tsource_caption\tsource_type\thit_start\thit_end" > "$tmp"
  IFS=',' read -r -a ARR <<< "$TERMS"

  # Universal Python processor that handles words, tslog, and auto sources (VTT removed)
  HITS_TERMS="$TERMS" EXACT="$EXACT" HITS_SOURCE="$SOURCE" python3 - "$WIN" "$PAD_START" "$PAD_END" "$OUT" >> "$tmp" <<'PY'
import sys, os, csv, re
from collections import defaultdict
csv.field_size_limit(10 * 1024 * 1024)

WIN = float(sys.argv[1])
PAD_START = float(sys.argv[2])
PAD_END = float(sys.argv[3])
terms = [t.strip() for t in os.environ.get('HITS_TERMS', '').split(',') if t.strip()]
terms_lower = [t.lower() for t in terms]
EXACT = int(os.environ.get('EXACT', '0') or 0)
SOURCE = os.environ.get('HITS_SOURCE', 'auto')

def guess_id_from_path(path):
    """Extract YouTube video ID from filename (11 char alphanumeric)"""
    base = os.path.basename(path)
    m = re.match(r'([A-Za-z0-9_-]{11})__', base)
    if m:
        return m.group(1)
    m = re.match(r'([A-Za-z0-9_-]{11})__', os.path.basename(os.path.dirname(path)))
    if m:
        return m.group(1)
    return ''

def emit(url, start, end, label, source, source_type, hit_start, hit_end):
    """Output a hit in TSV format (with hit span)"""
    sys.stdout.write(
        f"{url}\t{start:.3f}\t{end:.3f}\t{label}\t{source}\t{source_type}\t{hit_start:.3f}\t{hit_end:.3f}\n"
    )

def vtt_time_to_seconds(time_str):
    """Convert VTT timestamp (HH:MM:SS.mmm or MM:SS.mmm) to seconds"""
    parts = time_str.strip().split(':')
    if len(parts) == 3:  # HH:MM:SS.mmm
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:  # MM:SS.mmm
        m, s = parts
        return int(m) * 60 + float(s)
    else:
        return 0.0

def process_vtt_file(path, vid):
    """Parse VTT file and emit hits"""
    url = f"https://www.youtube.com/watch?v={vid}" if vid else ""
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Look for timestamp line: HH:MM:SS.mmm --> HH:MM:SS.mmm
        if '-->' in line:
            try:
                start_str, end_str = line.split('-->')
                timestamp_start = vtt_time_to_seconds(start_str)
                timestamp_end = vtt_time_to_seconds(end_str)

                # Next line(s) contain caption text
                caption_lines = []
                i += 1
                while i < len(lines) and lines[i].strip() and '-->' not in lines[i]:
                    caption_lines.append(lines[i].strip())
                    i += 1
                caption = ' '.join(caption_lines)

                # Check if any term matches
                if not terms_lower or any(t in caption.lower() for t in terms_lower):
                    # Use midpoint of caption timestamp for window
                    center = (timestamp_start + timestamp_end) / 2.0
                    start = max(0.0, center - WIN/2.0 - PAD_START)
                    end = center + WIN/2.0 + PAD_END

                    # Label is the first matching term, or the caption snippet
                    label = terms[0] if terms else 'hit'
                    for term in terms:
                        if term.lower() in caption.lower():
                            label = term
                            break

                    # Use the actual caption span as the hit span
                    emit(url, start, end, label, caption, 'vtt', timestamp_start, timestamp_end)
                continue
            except Exception:
                pass
        i += 1

def process_tslog_file(path, vid):
    """Parse legacy tslog format [HH:MM:SS] caption"""
    url = f"https://www.youtube.com/watch?v={vid}" if vid else ""
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            # Match: [HH:MM:SS] caption text
            m = re.match(r'\[(\d{2}):(\d{2}):(\d{2})\]\s+(.*)$', line)
            if not m:
                continue
            h, m_min, s, caption = m.groups()
            sec = int(h) * 3600 + int(m_min) * 60 + int(s)

            # Check if any term matches
            if not terms_lower or any(t in caption.lower() for t in terms_lower):
                start = max(0.0, sec - WIN/2.0 - PAD_START)
                end = sec + WIN/2.0 + PAD_END

                label = terms[0] if terms else 'hit'
                for term in terms:
                    if term.lower() in caption.lower():
                        label = term
                        break

                # Hit is a point for tslog
                emit(url, start, end, label, caption, 'tslog', sec, sec)

def find_cols(header):
    """Find column indices in TSV header (flexible names)"""
    h = [c.strip().lower() for c in header]
    def pick(*names):
        for n in names:
            if n in h:
                return h.index(n)
        return -1
    return {
        'word': pick('word', 'token', 'term'),
        'start': pick('start', 'start_sec', 'time', 'ts', 'offset'),
        'end': pick('end', 'end_sec', 'stop', 'to'),
        'id': pick('ytid', 'video_id', 'id'),
        'url': pick('url', 'webpage_url'),
    }

def process_words_file(path):
    """Parse words.tsv file with per-word timestamps, supporting phrase matching"""
    with open(path, 'r', encoding='utf-8', errors='ignore', newline='') as fh:
        sniffer = csv.Sniffer()
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.excel_tab
        try:
            dialect = sniffer.sniff(sample, delimiters='\t,')
        except Exception:
            pass
        reader = csv.reader(fh, dialect, quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            return

        cols = find_cols(header)

        # Read all rows into memory for phrase matching
        all_words = []
        for row in reader:
            if not row:
                continue
            def get(idx, default=''):
                return row[idx] if 0 <= idx < len(row) else default

            w = get(cols['word']).strip() if cols['word'] != -1 else ''
            if not w:
                continue

            try:
                s = float(get(cols['start']))
            except Exception:
                continue

            try:
                e = float(get(cols['end'])) if cols['end'] != -1 and get(cols['end']) else None
            except Exception:
                e = None

            vid = get(cols['id']).strip()
            url = get(cols['url']).strip()
            if not url and not vid:
                vid = guess_id_from_path(path)
            if not url and vid:
                url = f"https://www.youtube.com/watch?v={vid}"

            all_words.append({
                'word': w,
                'start': s,
                'end': e,
                'url': url,
                'vid': vid
            })

        # For each search term, find matching phrases
        for term in terms:
            term_words = term.strip().split()  # Split phrase into words
            term_words_lower = [tw.lower() for tw in term_words]

            # Sliding window to find phrase matches
            for i in range(len(all_words) - len(term_words) + 1):
                # Check if the next N words match the phrase (substring matching)
                window_words = [all_words[i + j]['word'].lower() for j in range(len(term_words))]

                # Match if search term is contained in the word (like old behavior)
                match = all(term_words_lower[j] in window_words[j] for j in range(len(term_words)))

                if match:
                    # Found a match! Get the span of the entire phrase
                    first_word = all_words[i]
                    last_word = all_words[i + len(term_words) - 1]

                    phrase_start = first_word['start']
                    phrase_end = last_word['end'] if last_word['end'] is not None else last_word['start']

                    url = first_word['url']

                    if EXACT:
                        start = max(0.0, phrase_start - PAD_START)
                        end = phrase_end + PAD_END
                    else:
                        center = (phrase_start + phrase_end) / 2.0
                        start = max(0.0, center - WIN/2.0 - PAD_START)
                        end = center + WIN/2.0 + PAD_END

                    label = term
                    source = ' '.join([all_words[i + j]['word'] for j in range(len(term_words))])
                    emit(url, start, end, label, source, 'words', phrase_start, phrase_end)

def discover_video_ids():
    """Discover all video IDs from media files in pull/"""
    ids = set()
    # Supported media extensions (from transcribe command)
    media_exts = ('.opus', '.mp4', '.mkv', '.mov', '.avi', '.mp3', '.wav', '.m4a',
                  '.webm', '.flv', '.ogg', '.ogv', '.3gp')

    if os.path.isdir('pull'):
        for fname in os.listdir('pull'):
            # Check if it's a media file
            if any(fname.endswith(ext) for ext in media_exts):
                vid = guess_id_from_path(fname)
                if vid:
                    ids.add(vid)

    # Also check generated/ for any video IDs we might have transcribed
    if os.path.isdir('generated'):
        for fname in os.listdir('generated'):
            vid = guess_id_from_path(fname)
            if vid:
                ids.add(vid)

    return ids

def find_best_source_for_video(vid):
    """For a video ID, return (source_type, path) in priority order"""
    # Priority 1: words.tsv in generated/ (excluding .yt.tsv)
    for root in ['generated', '.']:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if (fname == 'words.tsv' or (fname.endswith('.words.tsv') and not fname.endswith('.words.yt.tsv'))) and vid in fname:
                    return ('words', os.path.join(dirpath, fname))

    # Priority 2: tslog.txt in generated/
    for root in ['generated', '.']:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if fname.endswith('.tslog.txt') and vid in fname:
                    return ('tslog', os.path.join(dirpath, fname))

    # Priority 3: words.yt.tsv (generated by convert-captions)
    for root in ['generated', '.']:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if (fname == 'words.yt.tsv' or fname.endswith('words.yt.tsv')) and vid in fname:
                    return ('words', os.path.join(dirpath, fname))

    return (None, None)

def find_all_files_by_source(source_type):
    """Find all files of a specific type"""
    files = []
    seen_base_names = set()

    if source_type == 'words':
        # Collect all candidates first
        candidates = []
        for root in ['generated', '.']:
            if not os.path.isdir(root):
                continue
            for dirpath, _, fnames in os.walk(root):
                for fname in fnames:
                    if fname == 'words.tsv' or fname.endswith('.words.tsv') or fname.endswith('.words.yt.tsv'):
                        full_path = os.path.join(dirpath, fname)
                        # Extract base name (remove .words.tsv or .words.yt.tsv)
                        if fname.endswith('.words.yt.tsv'):
                            base = fname[:-len('.words.yt.tsv')]
                            priority = 2
                        elif fname.endswith('.words.tsv'):
                            base = fname[:-len('.words.tsv')]
                            priority = 1
                        else:
                            base = fname
                            priority = 1
                        candidates.append((priority, base, full_path))

        # Sort by base name and priority (lower priority number = higher precedence)
        candidates.sort(key=lambda x: (x[1], x[0]))

        # Deduplicate: for each base name, only take the highest priority file
        for priority, base, full_path in candidates:
            if base not in seen_base_names:
                seen_base_names.add(base)
                files.append(full_path)

    elif source_type == 'tslog':
        for root in ['generated', '.']:
            if not os.path.isdir(root):
                continue
            for dirpath, _, fnames in os.walk(root):
                for fname in fnames:
                    if fname.endswith('.tslog.txt'):
                        files.append(os.path.join(dirpath, fname))

    # VTT source removed

    return files

# Main processing logic
if SOURCE == 'auto':
    # Auto-detect: discover video IDs and pick best source for each
    video_ids = discover_video_ids()
    for vid in video_ids:
        source_type, path = find_best_source_for_video(vid)
        if not source_type:
            continue

        if source_type == 'words':
            process_words_file(path)
        elif source_type == 'tslog':
            process_tslog_file(path, vid)
        # words source covers both words.tsv and words.yt.tsv

elif SOURCE == 'words':
    for path in find_all_files_by_source('words'):
        process_words_file(path)

elif SOURCE == 'tslog':
    for path in find_all_files_by_source('tslog'):
        vid = guess_id_from_path(path)
        process_tslog_file(path, vid)

elif SOURCE == 'vtt':
    # Backward compatibility message
    sys.stderr.write('VTT source support has been removed. Use convert-captions then --words.\n')
PY

  # Merge overlapping clips
  python3 - "$tmp" "$OUT" <<'PYMERGE'
import sys, csv
from collections import defaultdict

infile = sys.argv[1]
outfile = sys.argv[2]

# Read all clips (with hit spans)
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
            source_type = row.get('source_type', '')
            hstart = float(row.get('hit_start', row['start']))
            hend = float(row.get('hit_end', row['end']))
            clips_by_url[url].append({
                'start': start,
                'end': end,
                'label': label,
                'source': source,
                'source_type': source_type,
                'hstart': hstart,
                'hend': hend
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
    current_types = [current['source_type']]

    for clip in clips[1:]:
        # Overlap in window AND overlap in hit span
        window_overlap = current['end'] >= clip['start']
        hit_overlap = not (current['hend'] < clip['hstart'] or clip['hend'] < current['hstart'])
        if window_overlap and hit_overlap:
            # Merge: extend end, combine labels
            current['end'] = max(current['end'], clip['end'])
            # Union hit spans for subsequent checks
            current['hstart'] = min(current['hstart'], clip['hstart'])
            current['hend'] = max(current['hend'], clip['hend'])
            if clip['label'] not in current_labels:
                current_labels.append(clip['label'])
            if clip['source'] and clip['source'] not in current_sources:
                current_sources.append(clip['source'])
            if clip['source_type'] and clip['source_type'] not in current_types:
                current_types.append(clip['source_type'])
        else:
            # No overlap, save current and start new
            current['label'] = ', '.join(current_labels)
            current['source'] = current_sources[0] if current_sources else ''
            current['source_type'] = ', '.join(sorted(set(current_types)))
            merged.append((url, current))
            current = clip.copy()
            current_labels = [current['label']]
            current_sources = [current['source']]
            current_types = [current['source_type']]

    # Don't forget last clip
    current['label'] = ', '.join(current_labels)
    current['source'] = current_sources[0] if current_sources else ''
    current['source_type'] = ', '.join(sorted(set(current_types)))
    merged.append((url, current))

# Write output (preserve original 6 columns for downstream tools)
with open(outfile, 'w', encoding='utf-8', newline='') as f:
    writer = csv.writer(f, delimiter='\t')
    writer.writerow(['url', 'start', 'end', 'label', 'source_caption', 'source_type'])
    for url, clip in merged:
        writer.writerow([
            url,
            f"{clip['start']:.3f}",
            f"{clip['end']:.3f}",
            clip['label'],
            clip['source'],
            clip['source_type']
        ])
PYMERGE
  rm -f "$tmp"
  c_ok "Wrote $OUT (overlapping clips merged)"
}
