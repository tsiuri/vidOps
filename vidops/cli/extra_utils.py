# vidops/cli/extra_utils.py

import click
import logging

from vidops.services import get_extra_utils_service

logger = logging.getLogger(__name__)


@click.group(name="extra-utils")
def extra_utils():
    """Enqueue and manage legacy extra-utils commands."""
    pass


@extra_utils.command("enqueue")
@click.option("--tool", required=True, help="Name of the workspace.sh extra-utils tool to run.")
@click.option("--input", "inputs", multiple=True, type=click.Path(exists=True), help="Input files to materialize.")
@click.option("--output-name", type=str, help="Relative output path to register (results/extra_utils/...).")
@click.option("--arg", "extra_args", multiple=True, help="Additional args to pass to the legacy tool.")
@click.option("--priority", type=int, default=0, show_default=True, help="Job priority.")
@click.option("--ytid", type=str, help="Optional ytid for asset registration context.")
def enqueue_extra_utils(tool, inputs, output_name, extra_args, priority, ytid):
    """Enqueue a legacy extra-utils helper job."""
    service = get_extra_utils_service()
    try:
        job = service.enqueue_job(
            tool=tool,
            inputs=list(inputs),
            output_name=output_name,
            args=list(extra_args),
            priority=priority,
            ytid=ytid,
        )
        click.echo(click.style(f"✓ Extra-utils job enqueued: {job.job_id} ({tool})", fg="green"))
    except Exception as exc:
        logger.error("Unable to enqueue extra-utils job: %s", exc, exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue extra-utils job: {exc}", fg="red"), err=True)
