#!/usr/bin/env python3
import sys, re, csv, pathlib, os

PROJECT_ROOT = pathlib.Path(os.environ.get("PROJECT_ROOT", "."))

# Usage: python3 find_word_hits.py WORD [PAD_S=0.20]
if len(sys.argv) < 2:
    print("Usage: find_word_hits.py WORD [PAD_S=0.20]", file=sys.stderr)
    sys.exit(1)

needle = sys.argv[1]
pad = float(sys.argv[2]) if len(sys.argv) > 2 else 0.20
rx = re.compile(rf"^\s*{re.escape(needle)}\s*$", re.IGNORECASE)

print("url\tstart\tend\tlabel\tsource_caption")  # TSV header for your pipeline
for words_path in PROJECT_ROOT.rglob("*.words.tsv"):
    base = words_path.with_suffix("")                   # strip .tsv
    media = None
    for ext in (".mp4",".mkv",".webm",".mp3",".wav",".m4a",".opus",".mov",".avi"):
        cand = base.with_suffix(ext)
        if cand.exists():
            media = cand
            break
    if not media:
        continue
    # Try provenance url
    url = None
    src = base.with_suffix(".src.json")
    if src.exists():
        try:
            import json
            d = json.loads(src.read_text(encoding="utf-8", errors="ignore"))
            url = d.get("url")
        except Exception:
            pass

    with open(words_path, encoding="utf-8") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for row in rd:
            w = (row.get("word") or "").strip()
            if rx.match(w):
                s = max(0.0, float(row["start"]) - pad)
                e = float(row["end"]) + pad
                label = w
                srcname = media.name
                print(f"{url or ''}\t{s:.3f}\t{e:.3f}\t{label}\t{srcname}")
