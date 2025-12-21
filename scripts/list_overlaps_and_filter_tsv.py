#!/usr/bin/env python3
import argparse
import csv
import os
import re
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING
from urllib.parse import urlparse, parse_qs

FILENAME_RE = re.compile(r"^(?P<id>[A-Za-z0-9_-]{11})_.*_(?P<start>\d+\.\d{2})-(?P<end>\d+\.\d{2})\.[A-Za-z0-9]+$")

def extract_youtube_id(url: str) -> str:
    try:
        p = urlparse(url)
        if p.netloc.endswith("youtube.com"):
            q = parse_qs(p.query)
            vid = q.get("v", [""])[0]
            if vid:
                return vid
        if p.netloc.endswith("youtu.be"):
            vid = p.path.split("/")[-1]
            if vid:
                return vid
    except Exception:
        pass
    m = re.search(r"([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""

def q2(x: Decimal, mode) -> str:
    # Return string formatted with 2 decimals after quantize with mode
    return f"{x.quantize(Decimal('0.00'), rounding=mode):.2f}"

def main():
    ap = argparse.ArgumentParser(description="Find overlaps between TSV rows and existing clips, tolerant to rounding.")
    ap.add_argument("tsv", help="Input TSV with columns: url start end ...")
    ap.add_argument("clips_dir", help="Directory containing downloaded clips")
    ap.add_argument("--pad-start", type=Decimal, default=Decimal("0.00"), dest="pad_start")
    ap.add_argument("--pad-end", type=Decimal, default=Decimal("0.00"), dest="pad_end")
    ap.add_argument("--out-present", required=True, help="Path for already-present TSV output")
    ap.add_argument("--out-todo", required=True, help="Path for filtered todo TSV output")
    args = ap.parse_args()

    # Scan existing files → set of (id, start_2d, end_2d)
    existing = set()
    try:
        for name in os.listdir(args.clips_dir):
            m = FILENAME_RE.match(name)
            if not m:
                continue
            vid = m.group("id")
            s = m.group("start")
            e = m.group("end")
            existing.add((vid, s, e))
    except FileNotFoundError:
        pass

    total_rows = 0
    kept = 0
    removed = 0

    os.makedirs(os.path.dirname(args.out_present), exist_ok=True)
    os.makedirs(os.path.dirname(args.out_todo), exist_ok=True)
    with open(args.tsv, "r", encoding="utf-8", newline="") as inf, \
         open(args.out_present, "w", encoding="utf-8", newline="") as present_f, \
         open(args.out_todo, "w", encoding="utf-8", newline="") as todo_f:
        reader = csv.reader(inf, delimiter='\t')
        pres_writer = csv.writer(present_f, delimiter='\t')
        todo_writer = csv.writer(todo_f, delimiter='\t')

        # Copy header
        try:
            header = next(reader)
        except StopIteration:
            print("Empty input TSV")
            return
        pres_writer.writerow(["url","start","end","id","pad_start","pad_end","matched_start","matched_end"]) 
        todo_writer.writerow(header)

        for row in reader:
            if not row or len(row) < 3:
                continue
            total_rows += 1
            url, s_str, e_str = row[0], row[1], row[2]
            vid = extract_youtube_id(url)
            if not vid:
                # If we cannot parse id, keep the row
                todo_writer.writerow(row)
                kept += 1
                continue
            try:
                s = Decimal(s_str)
                e = Decimal(e_str)
            except Exception:
                todo_writer.writerow(row)
                kept += 1
                continue

            s_adj = (s - args.pad_start)
            if s_adj < 0:
                s_adj = Decimal("0.00")
            e_adj = (e + args.pad_end)

            # Generate rounding variants for 2-dec matching
            s_cands = { q2(s_adj, ROUND_HALF_UP), q2(s_adj, ROUND_FLOOR), q2(s_adj, ROUND_CEILING) }
            e_cands = { q2(e_adj, ROUND_HALF_UP), q2(e_adj, ROUND_FLOOR), q2(e_adj, ROUND_CEILING) }

            found = None
            for ss in s_cands:
                for ee in e_cands:
                    if (vid, ss, ee) in existing:
                        found = (ss, ee)
                        break
                if found:
                    break

            if found:
                pres_writer.writerow([url, s_str, e_str, vid, f"{s_adj.quantize(Decimal('0.000'))}", f"{e_adj.quantize(Decimal('0.000'))}", found[0], found[1]])
                removed += 1
            else:
                todo_writer.writerow(row)
                kept += 1

    print(f"Original data rows: {total_rows}")
    print(f"Already present rows: {removed}")
    print(f"Todo rows: {kept}")
    print(f"Check: removed + kept = {removed+kept}")

if __name__ == "__main__":
    main()

