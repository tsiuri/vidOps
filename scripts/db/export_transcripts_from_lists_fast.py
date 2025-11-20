#!/usr/bin/env python3
import os
import re
from pathlib import Path
import csv

LOGS = Path('logs') / 'db'
GENERATED = Path('generated')
OUT = LOGS / 'transcripts.tsv'

ytid_re = re.compile(r'([A-Za-z0-9_-]{11})__')

def ytid_from_path(p: Path) -> str:
    m = ytid_re.search(p.name)
    if m:
        return m.group(1)
    m = ytid_re.search(p.parent.name)
    return m.group(1) if m else ''

def file_bytes(p: Path) -> int:
    try:
        return p.stat().st_size
    except Exception:
        return 0

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # Discover transcripts under generated/: prefer words.tsv (whisper) over words.yt.tsv (captions)
    # Emit exactly one row per video id (ytid).
    best_by_vid = {}
    if GENERATED.exists():
        for root, _, files in os.walk(GENERATED):
            for name in files:
                if not (name.endswith('.words.tsv') or name.endswith('.words.yt.tsv') or name == 'words.tsv'):
                    continue
                p = Path(root) / name
                vid = ytid_from_path(p)
                if not vid:
                    continue
                if name.endswith('.words.yt.tsv'):
                    kind = 'words_ytt'       # fallback
                else:
                    kind = 'words_whisper'   # preferred
                lang = 'en'
                size = file_bytes(p)
                cand = (vid, kind, lang, str(p.resolve()), size, '', '')
                prev = best_by_vid.get(vid)
                if prev is None:
                    best_by_vid[vid] = cand
                else:
                    prev_kind = prev[1]
                    prev_size = prev[4]
                    # Prefer whisper over ytt; within same kind, keep the larger file
                    if prev_kind == 'words_whisper' and kind == 'words_ytt':
                        pass  # keep prev
                    elif prev_kind == 'words_ytt' and kind == 'words_whisper':
                        best_by_vid[vid] = cand
                    else:
                        # same kind: choose larger file
                        if size > prev_size:
                            best_by_vid[vid] = cand

    # Write manifest
    with OUT.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t', lineterminator='\n')
        w.writerow(['ytid','kind','lang','path','bytes','word_count','segment_count'])
        rows = list(best_by_vid.values())
        for row in sorted(rows, key=lambda r: (r[0], r[1], r[2])):
            w.writerow(row)

    print(f'Wrote {OUT} ({len(best_by_vid)} videos)')

if __name__ == '__main__':
    main()
