# vidops/cli/transcribe.py

import click
from vidops.services import get_transcription_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def transcribe():
    """Enqueue and manage transcription jobs."""
    pass

@transcribe.command("enqueue")
@click.argument("ytid")
@click.option("--model", default="small", help="Whisper model to use (e.g., 'medium', 'large-v2').")
@click.option("--lang", default="en", help="Language of the video (e.g., 'en').")
@click.option("--priority", type=int, default=0, help="Job priority.")
@click.option("--force", is_flag=True, help="Force enqueue even if transcript exists.")
def enqueue_transcription(ytid: str, model: str, lang: str, priority: int, force: bool):
    """Enqueue a single video for transcription."""
    click.echo(f"Enqueuing transcription for YTID: {ytid} (Model: {model}, Lang: {lang}, Priority: {priority}, Force: {force})...")
    
    try:
        service = get_transcription_service()
        job = service.enqueue_video(ytid=ytid, model=model, language=lang, priority=priority, force=force)
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
@click.option("--priority", type=int, default=0, help="Job priority.")
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

