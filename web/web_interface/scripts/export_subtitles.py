#!/usr/bin/env python3
import argparse
import os
import sys
from typing import Iterable, List, Tuple

import psycopg2
from config_loader import load_local_config


def fmt_time_srt(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def fmt_time_vtt(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"


def detok(words: List[str]) -> str:
    # Simple, robust detokenization: join with spaces, then fix common space-before-punct
    if not words:
        return ""
    s = " ".join(w.strip() for w in words if w and w.strip())
    # Common punctuation spacing tweaks
    for p in [" .", " ,", " ;", " :", " !", " ?"]:
        s = s.replace(p, p[1:])
    s = s.replace("( ", "(").replace(" )", ")")
    s = s.replace("[ ", "[").replace(" ]", "]")
    s = s.replace("{ ", "{").replace(" }", "}")
    return s.strip()


def group_words(rows: Iterable[Tuple[int, str, float, float]], gap: float):
    group: List[str] = []
    group_start: float | None = None
    group_end: float | None = None
    last_end: float | None = None
    for _idx, word, start, end in rows:
        # Coerce Decimals to float for arithmetic
        s = float(start) if start is not None else 0.0
        e = float(end) if end is not None else s
        if last_end is None or (s - last_end) >= gap:
            if group:
                yield (group_start if group_start is not None else s,
                       group_end if group_end is not None else e,
                       detok(group))
            group = [word]
            group_start = s
            group_end = e
        else:
            group.append(word)
            group_end = e
        last_end = e
    if group:
        yield (group_start if group_start is not None else 0.0,
               group_end if group_end is not None else (group_start or 0.0),
               detok(group))


def fetch_words(conn, ytid: str, speaker_name: str | None,
                restrict_to_diarized: bool,
                start_sec: float | None,
                end_sec: float | None) -> List[Tuple[int, str, float, float]]:
    with conn.cursor() as cur:
        # Build time filters for alias w (words)
        time_filters = []
        time_params: List[float] = []
        if start_sec is not None:
            time_filters.append("w.start_sec >= %s")
            time_params.append(start_sec)
        if end_sec is not None:
            time_filters.append("w.start_sec <= %s")
            time_params.append(end_sec)

        if speaker_name:
            # Strictly within diarized windows for the selected speaker
            where_parts = ["sw.ytid=%s", "sw.speaker_name=%s"]
            if time_filters:
                where_parts.extend(time_filters)
            sql = (
                "SELECT w.idx, w.word, w.start_sec, w.end_sec "
                "FROM speaker_words sw "
                "JOIN words w ON w.ytid = sw.ytid AND w.idx = sw.idx "
                f"WHERE {' AND '.join(where_parts)} "
                "ORDER BY w.start_sec, w.idx"
            )
            params = [ytid, speaker_name, *time_params]
            cur.execute(sql, params)
        elif restrict_to_diarized:
            # Any diarized window (irrespective of speaker)
            where_parts = ["w.ytid=%s"]
            if time_filters:
                where_parts.extend(time_filters)
            sql = (
                "SELECT w.idx, w.word, w.start_sec, w.end_sec "
                "FROM words w "
                "WHERE " + " AND ".join(where_parts) +
                " AND EXISTS (SELECT 1 FROM diarized_timestamps dt "
                "             WHERE dt.ytid = w.ytid "
                "               AND w.start_sec >= dt.start_sec "
                "               AND w.start_sec < dt.end_sec) "
                "ORDER BY w.start_sec, w.idx"
            )
            params = [ytid, *time_params]
            cur.execute(sql, params)
        else:
            # Full transcript within optional time window
            where_parts = ["ytid=%s"]
            sql = (
                "SELECT idx, word, start_sec, end_sec "
                "FROM words "
                f"WHERE {' AND '.join(where_parts + [p.replace('w.', '') for p in time_filters])} "
                "ORDER BY start_sec, idx"
            )
            params = [ytid, *time_params]
            cur.execute(sql, params)
        return cur.fetchall()


def export_srt(groups: Iterable[Tuple[float, float, str]]) -> str:
    lines: List[str] = []
    for i, (start, end, text) in enumerate(groups, start=1):
        lines.append(str(i))
        lines.append(f"{fmt_time_srt(start)} --> {fmt_time_srt(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_vtt(groups: Iterable[Tuple[float, float, str]]) -> str:
    lines: List[str] = ["WEBVTT", ""]
    for i, (start, end, text) in enumerate(groups, start=1):
        lines.append(str(i))
        lines.append(f"{fmt_time_vtt(start)} --> {fmt_time_vtt(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description="Export reconstructed subtitles from PostgreSQL words table.")
    ap.add_argument("--db-name", default=None)
    ap.add_argument("--db-user", default=None)
    ap.add_argument("--db-host", default=None)
    ap.add_argument("--db-port", default=None)
    ap.add_argument("--db-pass", default=None)
    ap.add_argument("--ytid", required=True, help="YouTube ID to export")
    ap.add_argument("--speaker-name", default=None, help="Optional: restrict to diarized speaker name")
    ap.add_argument("--restrict-to-diarized", action="store_true",
                    help="If set, include only words that fall within ANY diarized window (ignores speaker). Default: off")
    ap.add_argument("--start-sec", type=float, default=None, help="Optional: lower bound (seconds) for export window")
    ap.add_argument("--end-sec", type=float, default=None, help="Optional: upper bound (seconds) for export window")
    ap.add_argument("--gap-sec", type=float, default=1.0, help="New line when gap >= this many seconds")
    ap.add_argument("--format", choices=["srt", "vtt"], default="srt")
    ap.add_argument("--output", default=None, help="Output path; default based on ytid + extension")

    args = ap.parse_args()

    cfg = load_local_config()
    def pick(val, cfg_key, env_key, default):
        if val not in (None, ""):
            return val
        if os.getenv(env_key) not in (None, ""):
            return os.getenv(env_key)
        if cfg.get(cfg_key) not in (None, ""):
            return cfg[cfg_key]
        return default

    host = pick(args.db_host, "db_host", "PGHOST", "localhost")
    port = pick(args.db_port, "db_port", "PGPORT", "5432")
    user = pick(args.db_user, "db_user", "PGUSER", os.getenv("USER"))
    dbname = pick(args.db_name, "db_name", "PGDATABASE", args.db_name)
    dbpass = pick(args.db_pass, "db_password", "PGPASSWORD", None)

    dsn = {
        "dbname": dbname,
        "user": user,
        "host": host,
        "port": port,
    }
    if dbpass:
        dsn["password"] = dbpass

    try:
        conn = psycopg2.connect(**dsn)
    except Exception as e:
        print(f"Failed to connect to DB: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        rows = fetch_words(conn, args.ytid, args.speaker_name,
                           args.restrict_to_diarized, args.start_sec, args.end_sec)
        if not rows:
            print("No words found for that ytid (and speaker, if specified).", file=sys.stderr)
            sys.exit(2)
        groups = list(group_words(rows, args.gap_sec))
        if args.format == "srt":
            out = export_srt(groups)
            ext = ".srt"
        else:
            out = export_vtt(groups)
            ext = ".vtt"

        out_path = args.output or f"{args.ytid}{ext}"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Wrote {args.format.upper()} to {out_path} (groups={len(groups)}, gap={args.gap_sec}s)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
