#!/usr/bin/env python3
import json, sys, re, os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "."))

out_path = PROJECT_ROOT / 'logs/db/videos_from_info.tsv'
last_ytids_path = PROJECT_ROOT / 'logs/db/_last_exported_ytids.txt'
pull = PROJECT_ROOT / 'pull'

def ymd_to_date(s: str):
    try:
        return datetime.strptime(s, '%Y%m%d').date().isoformat()
    except Exception:
        return ''

def main():
    files = sorted(pull.glob('*__*.info.json'))
    if not files:
        print('No pull/*__*.info.json found', file=sys.stderr)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ytids = []
    with out_path.open('w', encoding='utf-8') as o:
        o.write('\t'.join(['ytid','url','title','upload_date','duration_sec','channel','channel_id','extractor_key','tags_json','categories_json'])+'\n')
        for f in files:
            try:
                d = json.loads(f.read_text(encoding='utf-8', errors='ignore'))
            except Exception as e:
                print(f'WARN: failed to read {f}: {e}', file=sys.stderr)
                continue
            ytid = d.get('id') or ''
            url = d.get('webpage_url') or ''
            title = d.get('title') or ''
            upload_date = ymd_to_date(d.get('upload_date') or '')
            duration = d.get('duration')
            duration_s = str(int(duration)) if isinstance(duration, (int,float)) else ''
            channel = d.get('channel') or d.get('uploader') or ''
            channel_id = d.get('channel_id') or d.get('uploader_id') or ''
            extractor_key = d.get('extractor_key') or d.get('extractor') or ''
            tags = d.get('tags') or []
            cats = d.get('categories') or []
            # JSON-encode arrays; tabs will be preserved since json dumps uses quotes
            tags_json = json.dumps(tags, ensure_ascii=False)
            cats_json = json.dumps(cats, ensure_ascii=False)
            row = [ytid, url, title, upload_date, duration_s, channel, channel_id, extractor_key, tags_json, cats_json]
            o.write('\t'.join(x.replace('\n',' ').replace('\r',' ') for x in row) + '\n')
            if ytid:
                ytids.append(ytid)
    # Record the list of ytids seen in this export (for annotating upload_type)
    last_ytids_path.parent.mkdir(parents=True, exist_ok=True)
    with last_ytids_path.open('w', encoding='utf-8') as ly:
        for y in sorted(set(ytids)):
            ly.write(y + '\n')
    print(f'Wrote {out_path} and {last_ytids_path}')

if __name__ == '__main__':
    main()
