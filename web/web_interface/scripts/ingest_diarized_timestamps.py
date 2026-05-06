#!/usr/bin/env python3
"""
Ingest diarization spans (VTT-like or TSV files) into the diarized_timestamps table.

You can supply the speaker name via --speaker-name (non-interactive), or the
script will prompt. If a TSV file contains a column "speaker_name" it will be
used per-row and overrides the flag.

TSV format (header required): columns matching DB names are used when present:
  ytid, speaker_name, start_sec, end_sec, source_path
Missing ytid defaults to value inferred from filename. Missing source_path
defaults to file path. start_sec/end_sec must be numeric seconds.

VTT format: standard WebVTT cues; only the timestamp lines are parsed.

Example:
  scripts/ingest_diarized_timestamps.py \
    --path "/home/billie/scripts/hasan-transcription" \
    --db-name transcripts \
    --speaker-name "Hasan"
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterator, Tuple, List, Dict

import psycopg2
from psycopg2.extras import execute_values


TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[\.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[\.,](\d{3})")


def parse_vtt_spans(path: Path) -> Iterator[Tuple[float, float]]:
    """Yield (start_sec, end_sec) for each cue in a VTT file."""
    with path.open('r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            m = TIME_RE.search(line)
            if not m:
                continue
            h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
            start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
            end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
            if end >= start:
                yield (round(start, 3), round(end, 3))


def infer_ytid(file_path: Path) -> str:
    stem = file_path.stem
    # common pattern: <ytid>__anything...
    if '__' in stem:
        return stem.split('__', 1)[0]
    # fallback: first 11-char token
    m = re.match(r"([A-Za-z0-9_-]{8,})", stem)
    return m.group(1) if m else stem


def main():
    ap = argparse.ArgumentParser(description='Ingest diarized VTT spans into diarized_timestamps')
    ap.add_argument('--path', required=True, help='Directory or file path (glob ok) to ingest')
    ap.add_argument('--db-name', default=os.environ.get('DB_NAME', 'transcripts'))
    ap.add_argument('--db-host', default=os.environ.get('DB_HOST', 'localhost'))
    ap.add_argument('--db-port', type=int, default=int(os.environ.get('DB_PORT', 5432)))
    ap.add_argument('--db-user', default=os.environ.get('DB_USER'))
    ap.add_argument('--db-password', default=os.environ.get('DB_PASSWORD'))
    ap.add_argument('--speaker-name', help='Default speaker name (overridden by TSV speaker_name column)')
    args = ap.parse_args()

    speaker_flag = (args.speaker_name or '').strip()

    # Collect files
    p = Path(args.path)
    files: List[Path] = []
    if p.is_dir():
        files = sorted(list(p.glob('**/*.vtt')) + list(p.glob('**/*.tsv')))
    else:
        # allow glob pattern
        if any(ch in args.path for ch in '*?[]'):
            files = [Path(x) for x in sorted([str(q) for q in Path().glob(args.path)])]
        else:
            files = [p]
    files = [f for f in files if f.exists() and f.is_file()]
    if not files:
        print('No VTT files found to ingest.', file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(dbname=args.db_name, host=args.db_host, port=args.db_port,
                            user=args.db_user, password=args.db_password)
    cur = conn.cursor()

    total_rows = 0
    import csv
    for fp in files:
        ytid_default = infer_ytid(fp)
        inserted = 0
        if fp.suffix.lower() == '.vtt':
            speaker = speaker_flag
            if not speaker:
                try:
                    speaker = input(f'[{fp.name}] Enter speaker name to tag these spans: ').strip()
                except KeyboardInterrupt:
                    print('\nAborted.')
                    sys.exit(1)
            if not speaker:
                print(f'Skipping {fp.name}: no speaker name provided.', file=sys.stderr)
                continue
            spans = list(parse_vtt_spans(fp))
            if spans:
                rows = [(ytid_default, speaker, s, e, str(fp)) for (s, e) in spans]
                execute_values(
                    cur,
                    """
                    INSERT INTO diarized_timestamps
                      (ytid, speaker_name, start_sec, end_sec, source_path)
                    VALUES %s
                    ON CONFLICT (ytid, speaker_name, start_sec, end_sec) DO NOTHING
                    """,
                    rows
                )
                inserted += len(rows)
        else:
            # TSV ingestion with header
            with fp.open('r', encoding='utf-8', errors='ignore', newline='') as f:
                reader = csv.DictReader(f, delimiter='\t')
                batch: List[Tuple[str,str,float,float,str]] = []
                for row in reader:
                    try:
                        start = float(row.get('start_sec', '').strip())
                        end = float(row.get('end_sec', '').strip())
                    except Exception:
                        continue
                    if end < start:
                        continue
                    ytid = (row.get('ytid') or '').strip() or ytid_default
                    sp = (row.get('speaker_name') or '').strip() or speaker_flag
                    if not sp:
                        # skip rows without a speaker
                        continue
                    src = (row.get('source_path') or '').strip() or str(fp)
                    batch.append((ytid, sp, round(start,3), round(end,3), src))
                if batch:
                    execute_values(
                        cur,
                        """
                        INSERT INTO diarized_timestamps
                          (ytid, speaker_name, start_sec, end_sec, source_path)
                        VALUES %s
                        ON CONFLICT (ytid, speaker_name, start_sec, end_sec) DO NOTHING
                        """,
                        batch
                    )
                    inserted += len(batch)

        total_rows += inserted
        if inserted:
            print(f"{fp.name}: inserted {inserted} rows (ytid default {ytid_default})")

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. Inserted ~{total_rows} diarized spans across {len(files)} file(s).")


if __name__ == '__main__':
    main()
