# vidops/cli/download.py

import click
from services import get_download_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def download():
    """Manage and enqueue video downloads."""
    pass

@download.command("enqueue")
@click.argument("url")
@click.option("--priority", type=int, default=50, help="Job priority.")
@click.option("--cookies-browser", default=None, help="Pass cookies from a browser (e.g., firefox, chrome). Overrides config.")
@click.option(
    "--upload-type",
    default="",
    prompt="Upload type/id (leave blank to auto-generate)",
    show_default=False,
    help="Optional upload type/id to tag the video record.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Force re-download even if media is already present in pull/ or download archive.",
)
def enqueue_download(url: str, priority: int, cookies_browser: str | None, upload_type: str | None, force: bool):
    """Enqueue a video URL for download."""
    click.echo(f"Enqueuing download for URL: {url} (Priority: {priority}, Force: {force})...")

    upload_type = (upload_type or "").strip() or None
    try:
        service = get_download_service()
        job = service.enqueue_download(
            url,
            priority,
            cookies_browser=cookies_browser,
            upload_type=upload_type,
            ytdlp_overrides={"force_download": True, "no_overwrites": False} if force else None,
            force_download=force,
        )
        click.echo(click.style(f"✓ Download job enqueued: {job.job_id}", fg="green"))
    except Exception as e:
        logger.error(f"Error enqueuing download job for {url}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue download job: {e}", fg="red"), err=True)
