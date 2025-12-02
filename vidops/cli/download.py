# vidops/cli/download.py

import click
from vidops.services import get_download_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def download():
    """Manage and enqueue video downloads."""
    pass

@download.command("enqueue")
@click.argument("url")
@click.option("--priority", type=int, default=0, help="Job priority.")
def enqueue_download(url: str, priority: int):
    """Enqueue a video URL for download."""
    click.echo(f"Enqueuing download for URL: {url} (Priority: {priority})...")

    try:
        service = get_download_service()
        job = service.enqueue_download(url, priority)
        click.echo(click.style(f"✓ Download job enqueued: {job.job_id}", fg="green"))
    except Exception as e:
        logger.error(f"Error enqueuing download job for {url}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue download job: {e}", fg="red"), err=True)
