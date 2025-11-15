#!/usr/bin/env python3
import re
from pathlib import Path
import csv

LOGS = Path('logs')
HITS_LIST = LOGS / 'hits_test_targets.txt'
VTT_LIST = LOGS / 'vtt_files_used.txt'
OUT = LOGS / 'transcripts.tsv'

ytid_re = re.compile(r'([A-Za-z0-9_-]{11})__')

def ytid_from_string(s: str):
    m = ytid_re.search(s)
    return m.group(1) if m else ''

def parse_lang_from_vtt_name(name: str) -> str:
    parts = name.split('.transcript.')
    if len(parts) > 1:
        return parts[1].replace('.vtt','')
    return 'en'

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

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t', lineterminator='\n')
        w.writerow(['ytid','kind','lang','path','bytes','word_count','segment_count'])

        if VTT_LIST.exists():
            for line in VTT_LIST.read_text(encoding='utf-8', errors='ignore').splitlines():
                line=line.strip()
                if not line:
                    continue
                vid = ytid_from_string(line)
                if not vid:
                    continue
                p = norm_path(line)
                w.writerow([vid, 'vtt', parse_lang_from_vtt_name(p.name), str(p), file_bytes(p), '', ''])

        if HITS_LIST.exists():
            for line in HITS_LIST.read_text(encoding='utf-8', errors='ignore').splitlines():
                line=line.strip()
                if not line or line.startswith('#'):
                    continue
                vid = ytid_from_string(line)
                if not vid:
                    continue
                p = norm_path(line)
                kind = 'words_ytt' if p.name.endswith('.words.yt.tsv') else 'words_whisper'
                w.writerow([vid, kind, 'en', str(p), file_bytes(p), '', ''])

    print(f'Wrote {OUT}')

if __name__ == '__main__':
    main()

