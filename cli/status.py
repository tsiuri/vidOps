# vidops/cli/status.py

import click
from datetime import timedelta, datetime, UTC
from dal import WorkerRepository, JobRepository
from models import JobStatus, WorkerStatus
from db import check_connection, get_connection

@click.group()
def status():
    """Display status of workers, jobs, and database."""
    pass

@status.command("db")
def status_db():
    """Check database connection status."""
    click.echo("Checking database connection...")
    if check_connection():
        click.echo(click.style("✓ Database connection successful.", fg="green"))
    else:
        click.echo(click.style("✗ Database connection failed.", fg="red"))

@status.command("workers")
@click.option("--stale-minutes", type=int, default=5, help="Threshold in minutes for considering a worker stale.")
@click.option("--all", "show_all", is_flag=True, help="Show all workers, including stale ones.")
def status_workers(stale_minutes, show_all):
    """List active and potentially stale workers."""
    click.echo("Fetching worker status...")
    worker_repo = WorkerRepository()

    if show_all:
        # Show all workers
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM workers ORDER BY last_heartbeat DESC")
                rows = cur.fetchall()
                all_workers = [worker_repo._row_to_worker(row) for row in rows] if hasattr(worker_repo, '_row_to_worker') else []

                # Fallback if private method not available
                if not all_workers:
                    from models import Worker
                    all_workers = [Worker.from_row(row) for row in rows]

        workers_to_show = all_workers
        header = "--- All Workers ---"
    else:
        # Show only active workers
        active_workers = worker_repo.list_active(threshold=timedelta(minutes=stale_minutes))
        workers_to_show = active_workers
        header = f"--- Active Workers (last heartbeat within {stale_minutes} minutes) ---"

    if not workers_to_show:
        click.echo("No workers found.")
        return

    # Calculate stale/errored/active counts
    stale_count = sum(1 for w in workers_to_show if w.status == WorkerStatus.STALE)
    errored_count = sum(1 for w in workers_to_show if w.status == WorkerStatus.ERRORED)
    active_count = sum(1 for w in workers_to_show if w.status in (WorkerStatus.IDLE, WorkerStatus.BUSY))

    click.echo(f"\n{header}")
    click.echo(f"Total: {len(workers_to_show)} workers "
               f"({click.style(str(active_count), fg='green')} active, "
               f"{click.style(str(stale_count), fg='red')} stale, "
               f"{click.style(str(errored_count), fg='red')} errored)")
    click.echo()

    # Show stale/errored workers first
    for worker in sorted(workers_to_show, key=lambda w: (
        w.status not in (WorkerStatus.STALE, WorkerStatus.ERRORED),  # Stale/errored first
        w.last_heartbeat
    ), reverse=True):
        status_color = {
            WorkerStatus.IDLE: "green",
            WorkerStatus.BUSY: "yellow",
            WorkerStatus.ERRORED: "red",
            WorkerStatus.STOPPING: "blue",
            WorkerStatus.REGISTERING: "cyan",
            WorkerStatus.STALE: "red"
        }.get(worker.status, "white")

        # Calculate time since last heartbeat
        last_heartbeat = _to_utc(worker.last_heartbeat)
        if last_heartbeat:
            time_since_heartbeat = datetime.now(UTC) - last_heartbeat
            heartbeat_str = f"{int(time_since_heartbeat.total_seconds() / 60)}m ago"
            heartbeat_display = last_heartbeat.strftime('%Y-%m-%d %H:%M:%S UTC')
        else:
            heartbeat_str = "n/a"
            heartbeat_display = "unknown"

        click.echo(f"  ID: {worker.worker_id}")
        click.echo(f"    Alias: {worker.machine_alias} (Type: {worker.worker_type})")
        click.echo(f"    Status: {click.style(worker.status.value.upper(), fg=status_color)}")
        click.echo(f"    Last Heartbeat: {heartbeat_display} ({heartbeat_str})")

        if worker.current_job_id:
            click.echo(f"    Current Job: {worker.current_job_id}")

        if worker.capabilities:
            click.echo(f"    Capabilities: {', '.join(worker.capabilities)}")

        click.echo("-" * 20)

@status.command("jobs")
@click.option("--job-type", default=None, help="Filter by job type (e.g., 'download', 'transcription').")
@click.option("--detail", is_flag=True, help="Show detailed job breakdown by type.")
def status_jobs(job_type, detail):
    """Display a summary of job statuses from the generic jobs table."""
    if job_type:
        click.echo(f"Fetching job status for job_type='{job_type}'...")
    else:
        click.echo("Fetching job status from generic jobs queue...")

    job_repo = JobRepository()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Overall status counts
                if job_type:
                    cur.execute(
                        "SELECT status, COUNT(*) as count FROM jobs WHERE job_type = %s GROUP BY status",
                        (job_type,)
                    )
                else:
                    cur.execute("SELECT status, COUNT(*) as count FROM jobs GROUP BY status")

                job_counts = {status: 0 for status in JobStatus}
                for row in cur.fetchall():
                    try:
                        job_counts[JobStatus(row['status'])] = row['count']
                    except ValueError:
                        click.echo(f"Warning: Unknown job status '{row['status']}' found in DB.", err=True)

                # Job type breakdown (if detail requested)
                job_type_counts = {}
                if detail and not job_type:
                    cur.execute(
                        """
                        SELECT job_type, status, COUNT(*) as count
                        FROM jobs
                        GROUP BY job_type, status
                        ORDER BY job_type, status
                        """
                    )
                    for row in cur.fetchall():
                        jtype = row['job_type']
                        if jtype not in job_type_counts:
                            job_type_counts[jtype] = {s: 0 for s in JobStatus}
                        try:
                            job_type_counts[jtype][JobStatus(row['status'])] = row['count']
                        except ValueError:
                            pass

    except Exception as e:
        click.echo(click.style(f"✗ Error fetching job counts: {e}", fg="red"), err=True)
        return

    # Display results
    click.echo("\n--- Job Summary ---")

    if job_type:
        click.echo(f"Job Type: {job_type}")

    total_jobs = sum(job_counts.values())
    click.echo(f"Total Jobs: {total_jobs}")
    click.echo()

    for status_enum in JobStatus:
        count = job_counts.get(status_enum, 0)
        status_color = {
            JobStatus.PENDING: "blue",
            JobStatus.CLAIMED: "yellow",
            JobStatus.RUNNING: "cyan",
            JobStatus.COMPLETED: "green",
            JobStatus.FAILED: "red",
            JobStatus.CANCELLED: "magenta"
        }.get(status_enum, "white")
        click.echo(f"  {status_enum.value.capitalize()}: {click.style(str(count), fg=status_color)}")

    # Show breakdown by job type if detail requested
    if detail and job_type_counts:
        click.echo("\n--- Breakdown by Job Type ---")
        for jtype, counts in sorted(job_type_counts.items()):
            total = sum(counts.values())
            click.echo(f"\n{jtype}: {total} total")
            for status_enum, count in counts.items():
                if count > 0:
                    status_color = {
                        JobStatus.PENDING: "blue",
                        JobStatus.CLAIMED: "yellow",
                        JobStatus.RUNNING: "cyan",
                        JobStatus.COMPLETED: "green",
                        JobStatus.FAILED: "red",
                        JobStatus.CANCELLED: "magenta"
                    }.get(status_enum, "white")
                    click.echo(f"  {status_enum.value}: {click.style(str(count), fg=status_color)}")
def _to_utc(dt: datetime | None) -> datetime | None:
    """Return a UTC-aware datetime regardless of the input timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
