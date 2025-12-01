import click
import logging
from typing import Optional

from vidops.services import get_subtitle_service

logger = logging.getLogger(__name__)


@click.group()
def convert_captions():
    """Convert downloaded captions into words.yt.tsv via legacy convert-captions."""
    pass


@convert_captions.command("enqueue")
@click.argument("ytid")
@click.option(
    "--subtitle-path",
    type=str,
    help="Subtitle asset path (relative to storage) to convert. Falls back to latest subtitle asset.",
)
@click.option("--overwrite", is_flag=True, help="Forward --overwrite to the legacy converter.")
@click.option("--priority", type=int, default=0, show_default=True, help="Job priority.")
def enqueue_convert(ytid: str, subtitle_path: Optional[str], overwrite: bool, priority: int):
    """Enqueue a convert-captions job for a single video."""
    click.echo(f"Enqueuing convert-captions for {ytid} (overwrite={overwrite})...")
    service = get_subtitle_service()
    try:
        job = service.enqueue_convert_captions(
            ytid=ytid,
            subtitle_asset_path=subtitle_path,
            overwrite=overwrite,
            priority=priority,
        )
    except Exception as exc:
        logger.error("Failed to enqueue convert-captions for %s: %s", ytid, exc, exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue: {exc}", fg="red"), err=True)
        return
    click.echo(click.style(f"✓ convert-captions job enqueued: {job.job_id}", fg="green"))
