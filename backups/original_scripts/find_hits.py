#usage- python3 find_hits.py 'kaya|prong collar|basketball' 7 > wanted_clips.tsv
# columns: url  start  end  label  source_caption

#!/usr/bin/env python3
import re, sys, json
from pathlib import Path

USAGE = "Usage: find_hits.py 'regex|regex2|…' [pad_seconds=5]"

if len(sys.argv) < 2:
    print(USAGE); sys.exit(1)
rx = re.compile(sys.argv[1], re.IGNORECASE)
pad = int(sys.argv[2]) if len(sys.argv)>=3 else 5

def parse_caption_times(lines):
    # Handles WEBVTT and SRT-ish
    times=[]
    sh=None
    for ln in lines:
        ln=ln.strip("\n")
        if "-->" in ln and (":" in ln[:8]):
            sh=ln
            continue
        if ln and sh:
            # extract HH:MM:SS.xxx at left
            h,m,s = sh[:12].replace(',', '.').split(':')
            t = int(h)*3600 + int(m)*60 + float(s)
            times.append((t, ln))
            sh=None
    return times

def first_ts_for_line(lines, idx):
    # Walk backward to find last timestamp we saw before this text
    t=0.0
    for j in range(idx, -1, -1):
        if isinstance(lines[j], tuple):
            return lines[j][0]
    return t

def load_src_json(base):
    src = base.with_suffix(".src.json")
    if src.exists():
        try:
            return json.loads(src.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return {}
    return {}

def scan_caption(path: Path):
    txt = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    # Build a mixed list: either (time,text) from caption or plain lines for .tslog
    mixed=[]
    is_tslog = path.suffix == ".txt" and path.name.endswith(".tslog.txt")
    if is_tslog:
        # tslog lines are already prefixed sometimes with [HH:MM:SS]
        for ln in txt:
            m=re.match(r'^\[(\d{2}):(\d{2}):(\d{2})\]\s*(.*)$', ln)
            if m:
                h,mn,s = map(int, m.groups()[:3])
                t = h*3600 + mn*60 + s
                mixed.append((t, m.group(4)))
            else:
                mixed.append(ln)
    else:
        # VTT/SRT
        parsed = parse_caption_times(txt)
        for t,line in parsed:
            mixed.append((t, line))
    return mixed

print("url\tstart\tend\tlabel\tsource_caption")
for cap in sorted(Path(".").rglob("*.*"), key=lambda p:p.as_posix()):
    if not cap.suffix.lower() in (".vtt",".srt",".txt"): continue
    if cap.suffix.lower()==".txt" and not cap.name.endswith(".tslog.txt"): continue
    base = cap.with_suffix("") if not cap.name.endswith(".tslog.txt") else Path(cap.as_posix().replace(".tslog",""))
    src = load_src_json(base)
    url = src.get("url")
    base_offset = float(src.get("base_offset", 0.0) or 0.0)
    if not url:
        continue  # skip if we can't map to a URL

    mixed = scan_caption(cap)
    # Build a simple array of text only for search along with timestamps
    texts = []
    times = []
    for item in mixed:
        if isinstance(item, tuple):
            times.append(item[0])
            texts.append(item[1])
        else:
            # plain line (no timestamp) — rare; skip
            continue

    for t, line in zip(times, texts):
        if not line.strip(): continue
        if rx.search(line):
            # clip window around this line’s start
            start = max(0.0, t - pad) + base_offset
            end   = t + pad + base_offset
            label = rx.pattern
            print(f"{url}\t{start:.3f}\t{end:.3f}\t{label}\t{cap}")
