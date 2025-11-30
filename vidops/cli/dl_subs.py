# vidops/cli/dl_subs.py

import click
from vidops.services import get_subtitle_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def dl_subs():
    """Enqueue and manage subtitle download jobs."""
    pass

@dl_subs.command("enqueue")
@click.argument("ytid")
@click.option("--lang", default="en", help="Language of the subtitles to download (e.g., 'en').")
@click.option("--format", default="vtt", help="Format of the subtitles (e.g., 'vtt', 'srt').")
@click.option("--priority", type=int, default=0, help="Job priority.")
def enqueue_subtitle_download(ytid: str, lang: str, format: str, priority: int):
    """Enqueue a single subtitle download job."""
    click.echo(f"Enqueuing subtitle download for YTID: {ytid} (Lang: {lang}, Format: {format}, Priority: {priority})...")
    
    try:
        service = get_subtitle_service()
        job = service.enqueue_subtitle_download_job(ytid=ytid, lang=lang, format=format, priority=priority)
        click.echo(click.style(f"✓ Subtitle download job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue subtitle download job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing subtitle download job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue subtitle download job: {e}", fg="red"), err=True)

