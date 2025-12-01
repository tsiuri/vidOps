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
@click.option(
    "--reference-dir",
    help="Directory under storage containing reference.json and clips (default: generated/diary_reference/<ytid>).",
)
@click.option(
    "--words-path",
    help="Relative path to words TSV under storage (default: transcript path for transcript-kind).",
)
@click.option("--device", default="auto", show_default=True, help="Device hint for the legacy diarization script.")
@click.option(
    "--chunk-seconds",
    type=float,
    default=6.0,
    show_default=True,
    help="Chunk size passed to the legacy diarization runner.",
)
@click.option(
    "--overlap-seconds",
    type=float,
    default=1.0,
    show_default=True,
    help="Chunk overlap passed to the legacy diarization runner.",
)
@click.option(
    "--similarity-threshold",
    type=float,
    default=0.6,
    show_default=True,
    help="Speaker similarity threshold for the legacy runner.",
)
@click.option(
    "--gap-threshold",
    type=float,
    default=0.15,
    show_default=True,
    help="Gap tolerance when aligning words to speaker spans.",
)
@click.option(
    "--output-dir",
    help="Relative output directory under storage (default: generated/diarization_resemblyzer/<ytid>).",
)
def enqueue_diarization(
    ytid: str,
    transcript_kind: str,
    model: str,
    priority: int,
    reference_dir: str | None,
    words_path: str | None,
    device: str,
    chunk_seconds: float,
    overlap_seconds: float,
    similarity_threshold: float,
    gap_threshold: float,
    output_dir: str | None,
):
    """Enqueue a single diarization job."""
    click.echo(
        f"Enqueuing diarization job for YTID: {ytid} "
        f"(Transcript Kind: {transcript_kind}, Model: {model}, Priority: {priority})..."
    )
    
    try:
        service = get_diarization_service()
        job = service.enqueue_diarization_job(
            ytid=ytid,
            transcript_kind=transcript_kind,
            diarization_model=model,
            priority=priority,
            reference_dir=reference_dir,
            words_path=words_path,
            device=device,
            chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds,
            similarity_threshold=similarity_threshold,
            gap_threshold=gap_threshold,
            output_dir=output_dir,
        )
        click.echo(click.style(f"✓ Diarization job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue diarization job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing diarization job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue diarization job: {e}", fg="red"), err=True)
