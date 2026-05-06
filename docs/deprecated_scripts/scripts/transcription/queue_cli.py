#!/usr/bin/env python3
"""
Queue management CLI for transcription workers.

Usage:
    ./queue_cli.py enqueue <files...> [--model MODEL] [--priority PRI]
    ./queue_cli.py status
    ./queue_cli.py workers
    ./queue_cli.py cancel <job_id>
    ./queue_cli.py retry --failed
    ./queue_cli.py recover-stale
    ./queue_cli.py logs <job_id>
    ./queue_cli.py stats
"""

import sys
import argparse
from pathlib import Path
from typing import List
import json
from datetime import datetime

# Add script dir to path
sys.path.insert(0, str(Path(__file__).parent))

from db_queue import TranscriptionQueue


def cmd_enqueue(args):
    """Enqueue media files for transcription"""
    queue = TranscriptionQueue()

    job_ids = []
    for media_path in args.files:
        media_path = Path(media_path).resolve()
        if not media_path.exists():
            print(f"✗ File not found: {media_path}", file=sys.stderr)
            continue

        job_id = queue.enqueue(
            media_path=str(media_path),
            model=args.model,
            language=args.language,
            output_format=args.format,
            priority=args.priority
        )
        job_ids.append(job_id)
        print(f"✓ Enqueued: {media_path.name} -> {job_id}")

    print(f"\n✓ Enqueued {len(job_ids)} jobs")
    return 0


def cmd_status(args):
    """Show queue status"""
    queue = TranscriptionQueue()
    stats = queue.get_queue_stats()

    print("Queue Status")
    print("=" * 50)
    for status, count in sorted(stats.items()):
        print(f"  {status:15} {count:>5} jobs")
    print("=" * 50)
    print(f"  Total:          {sum(stats.values()):>5} jobs")
    return 0


def cmd_workers(args):
    """Show worker status"""
    queue = TranscriptionQueue()
    with queue._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM transcribe_worker_stats ORDER BY worker_id")
            rows = cur.fetchall()

    if not rows:
        print("No workers registered")
        return 0

    print("Worker Status")
    print("=" * 120)
    print(f"{'Worker ID':<30} {'Type':<8} {'Status':<10} {'Jobs':<10} {'Avg Time':<10} {'Last Seen':<20}")
    print("=" * 120)

    for row in rows:
        worker_id, worker_type, status, completed, failed, avg_time, last_hb, *_ = row
        last_seen = f"{int(last_hb)}s ago" if last_hb is not None else "never"
        jobs = f"{completed}/{failed}"
        avg = f"{avg_time:.1f}s" if avg_time else "N/A"

        print(f"{worker_id:<30} {worker_type:<8} {status:<10} {jobs:<10} {avg:<10} {last_seen:<20}")

    return 0


def cmd_cancel(args):
    """Cancel a job"""
    queue = TranscriptionQueue()
    queue.update_status(args.job_id, 'cancelled')
    print(f"✓ Cancelled job: {args.job_id}")
    return 0


def cmd_retry_failed(args):
    """Retry all failed jobs"""
    queue = TranscriptionQueue()
    with queue._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE transcribe_jobs
                SET status = 'pending',
                    worker_id = NULL,
                    lease_expires_at = NULL
                WHERE status = 'failed'
                  AND attempts < max_attempts
                RETURNING job_id
            """)
            job_ids = [row[0] for row in cur.fetchall()]

    print(f"✓ Re-queued {len(job_ids)} failed jobs")
    return 0


def cmd_recover_stale(args):
    """Recover jobs with expired leases"""
    queue = TranscriptionQueue()
    rows = queue.recover_stale_jobs()

    if rows:
        print(f"✓ Recovered {len(rows)} stale jobs:")
        for row in rows:
            job_id = row['job_id']
            worker_id = row['worker_id']
            duration = row['stale_duration']
            print(f"  - {job_id} (was held by {worker_id})")
    else:
        print("No stale jobs found")

    return 0


def cmd_logs(args):
    """Show job log"""
    queue = TranscriptionQueue()
    with queue._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT timestamp, event_type, worker_id, message
                FROM transcribe_job_log
                WHERE job_id = %s
                ORDER BY timestamp
            """, (args.job_id,))
            rows = cur.fetchall()

    if not rows:
        print(f"No logs found for job: {args.job_id}")
        return 1

    print(f"Job Log: {args.job_id}")
    print("=" * 100)
    for ts, event, worker, msg in rows:
        worker_str = worker or 'system'
        msg_str = msg or ''
        print(f"{ts} [{event:12}] {worker_str:20} {msg_str}")

    return 0


def cmd_stats(args):
    """Show detailed statistics"""
    queue = TranscriptionQueue()
    with queue._conn() as conn:
        with conn.cursor() as cur:
            # Queue stats
            cur.execute("SELECT * FROM transcribe_queue_stats")
            queue_stats = cur.fetchall()

            # Recent jobs
            cur.execute("""
                SELECT status, COUNT(*), AVG(processing_time_sec)
                FROM transcribe_jobs
                WHERE created_at > now() - interval '24 hours'
                GROUP BY status
            """)
            recent_stats = cur.fetchall()

            # Throughput
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'completed') as completed,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed,
                    AVG(processing_time_sec) FILTER (WHERE status = 'completed') as avg_time
                FROM transcribe_jobs
                WHERE created_at > now() - interval '1 hour'
            """)
            hourly = cur.fetchone()

    print("Statistics")
    print("=" * 80)
    print("\nQueue Status:")
    for status, count, avg_wait, oldest in queue_stats:
        wait_str = f"{avg_wait:.0f}s" if avg_wait else "N/A"
        print(f"  {status:15} {count:>5} jobs (avg wait: {wait_str})")

    print("\nLast 24 Hours:")
    for status, count, avg_time in recent_stats:
        avg = f"{avg_time:.1f}s" if avg_time else "N/A"
        print(f"  {status:15} {count:>5} jobs (avg: {avg})")

    if hourly:
        total, completed, failed, avg_time = hourly
        print(f"\nLast Hour:")
        print(f"  Total jobs:     {total}")
        print(f"  Completed:      {completed}")
        print(f"  Failed:         {failed}")
        print(f"  Avg time:       {avg_time:.1f}s" if avg_time else "  Avg time:       N/A")
        if completed > 0:
            print(f"  Throughput:     {completed} jobs/hour")

    return 0


def cmd_list_jobs(args):
    """List recent jobs"""
    queue = TranscriptionQueue()
    with queue._conn() as conn:
        with conn.cursor() as cur:
            sql = """
                SELECT job_id, media_path, status, worker_id, priority, attempts, created_at
                FROM transcribe_jobs
                ORDER BY created_at DESC
                LIMIT %s
            """
            cur.execute(sql, (args.limit,))
            rows = cur.fetchall()

    if not rows:
        print("No jobs found")
        return 0

    print(f"Recent Jobs (last {len(rows)})")
    print("=" * 140)
    print(f"{'Job ID':<45} {'Media':<40} {'Status':<10} {'Worker':<20} {'Pri':<5} {'Att':<5} {'Created':<20}")
    print("=" * 140)

    for job_id, media_path, status, worker_id, priority, attempts, created_at in rows:
        media_name = Path(media_path).name if media_path else "N/A"
        media_name = media_name[:40]
        worker_str = (worker_id[:18] + '..') if worker_id and len(worker_id) > 20 else (worker_id or 'N/A')
        print(f"{job_id:<45} {media_name:<40} {status:<10} {worker_str:<20} {priority:<5} {attempts:<5} {created_at}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Transcription queue management")
    subparsers = parser.add_subparsers(dest='command', required=True)

    # enqueue
    p_enq = subparsers.add_parser('enqueue', help='Enqueue files for transcription')
    p_enq.add_argument('files', nargs='+', help='Media files to transcribe')
    p_enq.add_argument('--model', default='medium', help='Whisper model')
    p_enq.add_argument('--language', default='en', help='Language code')
    p_enq.add_argument('--format', default='vtt', choices=['vtt', 'srt', 'both'])
    p_enq.add_argument('--priority', type=int, default=0, help='Priority (higher=first)')

    # status
    subparsers.add_parser('status', help='Show queue status')

    # workers
    subparsers.add_parser('workers', help='Show worker status')

    # cancel
    p_cancel = subparsers.add_parser('cancel', help='Cancel a job')
    p_cancel.add_argument('job_id', help='Job ID to cancel')

    # retry
    p_retry = subparsers.add_parser('retry', help='Retry jobs')
    p_retry.add_argument('--failed', action='store_true', help='Retry all failed jobs')

    # recover-stale
    subparsers.add_parser('recover-stale', help='Recover jobs with expired leases')

    # logs
    p_logs = subparsers.add_parser('logs', help='Show job log')
    p_logs.add_argument('job_id', help='Job ID')

    # stats
    subparsers.add_parser('stats', help='Show detailed statistics')

    # list (new)
    p_list = subparsers.add_parser('list', help='List recent jobs')
    p_list.add_argument('--limit', type=int, default=20, help='Number of jobs to show')

    args = parser.parse_args()

    # Dispatch to command handler
    cmd_map = {
        'enqueue': cmd_enqueue,
        'status': cmd_status,
        'workers': cmd_workers,
        'cancel': cmd_cancel,
        'retry': cmd_retry_failed,
        'recover-stale': cmd_recover_stale,
        'logs': cmd_logs,
        'stats': cmd_stats,
        'list': cmd_list_jobs,
    }

    return cmd_map[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
