#!/usr/bin/env python3
"""
Live curses-based watcher for the jobs queue.

Usage:
  PYTHONPATH=.<or repo> python scripts/management/watch_jobs.py [--interval 1.0] [--limit 10]

Reads DB config via configuration.py (respects VIDOPS_PROJECT_ROOT/config.yaml).
Controls: q to quit.
"""
import argparse
import curses
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure repo import
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db import get_connection  # type: ignore  # noqa: E402
from configuration import load_config  # type: ignore  # noqa: E402


def fetch_status(limit: int = 10):
    data = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select status, count(*) from jobs group by status order by status")
            data["status_counts"] = cur.fetchall()
            # Ensure pending/running always show (even if zero)
            wanted = {"pending", "running"}
            existing = {row[0] for row in data["status_counts"]}
            for missing in sorted(wanted - existing):
                data["status_counts"].append((missing, 0))
            data["status_counts"] = sorted(data["status_counts"], key=lambda r: r[0])

            cur.execute(
                """
                select job_type, status, count(*)
                from jobs
                group by job_type, status
                order by job_type, status
                """
            )
            data["by_type"] = cur.fetchall()

            cur.execute(
                """
                select job_id, job_type, status, priority, ytid, created_at, claimed_by
                from jobs
                where status in ('pending','claimed','running')
                order by status asc, priority desc, created_at asc
                limit %s
                """,
                (limit,),
            )
            data["active"] = cur.fetchall()

            cur.execute(
                """
                select job_id, job_type, status, priority, ytid, created_at, claimed_by
                from jobs
                where status in ('failed','cancelled')
                order by updated_at desc
                limit %s
                """,
                (limit,),
            )
            data["recent_failed"] = cur.fetchall()

            cur.execute(
                """
                select
                    sum(
                        case
                            when status = 'completed'
                                 and coalesce(completed_at, updated_at) >= now() - interval '2 hours'
                            then 1 else 0
                        end
                    ) as completed_recent,
                    sum(
                        case
                            when status = 'failed'
                                 and updated_at >= now() - interval '2 hours'
                            then 1 else 0
                        end
                    ) as failed_recent
                from jobs
                """
            )
            row = cur.fetchone() or (0, 0)
            data["recent_counts"] = {
                "completed_recent": row[0] or 0,
                "failed_recent": row[1] or 0,
            }
    return data


def truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def render_table(rows, headers, max_width):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell or "")))
    # Trim widths to fit max_width
    total = sum(widths) + 2 * (len(widths) - 1)
    if total > max_width:
        # reduce widest columns first
        over = total - max_width
        for _ in range(over):
            max_idx = max(range(len(widths)), key=lambda i: widths[i])
            if widths[max_idx] > 4:
                widths[max_idx] -= 1
            else:
                break
    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    lines = [fmt.format(*headers)]
    lines.append(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        cells = [truncate(str(c or ""), widths[i]) for i, c in enumerate(row)]
        lines.append(fmt.format(*cells))
    return lines


def draw(screen, args):
    load_config()
    base_timeout_ms = 100
    screen.nodelay(True)
    screen.timeout(base_timeout_ms)
    curses.curs_set(0)
    interval = args.interval
    scroll = 0
    h_offset = 0
    last_fetch = 0
    data = {}
    error = None
    cursor = 0  # global cursor across rendered lines
    info_msg = ""

    while True:
        try:
            now_ts = time.time()
            if now_ts - last_fetch >= interval:
                data = fetch_status(limit=args.limit)
                error = None
                last_fetch = now_ts
        except Exception as exc:  # pragma: no cover - live env
            error = str(exc)

        height, width = screen.getmaxyx()
        screen.erase()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = 0
        header = f"Jobs Queue — {now}  (q quit, arrows move, ←/→ horiz scroll, x cancels selected pending)"
        screen.addstr(line, 0, truncate(header, width))
        line += 2

        if error:
            screen.addstr(line, 0, truncate(f"Error: {error}", width), curses.color_pair(0) | curses.A_BOLD)
            screen.refresh()
            time.sleep(interval)
            continue
        if info_msg:
            screen.addstr(line, 0, truncate(info_msg, width))
            line += 1
            info_msg = ""

        body_lines = []
        meta = []  # track section/job per rendered line

        body_lines.append("Status counts:")
        meta.append({"section": "status_header"})
        status_rows = data.get("status_counts", [])
        status_table = render_table(status_rows, ["status", "count"], width)
        for idx, line_txt in enumerate(status_table):
            body_lines.append(line_txt)
            if idx == 0:
                meta.append({"section": "status_header"})
            elif idx == 1:
                meta.append({"section": "status_sep"})
            else:
                row_idx = idx - 2
                if row_idx < len(status_rows):
                    meta.append({"section": "status", "status_label": status_rows[row_idx][0]})
                else:
                    meta.append({"section": "status"})
        body_lines.append("")
        meta.append({"section": "spacer"})

        recent_counts = data.get("recent_counts") or {}
        completed_recent = recent_counts.get("completed_recent", 0)
        failed_recent = recent_counts.get("failed_recent", 0)
        body_lines.append(
            f"Last 2h: completed={completed_recent}  failed={failed_recent}"
        )
        meta.append({"section": "recent_counts"})
        body_lines.append("")
        meta.append({"section": "spacer"})

        body_lines.append("By type:")
        meta.append({"section": "by_type"})
        body_lines.extend(render_table(data.get("by_type", []), ["job_type", "status", "count"], width))
        meta.extend([{"section": "by_type"}] * (len(body_lines) - len(meta)))
        body_lines.append("")
        meta.append({"section": "spacer"})

        active = data.get("active") or []
        if active:
            body_lines.append("Active (pending/claimed/running):")
            meta.append({"section": "active"})
            table_lines = render_table(
                active,
                ["job_id", "type", "status", "prio", "ytid", "created_at", "claimed_by"],
                width,
            )
            body_lines.extend(table_lines)
            for row in active:
                meta.append(
                    {
                        "section": "active",
                        "job_id": row[0],
                        "status": row[2],
                    }
                )
            body_lines.append("")
            meta.append({"section": "spacer"})

        failed = data.get("recent_failed") or []
        if failed:
            body_lines.append("Recent failed/cancelled:")
            meta.append({"section": "failed"})
            body_lines.extend(
                render_table(
                    failed,
                    ["job_id", "type", "status", "prio", "ytid", "created_at", "claimed_by"],
                    width,
                )
            )
            for row in failed:
                meta.append(
                    {
                        "section": "failed",
                        "job_id": row[0],
                        "status": row[2],
                    }
                )

        max_scroll = max(0, len(body_lines) - (height - line - 1))
        scroll = max(0, min(scroll, max_scroll))
        cursor = max(0, min(cursor, len(body_lines) - 1))
        max_line_len = max((len(bl) for bl in body_lines), default=width)
        max_h_offset = max(0, max_line_len - width)
        h_offset = max(0, min(h_offset, max_h_offset))

        # Render with scroll offset
        for idx, text in enumerate(body_lines):
            row_idx = line + idx
            if row_idx < line + scroll:
                continue
            if row_idx - scroll >= height - 1:
                break
            attr = curses.A_REVERSE if idx == cursor else 0
            slice_text = text[h_offset : h_offset + width]
            if len(slice_text) < width:
                slice_text = slice_text.ljust(width)
            screen.addstr(row_idx - scroll, 0, slice_text, attr)

        screen.refresh()
        try:
            ch = screen.getch()
            if ch == -1:
                continue
            if ch in (ord("q"), ord("Q")):
                break
            elif ch == curses.KEY_UP:
                cursor = max(0, cursor - 1)
                if cursor < scroll:
                    scroll = max(0, scroll - 1)
            elif ch == curses.KEY_DOWN:
                cursor = min(len(body_lines) - 1, cursor + 1)
                if cursor - scroll >= height - line - 1:
                    scroll = min(max_scroll, scroll + 1)
            elif ch == curses.KEY_NPAGE:  # Page down
                scroll = scroll + (height // 2)
                cursor = min(len(body_lines) - 1, cursor + (height // 2))
            elif ch == curses.KEY_PPAGE:  # Page up
                scroll = max(0, scroll - (height // 2))
                cursor = max(0, cursor - (height // 2))
            elif ch == curses.KEY_LEFT:
                h_offset = max(0, h_offset - 4)
            elif ch == curses.KEY_RIGHT:
                h_offset = min(max_h_offset, h_offset + 4)
            elif ch == ord("x"):
                # Cancel pending job under cursor, or bulk-cancel all pending if cursor is on the status counts "pending" row
                if 0 <= cursor < len(meta):
                    row_meta = meta[cursor]
                    if row_meta.get("section") == "active" and row_meta.get("status") == "pending":
                        jid = row_meta.get("job_id")
                        if jid:
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "UPDATE jobs SET status='failed', error_message='Cancelled via watcher' WHERE job_id=%s AND status='pending'",
                                        (jid,),
                                    )
                            info_msg = f"Cancelled pending job {jid}"
                    elif row_meta.get("section") == "status" and row_meta.get("status_label") == "pending":
                        # Prompt user (blocking input)
                        curses.echo()
                        prompt = "Cancel ALL pending jobs? (y/N): "
                        screen.addstr(height - 2, 0, prompt)
                        screen.clrtoeol()
                        screen.nodelay(False)
                        screen.timeout(-1)
                        resp_bytes = screen.getstr(height - 2, len(prompt), 3)
                        screen.nodelay(True)
                        screen.timeout(base_timeout_ms)
                        try:
                            resp = resp_bytes.decode().strip().lower() if resp_bytes else ""
                        except Exception:
                            resp = ""
                        curses.noecho()
                        if resp == "y":
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "UPDATE jobs SET status='failed', error_message='Cancelled via watcher' WHERE status='pending'"
                                    )
                            info_msg = "Cancelled all pending jobs"
                        else:
                            info_msg = "Bulk cancel aborted"
                    else:
                        info_msg = "Selection is not a pending job; no action"
        except curses.error:
            pass


def main():
    parser = argparse.ArgumentParser(description="Curses queue watcher")
    parser.add_argument("--interval", type=float, default=1.0, help="Refresh interval seconds")
    parser.add_argument("--limit", type=int, default=10, help="Max rows per section")
    args = parser.parse_args()
    curses.wrapper(draw, args)


if __name__ == "__main__":
    main()
