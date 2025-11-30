# vidops/cli/clipping.py

import click
from vidops.services import get_clipping_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def clip():
    """Enqueue and manage video clipping jobs."""
    pass

@clip.command("enqueue")
@click.argument("ytid")
@click.option("--start", type=float, required=True, help="Start time of the clip in seconds.")
@click.option("--end", type=float, required=True, help="End time of the clip in seconds.")
@click.option("--label", default="manual_clip", help="A label for the clip (e.g., search term, event name).")
@click.option("--priority", type=int, default=0, help="Job priority.")
def enqueue_clip(ytid: str, start: float, end: float, label: str, priority: int):
    """Enqueue a single video clipping job."""
    click.echo(f"Enqueuing clipping job for YTID: {ytid} from {start}s to {end}s (Label: {label}, Priority: {priority})...")
    
    if start >= end:
        click.echo(click.style("✗ Error: Start time must be less than end time.", fg="red"), err=True)
        return

    try:
        service = get_clipping_service()
        job = service.enqueue_clip_job(ytid=ytid, start_sec=start, end_sec=end, label=label, priority=priority)
        click.echo(click.style(f"✓ Clipping job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue clipping job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing clipping job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue clipping job: {e}", fg="red"), err=True)

