# vidops/cli/dates.py

import click
import logging

from vidops.services import get_dates_service

logger = logging.getLogger(__name__)


@click.group()
def dates():
    """Enqueue and manage legacy date utilities."""
    pass


@dates.command("enqueue")
@click.option(
    "--action",
    required=True,
    type=click.Choice(["find-missing", "create-list", "move", "compare"], case_sensitive=False),
    help="Legacy dates action to run.",
)
@click.option("--dates-file", type=click.Path(exists=True), help="Path to the dates list to materialize.")
@click.option("--source-dir", type=str, help="Source directory for media (relative to workspace or storage).")
@click.option("--archive-cache", type=str, help="Archive cache path used by create-list.")
@click.option("--dest-dir", type=str, help="Destination directory for move action.")
@click.option("--output-name", type=str, help="Relative path for the output manifest (optional).")
@click.option("--priority", type=int, default=0, show_default=True, help="Job priority.")
@click.option("--arg", "extra_args", multiple=True, help="Additional args passed through to workspace.sh.")
def enqueue_dates(action, dates_file, source_dir, archive_cache, dest_dir, output_name, priority, extra_args):
    """Enqueue a legacy dates helper job."""
    action = action.lower()
    if action in {"find-missing", "create-list"} and not dates_file:
        click.echo(click.style("✗ --dates-file is required for this action", fg="red"), err=True)
        return
    service = get_dates_service()
    try:
        job = service.enqueue_job(
            action=action,
            dates_file=dates_file,
            source_dir=source_dir,
            archive_cache=archive_cache,
            dest_dir=dest_dir,
            output_name=output_name,
            extra_args=list(extra_args),
            priority=priority,
        )
        click.echo(click.style(f"✓ Dates job enqueued: {job.job_id} ({action})", fg="green"))
    except Exception as exc:
        logger.error("Unable to enqueue dates job: %s", exc, exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue dates job: {exc}", fg="red"), err=True)
