#!/usr/bin/env python3
import re, sys
from pathlib import Path
from datetime import date

OUT = Path('logs/title_date_map.tsv')
SOURCES = [Path('logs/hits_test_targets.txt'), Path('logs/vtt_files_used.txt')]

months_full = ['January','February','March','April','May','June','July','August','September','October','November','December']
months_abbr = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Sept','Oct','Nov','Dec']
months = months_full + months_abbr
pat = re.compile(r'(?P<month>' + '|'.join(months) + r')\s*(?P<day>\d{1,2})\s*,?\s*(?P<year>\d{4})', re.IGNORECASE)
mfull_to_num = {m.lower(): i for i,m in enumerate(months_full, start=1)}
abbr_to_num = {m.lower(): i for i,m in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], start=1)}
abbr_to_num['sept'] = 9

def normalize_date(s: str):
    m = pat.fullmatch(s.strip())
    if not m: return None
    mon = m.group('month').lower()
    day = int(m.group('day'))
    year = int(m.group('year'))
    mon_num = mfull_to_num.get(mon) or abbr_to_num.get(mon)
    if not mon_num: return None
    try:
        return date(year, mon_num, day).isoformat()
    except ValueError:
        return None

def main():
    id_to_day = {}
    for src in SOURCES:
        if not src.exists():
            continue
        for line in src.read_text(encoding='utf-8', errors='ignore').splitlines():
            if not line or line.lstrip().startswith('#'):
                continue
            m = re.search(r'([A-Za-z0-9_-]{11})__', line)
            if not m:
                continue
            vid = m.group(1)
            part = line.split('__',1)[1]
            m2 = pat.search(part)
            if not m2:
                continue
            ds = normalize_date(m2.group(0))
            if not ds:
                continue
            id_to_day[vid] = ds
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8') as o:
        o.write('ytid\ttitle_date\n')
        for vid, ds in sorted(id_to_day.items()):
            o.write(f'{vid}\t{ds}\n')
    print(f'Wrote {OUT} ({len(id_to_day)} mappings)')

if __name__ == '__main__':
    main()

