# vidops/cli/voice.py

import click
import logging
from typing import List

from vidops.services import get_voice_service

logger = logging.getLogger(__name__)


def _split_paths(values: List[str]) -> List[str]:
    resolved: List[str] = []
    for value in values:
        if not value:
            continue
        resolved.extend([segment.strip() for segment in value.split(",") if segment.strip()])
    return resolved


@click.group()
def voice():
    """Voice filtering commands."""
    pass


@voice.command("enqueue")
@click.argument("ytid")
@click.option("--clips-path", required=True, help="Directory containing clips to evaluate.")
@click.option(
    "--reference",
    multiple=True,
    required=True,
    help="Reference audio clip(s) for the target speaker. Accepts multiple values or comma-separated paths.",
)
@click.option("--threshold", type=float, default=0.72, show_default=True, help="Similarity threshold for a match.")
@click.option(
    "--method",
    type=click.Choice(["simple", "parallel", "chunked"]),
    default="chunked",
    show_default=True,
    help="Legacy selector for parity with workspace.sh (currently informational only).",
)
@click.option(
    "--output-dir",
    help="Relative directory under central storage to write results. Defaults to results/voice_filter/<ytid>.",
)
@click.option("--priority", type=int, default=0, show_default=True, help="Job priority.")
def enqueue_voice(ytid, clips_path, reference, threshold, method, output_dir, priority):
    """Enqueue a voice filtering job for the given YTID."""
    service = get_voice_service()
    references = _split_paths(list(reference))

    click.echo(
        f"Enqueuing voice filter job for {ytid} (clips={clips_path}, references={len(references)}, "
        f"threshold={threshold})"
    )
    try:
        job = service.enqueue_job(
            ytid=ytid,
            clips_path=clips_path,
            reference_paths=references,
            threshold=threshold,
            method=method,
            priority=priority,
            output_dir=output_dir,
        )
    except Exception as exc:
        logger.error("Failed to enqueue voice job for %s: %s", ytid, exc, exc_info=True)
        raise click.ClickException(str(exc))

    click.echo(click.style(f"✓ Voice filter job created: {job.job_id}", fg="green"))
