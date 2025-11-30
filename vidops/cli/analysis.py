# vidops/cli/analysis.py

import click
from vidops.services import get_analysis_service
import logging

logger = logging.getLogger(__name__)

@click.group()
def analyze():
    """Enqueue and manage transcript analysis jobs."""
    pass

@analyze.command("enqueue")
@click.argument("ytid")
@click.option("--transcript-kind", required=True, help="The kind of transcript to analyze (e.g., 'words_whisper_medium').")
@click.option("--model", default="llama3", help="The AI model to use for analysis (e.g., 'llama3').")
@click.option("--priority", type=int, default=0, help="Job priority.")
def enqueue_analysis(ytid: str, transcript_kind: str, model: str, priority: int):
    """Enqueue a single transcript analysis job."""
    click.echo(f"Enqueuing analysis job for YTID: {ytid} (Transcript Kind: {transcript_kind}, Model: {model}, Priority: {priority})...")
    
    try:
        service = get_analysis_service()
        job = service.enqueue_analysis_job(ytid=ytid, transcript_kind=transcript_kind, analysis_model=model, priority=priority)
        click.echo(click.style(f"✓ Analysis job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue analysis job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing analysis job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue analysis job: {e}", fg="red"), err=True)

