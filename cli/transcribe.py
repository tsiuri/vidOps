# vidops/cli/transcribe.py

import click
import logging
from services import get_transcription_service
from dal import JobRepository
from db import get_connection
from models import Job

logger = logging.getLogger(__name__)

@click.group()
def transcribe():
    """Enqueue and manage transcription jobs."""
    pass


def _looks_like_video_id(ytid: str | None) -> bool:
    """Heuristic to gate transcribe enqueue to real video IDs (avoids channel/playlist IDs)."""
    if not ytid:
        return False
    if len(ytid) != 11:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in ytid)

@transcribe.command("enqueue")
@click.argument("ytid")
@click.option("--model", default="small", help="Whisper model to use (e.g., 'medium', 'large-v2').")
@click.option("--lang", default="en", help="Language of the video (e.g., 'en').")
@click.option("--priority", type=int, default=50, help="Job priority.")
@click.option("--force", is_flag=True, help="Force enqueue even if transcript exists.")
@click.option("--force-job", is_flag=True, help="Allow duplicate pending/running jobs for the same video/model.")
def enqueue_transcription(ytid: str, model: str, lang: str, priority: int, force: bool, force_job: bool):
    """Enqueue a single video for transcription."""
    click.echo(f"Enqueuing transcription for YTID: {ytid} (Model: {model}, Lang: {lang}, Priority: {priority}, Force: {force})...")
    
    try:
        service = get_transcription_service()
        job = service.enqueue_video(ytid=ytid, model=model, language=lang, priority=priority, force=force, force_job=force_job)
        click.echo(click.style(f"✓ Transcription job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue transcription job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing transcription job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue transcription job: {e}", fg="red"), err=True)

@transcribe.command("enqueue-pending")
@click.option("--model", default="small", help="Whisper model to check for.")
@click.option("--limit", type=int, default=100, help="Maximum number of videos to enqueue.")
@click.option("--lang", default="en", help="Default language for new jobs.")
@click.option("--priority", type=int, default=50, help="Job priority.")
def enqueue_pending(model: str, limit: int, lang: str, priority: int):
    """Enqueue videos that do not yet have a transcript for the specified model."""
    click.echo(f"Enqueuing up to {limit} pending videos for transcription (Model: {model}, Lang: {lang}, Priority: {priority})...")

    try:
        service = get_transcription_service()
        jobs = service.enqueue_pending_videos(model=model, limit=limit, language=lang, priority=priority)
        if jobs:
            click.echo(click.style(f"✓ Enqueued {len(jobs)} transcription jobs.", fg="green"))
            for job in jobs:
                click.echo(f"  - {job.job_id} (YTID: {job.ytid})")
        else:
            click.echo("No pending videos found to enqueue.")
    except Exception as e:
        logger.error(f"Error enqueuing pending transcription jobs: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue pending transcription jobs: {e}", fg="red"), err=True)


@transcribe.command("from-download")
@click.option("--job-id", help="Download job_id to source media from.")
@click.option("--url", help="Download URL/playlist/channel used when enqueuing the download job (media_path match).")
@click.option("--model", default="small", show_default=True, help="Transcription model.")
@click.option("--lang", default="en", show_default=True, help="Language.")
@click.option("--priority", type=int, default=50, show_default=True, help="Job priority.")
@click.option("--force", is_flag=True, help="Force enqueue even if transcript exists.")
@click.option("--force-job", is_flag=True, help="Allow duplicate pending/running jobs for the same video/model.")
def transcribe_from_download(job_id: str | None, url: str | None, model: str, lang: str, priority: int, force: bool, force_job: bool):
    """Enqueue transcription for all media assets registered by a download job (by job_id or URL)."""
    if not job_id and not url:
        raise click.UsageError("Provide --job-id or --url")

    jr = JobRepository()
    job = None
    if job_id:
        job = jr.get(job_id)
        if not job:
            raise click.ClickException(f"No job found for id {job_id}")
    else:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM jobs
                    WHERE job_type='download' AND status='completed' AND media_path = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (url,),
                )
                row = cur.fetchone()
                if row:
                    job = Job.from_row(row)  # type: ignore[arg-type]
        if not job:
            raise click.ClickException(f"No completed download job found for URL {url}")

    registered = []
    if job and job.result:
        reg = job.result.get("registered_media")
        if isinstance(reg, list):
            for entry in reg:
                y = entry.get("ytid")
                rel = entry.get("rel_path")
                if y and _looks_like_video_id(y):
                    registered.append((y, rel))

    if not registered:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ytid, rel_path
                    FROM assets
                    WHERE kind='media'
                      AND created_at >= %s
                      AND created_at <= %s
                """,
                    (
                        job.created_at,
                        job.updated_at,
                    ),
                )
                rows = cur.fetchall()
                registered = [
                    (row[0], row[1])
                    for row in rows
                    if row and _looks_like_video_id(row[0])
                ]

    if not registered:
        click.echo(click.style("No media assets found for the given download job.", fg="yellow"))
        return

    service = get_transcription_service()
    enqueued = 0
    skipped = []
    for ytid, rel in registered:
        try:
            service.enqueue_video(ytid=ytid, model=model, language=lang, priority=priority, force=force, force_job=force_job)
            enqueued += 1
        except Exception as exc:
            # Common case: already exists or in-flight (show concise skip); otherwise warn
            msg = str(exc)
            known = ("already exists", "already pending", "already running", "already pending/running")
            if any(k in msg for k in known):
                skipped.append(ytid)
            else:
                logger.error("Failed to enqueue transcription for %s: %s", ytid, exc, exc_info=True)
                skipped.append(f"{ytid} (error)")
    click.echo(click.style(f"✓ Enqueued {enqueued} transcription jobs from download {job.job_id}", fg="green"))
    if skipped:
        click.echo(f"Skipped {len(skipped)} (likely already transcribed): " + ", ".join(skipped))
