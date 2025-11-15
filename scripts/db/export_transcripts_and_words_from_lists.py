#!/usr/bin/env python3
import csv
import re
from pathlib import Path

LOGS = Path('logs')
HITS_LIST = LOGS / 'hits_test_targets.txt'
VTT_LIST = LOGS / 'vtt_files_used.txt'

out_transcripts = LOGS / 'transcripts.tsv'
out_words = LOGS / 'words_export.tsv'

ytid_re = re.compile(r'([A-Za-z0-9_-]{11})__')

def ytid_from_string(s: str):
    m = ytid_re.search(s)
    return m.group(1) if m else ''

def norm_path(s: str):
    p = Path(s.strip())
    if not p.is_absolute():
        p = Path.cwd() / p
    return p

def file_bytes(p: Path):
    try:
        return p.stat().st_size
    except Exception:
        return 0

def parse_lang_from_vtt_name(name: str) -> str:
    parts = name.split('.transcript.')
    if len(parts) > 1:
        return parts[1].replace('.vtt','')
    return 'en'

def export_from_lists():
    out_transcripts.parent.mkdir(parents=True, exist_ok=True)
    with out_transcripts.open('w', encoding='utf-8', newline='') as ft, \
         out_words.open('w', encoding='utf-8', newline='') as fw:
        tw = csv.writer(ft, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
        ww = csv.writer(fw, delimiter='\t', lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
        tw.writerow(['ytid','kind','lang','path','bytes','word_count','segment_count'])
        ww.writerow(['ytid','source','idx','word','start_sec','end_sec','confidence','segment_id','path'])

        # VTT entries from VTT_LIST
        if VTT_LIST.exists():
            for line in VTT_LIST.read_text(encoding='utf-8', errors='ignore').splitlines():
                if not line.strip():
                    continue
                p = norm_path(line)
                vid = ytid_from_string(line)
                if not vid:
                    continue
                tw.writerow([vid, 'vtt', parse_lang_from_vtt_name(p.name), str(p), file_bytes(p), '', ''])

        # Words files from HITS_LIST
        if HITS_LIST.exists():
            import csv as _csv
            for line in HITS_LIST.read_text(encoding='utf-8', errors='ignore').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                p = norm_path(line)
                vid = ytid_from_string(line)
                if not vid:
                    continue
                src = 'yt' if p.name.endswith('.words.yt.tsv') else 'whisper'
                word_count = 0
                segs = set()
                try:
                    with p.open('r', encoding='utf-8', errors='ignore', newline='') as f:
                        r = _csv.reader(f, delimiter='\t')
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
                                start = float((get('start','0') or '0'))
                                end = float((get('end','0') or '0'))
                            except Exception:
                                continue
                            word = (get('word','') or '').strip().lower()
                            seg = get('seg','')
                            conf = get('confidence','')
                            idx += 1
                            word_count += 1
                            if seg:
                                segs.add(seg)
                            ww.writerow([vid, src, idx, word, f'{start:.3f}', f'{end:.3f}', conf, seg, str(p)])
                except Exception:
                    continue
                tw.writerow([vid, 'words_ytt' if src=='yt' else 'words_whisper', 'en', str(p), file_bytes(p), word_count, len(segs) or ''])

def main():
    export_from_lists()
    print(f'Wrote {out_transcripts} and {out_words}')

if __name__ == '__main__':
    main()

