#!/usr/bin/env python3
import csv
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "."))

ROOT = PROJECT_ROOT
GENERATED = ROOT / 'generated'
LOGS = ROOT / 'logs' / 'db'
out_transcripts = LOGS / 'transcripts.tsv'
out_words = LOGS / 'words_export.tsv'

ytid_re = re.compile(r'([A-Za-z0-9_-]{11})__')

def ytid_from_path(p: Path):
    m = ytid_re.search(p.name)
    if m:
        return m.group(1)
    m = ytid_re.search(p.parent.name)
    return m.group(1) if m else ''

def file_bytes(p: Path):
    try:
        return p.stat().st_size
    except Exception:
        return 0

def pick_best_words_by_vid():
    best = {}
    if not GENERATED.exists():
        return best
    for root, _, files in os.walk(GENERATED):
        for name in files:
            if not (name.endswith('.words.tsv') or name.endswith('.words.yt.tsv') or name == 'words.tsv'):
                continue
            p = Path(root) / name
            vid = ytid_from_path(p)
            if not vid:
                continue
            kind = 'yt' if name.endswith('.words.yt.tsv') else 'whisper'
            size = file_bytes(p)
            prev = best.get(vid)
            if prev is None:
                best[vid] = (p, kind, size)
            else:
                prev_p, prev_kind, prev_size = prev
                if prev_kind == 'whisper' and kind == 'yt':
                    continue
                elif prev_kind == 'yt' and kind == 'whisper':
                    best[vid] = (p, kind, size)
                else:
                    if size > prev_size:
                        best[vid] = (p, kind, size)
    return best

def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    selection = pick_best_words_by_vid()

    with out_transcripts.open('w', encoding='utf-8', newline='') as ft, \
         out_words.open('w', encoding='utf-8', newline='') as fw:
        tw = csv.writer(ft, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
        ww = csv.writer(fw, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
        tw.writerow(['ytid','kind','lang','path','bytes','word_count','segment_count'])
        ww.writerow(['ytid','source','idx','word','start_sec','end_sec','confidence','segment_id','path'])

        for vid in sorted(selection.keys()):
            p, kind, _ = selection[vid]
            src_kind = 'words_whisper' if kind == 'whisper' else 'words_ytt'
            word_count = 0
            seg_counts = set()
            try:
                with p.open('r', encoding='utf-8', errors='ignore', newline='') as f:
                    r = csv.reader(f, delimiter='\t')
                    header = next(r, None)
                    if not header:
                        continue
                    colmap = {h.strip().lower(): i for i,h in enumerate(header)}
                    idx = 0
                    for row in r:
                        if not row:
                            continue
                        def get(name, default=''):
                            i = colmap.get(name)
                            return row[i] if i is not None and 0 <= i < len(row) else default
                        try:
                            start = float((get('start','') or '0'))
                            end = float((get('end','') or '0'))
                        except Exception:
                            continue
                        word = (get('word','') or '').strip().lower()
                        seg = get('seg','')
                        conf = get('confidence','')
                        idx += 1
                        word_count += 1
                        if seg:
                            seg_counts.add(seg)
                        ww.writerow([vid, kind, idx, word, f'{start:.3f}', f'{end:.3f}', conf, seg, str(p.resolve())])
            except Exception:
                continue
            tw.writerow([vid, src_kind, 'en', str(p.resolve()), file_bytes(p), word_count, len(seg_counts) or ''])

    print(f'Wrote {out_transcripts} and {out_words} (videos: {len(selection)})')

if __name__ == '__main__':
    main()
