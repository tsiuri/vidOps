#!/usr/bin/env python3
"""
Reset jobs that are stuck in 'running' state.

This happens when:
- A worker crashes or is killed
- Network issues cause a worker to lose connection
- Long-running jobs exceed expected duration

Usage:
    python scripts/utilities/reset_stuck_jobs.py [--max-age MINUTES] [--dry-run]
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vidops.dal.jobs import JobRepository
from vidops.models import JobStatus


def reset_stuck_jobs(max_age_minutes: int = 30, dry_run: bool = False):
    """
    Reset jobs that have been in 'running' state for too long.

    Args:
        max_age_minutes: Jobs older than this many minutes will be reset
        dry_run: If True, only print what would be done without making changes
    """
    job_repo = JobRepository()

    # Calculate cutoff time
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)

    # Find stuck jobs using raw SQL since we need time-based filtering
    query = """
        SELECT job_id, ytid, job_type, updated_at
        FROM jobs
        WHERE status = 'running'
          AND updated_at < %s
        ORDER BY updated_at ASC
    """

    with job_repo.db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (cutoff,))
            stuck_jobs = cur.fetchall()

    if not stuck_jobs:
        print(f"✓ No stuck jobs found (older than {max_age_minutes} minutes)")
        return 0

    print(f"Found {len(stuck_jobs)} stuck job(s) older than {max_age_minutes} minutes:\n")

    for job_id, ytid, job_type, updated_at in stuck_jobs:
        age = datetime.utcnow() - updated_at
        age_str = f"{int(age.total_seconds() / 60)} minutes ago"
        print(f"  - {job_id[:8]}... ({ytid}, {job_type}) - stuck since {age_str}")

    if dry_run:
        print("\n[DRY RUN] Would reset these jobs to 'pending' status")
        return len(stuck_jobs)

    # Reset the jobs
    print(f"\nResetting {len(stuck_jobs)} job(s) to 'pending' status...")

    reset_query = """
        UPDATE jobs
        SET status = 'pending',
            error_message = 'Reset from stuck running state (auto-cleanup)',
            updated_at = NOW()
        WHERE status = 'running'
          AND updated_at < %s
        RETURNING job_id
    """

    with job_repo.db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(reset_query, (cutoff,))
            reset_count = cur.rowcount
            conn.commit()

    print(f"✓ Reset {reset_count} job(s) to pending")
    return reset_count


def main():
    parser = argparse.ArgumentParser(
        description="Reset jobs stuck in 'running' state",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Reset jobs stuck for more than 30 minutes (default)
  python scripts/utilities/reset_stuck_jobs.py

  # Reset jobs stuck for more than 2 hours
  python scripts/utilities/reset_stuck_jobs.py --max-age 120

  # Dry run to see what would be reset
  python scripts/utilities/reset_stuck_jobs.py --dry-run
        """
    )

    parser.add_argument(
        "--max-age",
        type=int,
        default=30,
        metavar="MINUTES",
        help="Reset jobs older than this many minutes (default: 30)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )

    args = parser.parse_args()

    try:
        count = reset_stuck_jobs(max_age_minutes=args.max_age, dry_run=args.dry_run)
        sys.exit(0 if count >= 0 else 1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
