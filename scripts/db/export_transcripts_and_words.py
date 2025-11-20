#!/usr/bin/env python3
import csv
import os
import re
from pathlib import Path

ROOT = Path('.')
GENERATED = ROOT / 'generated'
PULL = ROOT / 'pull'
LOGS = ROOT / 'logs' / 'db'

out_transcripts = LOGS / 'transcripts.tsv'
out_words = LOGS / 'words_export.tsv'

ytid_re = re.compile(r'([A-Za-z0-9_-]{11})__')

def ytid_from_path(p: Path):
    m = ytid_re.search(p.name)
    if m:
        return m.group(1)
    # try parent dir
    m = ytid_re.search(p.parent.name)
    return m.group(1) if m else ''

def file_bytes(p: Path):
    try:
        return p.stat().st_size
    except Exception:
        return 0

def iter_words_files():
    if not GENERATED.exists():
        return
    for p in GENERATED.rglob('*.words*.tsv'):
        # classify source
        src = 'yt' if p.name.endswith('.words.yt.tsv') else 'whisper'
        yield p, src

def iter_vtt_files():
    if not PULL.exists():
        return
    for p in PULL.rglob('*.transcript.*.vtt'):
        yield p

def parse_lang_from_vtt_name(name: str) -> str:
    # e.g., XYZ.transcript.en.vtt -> en
    parts = name.split('.transcript.')
    if len(parts) > 1:
        rest = parts[1]
        lang = rest.replace('.vtt', '')
        return lang
    return 'en'

def main():
    LOGS.mkdir(parents=True, exist_ok=True)

    # Write transcripts.tsv header
    with out_transcripts.open('w', encoding='utf-8', newline='') as ft, \
         out_words.open('w', encoding='utf-8', newline='') as fw:
        tw = csv.writer(ft, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
        ww = csv.writer(fw, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
        tw.writerow(['ytid','kind','lang','path','bytes','word_count','segment_count'])
        ww.writerow(['ytid','source','idx','word','start_sec','end_sec','confidence','segment_id','path'])

        # VTT transcripts
        for p in iter_vtt_files():
            vid = ytid_from_path(p)
            if not vid:
                continue
            lang = parse_lang_from_vtt_name(p.name)
            tw.writerow([vid, 'vtt', lang, str(p), file_bytes(p), '', ''])

        # Words files (both yt and whisper)
        for p, src in iter_words_files():
            vid = ytid_from_path(p)
            if not vid:
                continue
            # count segments later; load file
            word_count = 0
            seg_counts = set()
            try:
                with p.open('r', encoding='utf-8', errors='ignore', newline='') as f:
                    r = csv.reader(f, delimiter='\t')
                    header = next(r, None)
                    colmap = {h.strip().lower(): i for i,h in enumerate(header or [])}
                    idx = 0
                    for row in r:
                        if not row:
                            continue
                        def get(name, default=''):
                            i = colmap.get(name)
                            return row[i] if i is not None and 0 <= i < len(row) else default
                        try:
                            start = float(get('start','0') or '0')
                            end = float(get('end','0') or '0')
                        except Exception:
                            continue
                        word = (get('word','') or '').strip()
                        seg = get('seg','')
                        conf = get('confidence','')
                        idx += 1
                        word_count += 1
                        if seg:
                            seg_counts.add(seg)
                        ww.writerow([vid, src, idx, word.lower(), f'{start:.3f}', f'{end:.3f}', conf, seg, str(p)])
            except Exception:
                continue
            tw.writerow([vid, 'words_ytt' if src=='yt' else 'words_whisper', 'en', str(p), file_bytes(p), word_count, len(seg_counts) or ''])

    print(f'Wrote {out_transcripts} and {out_words}')

if __name__ == '__main__':
    main()
