# vidops/cli/diarization.py

import click
from vidops.services import get_diarization_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def diarize():
    """Enqueue and manage diarization jobs."""
    pass

@diarize.command("enqueue")
@click.argument("ytid")
@click.option("--transcript-kind", required=True, help="The kind of transcript to use for diarization (e.g., 'words_whisper_medium').")
@click.option("--model", default="resemblyzer", help="The diarization model to use (e.g., 'resemblyzer').")
@click.option("--priority", type=int, default=0, help="Job priority.")
def enqueue_diarization(ytid: str, transcript_kind: str, model: str, priority: int):
    """Enqueue a single diarization job."""
    click.echo(f"Enqueuing diarization job for YTID: {ytid} (Transcript Kind: {transcript_kind}, Model: {model}, Priority: {priority})...")
    
    try:
        service = get_diarization_service()
        job = service.enqueue_diarization_job(ytid=ytid, transcript_kind=transcript_kind, diarization_model=model, priority=priority)
        click.echo(click.style(f"✓ Diarization job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue diarization job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing diarization job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue diarization job: {e}", fg="red"), err=True)
