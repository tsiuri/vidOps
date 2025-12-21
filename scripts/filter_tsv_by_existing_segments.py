#!/usr/bin/env python3
import argparse
import csv
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse, parse_qs


FILENAME_RE = re.compile(r"^(?P<id>[A-Za-z0-9_-]{11})_.*_(?P<start>\d+\.\d{2,3})-(?P<end>\d+\.\d{2,3})\.mp4$")


def extract_youtube_id(url: str) -> str:
    url = url.strip()
    # Handle full watch URLs and youtu.be short URLs; fallback to last path segment if needed
    try:
        p = urlparse(url)
        if p.netloc.endswith("youtube.com"):
            q = parse_qs(p.query)
            vid = q.get("v", [""])[0]
            if vid:
                return vid
        if p.netloc.endswith("youtu.be"):
            # path like /VIDEOID
            vid = p.path.split("/")[-1]
            if vid:
                return vid
    except Exception:
        pass
    # Fallback: try to find an 11-char ID in the string
    m = re.search(r"([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)


def main():
    ap = argparse.ArgumentParser(description="Filter TSV to remove segments already present in clips directory.")
    ap.add_argument("tsv", help="Input TSV with columns: url start end ...")
    ap.add_argument("clips_dir", help="Directory containing downloaded clip files")
    ap.add_argument("--pad-start", type=Decimal, default=Decimal("0.00"), dest="pad_start")
    ap.add_argument("--pad-end", type=Decimal, default=Decimal("0.00"), dest="pad_end")
    ap.add_argument("--out-tsv", required=True, help="Path for filtered TSV output")
    ap.add_argument("--out-existing", required=True, help="Path for list of existing segments")
    args = ap.parse_args()

    # Build set of existing (id, start, end) from filenames
    existing = set()
    os.makedirs(os.path.dirname(args.out_existing), exist_ok=True)
    with open(args.out_existing, "w", encoding="utf-8", newline="") as exf:
        writer = csv.writer(exf, delimiter='\t')
        writer.writerow(["id", "start", "end", "filename"])
        try:
            entries = os.listdir(args.clips_dir)
        except FileNotFoundError:
            print(f"clips_dir not found: {args.clips_dir}")
            entries = []
        for name in entries:
            m = FILENAME_RE.match(name)
            if not m:
                continue
            vid = m.group("id")
            s = Decimal(m.group("start"))
            e = Decimal(m.group("end"))
            s2 = q2(s)
            e2 = q2(e)
            key = (vid, f"{s2:.2f}", f"{e2:.2f}")
            existing.add(key)
            writer.writerow([vid, f"{s2:.2f}", f"{e2:.2f}", name])

    kept = 0
    removed = 0
    os.makedirs(os.path.dirname(args.out_tsv), exist_ok=True)
    with open(args.tsv, "r", encoding="utf-8", newline="") as inf, \
         open(args.out_tsv, "w", encoding="utf-8", newline="") as outf:
        reader = csv.reader(inf, delimiter='\t')
        writer = csv.writer(outf, delimiter='\t')
        # Copy header through as-is
        try:
            header = next(reader)
        except StopIteration:
            return
        writer.writerow(header)

        for row in reader:
            if not row or len(row) < 3:
                continue
            url, start_str, end_str = row[0], row[1], row[2]
            vid = extract_youtube_id(url)
            if not vid:
                # If cannot parse, keep it to be safe
                writer.writerow(row)
                kept += 1
                continue

            try:
                s = Decimal(start_str)
                e = Decimal(end_str)
            except Exception:
                writer.writerow(row)
                kept += 1
                continue

            s_adj = q2(max(Decimal("0.00"), s - args.pad_start))
            e_adj = q2(e + args.pad_end)
            key = (vid, f"{s_adj:.2f}", f"{e_adj:.2f}")

            if key in existing:
                removed += 1
                continue
            writer.writerow(row)
            kept += 1

    print(f"Rows removed (already present): {removed}")
    print(f"Rows kept (to download): {kept}")


if __name__ == "__main__":
    main()

