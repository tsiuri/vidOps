# vidops/cli/analysis.py

import click
import logging
from pathlib import Path

from configuration import load_config
from dal import TranscriptRepository, JobRepository
from dal.analysis_task_repository import AnalysisDatabase
from db import get_connection
from models import Job, JobStatus
from scripts.analysis.analyze_transcript import VTTParser, TranscriptChunker
from scripts.analysis.analyze_to_db import create_analysis_job, export_vtt_from_db
from scripts.analysis.analysis_config import AnalysisConfig
from services import get_analysis_service

logger = logging.getLogger(__name__)

@click.group()
def analyze():
    """Enqueue and manage transcript analysis jobs."""
    pass

@analyze.command("enqueue")
@click.argument("ytid")
@click.option("--transcript-kind", required=True, help="The kind of transcript to analyze (e.g., 'words_whisper_medium').")
@click.option("--model", default="llama3", help="The AI model to use for analysis (e.g., 'llama3').")
@click.option("--priority", type=int, default=50, help="Job priority.")
@click.option("--output-name", help="Relative path for the analysis artifact (optional).")
def enqueue_analysis(ytid: str, transcript_kind: str, model: str, priority: int, output_name: str | None):
    """Enqueue a single transcript analysis job."""
    click.echo(f"Enqueuing analysis job for YTID: {ytid} (Transcript Kind: {transcript_kind}, Model: {model}, Priority: {priority})...")
    
    try:
        service = get_analysis_service()
        job = service.enqueue_analysis_job(
            ytid=ytid,
            transcript_kind=transcript_kind,
            analysis_model=model,
            priority=priority,
            output_name=output_name,
        )
        click.echo(click.style(f"✓ Analysis job enqueued: {job.job_id}", fg="green"))
    except ValueError as e:
        click.echo(click.style(f"✗ Failed to enqueue analysis job: {e}", fg="yellow"), err=True)
    except Exception as e:
        logger.error(f"Error enqueuing analysis job for {ytid}: {e}", exc_info=True)
        click.echo(click.style(f"✗ Failed to enqueue analysis job: {e}", fg="red"), err=True)


@analyze.command("enqueue-distributed")
@click.argument("ytid")
@click.option("--config-id", required=True, help="ID of the analysis config stored in analysis_configs.")
@click.option("--transcript-kind", default=None, help="Transcript kind to use (defaults to best available).")
@click.option("--chunk-size", type=int, default=None, help="Chunk size override (words).")
@click.option("--chunk-overlap", type=int, default=None, help="Overlap override (words).")
@click.option("--priority", type=int, default=50, help="Job priority (higher = claimed first; default: 50).")
def enqueue_distributed_analysis(
    ytid: str,
    config_id: str,
    transcript_kind: str | None,
    chunk_size: int | None,
    chunk_overlap: int | None,
    priority: int,
) -> None:
    """
    Chunk a transcript locally and enqueue distributed-analysis tasks.
    """
    click.echo(f"Preparing distributed analysis job for {ytid} using config {config_id}...")
    cfg = load_config()
    repo = TranscriptRepository()

    transcript = repo.get(ytid, transcript_kind) if transcript_kind else repo.get_best_available(ytid)
    if not transcript or not transcript.path:
        raise click.ClickException(f"No transcript metadata found for {ytid} (kind={transcript_kind or 'auto'})")

    transcript_path = _resolve_transcript_path(transcript.path, cfg.paths.path_prefix or cfg.paths.central_storage_root)
    if not transcript_path.exists():
        click.echo(f"Transcript missing on disk ({transcript_path}); rebuilding from DB words...", err=True)
        rebuild_dir = _prepare_rebuild_dir(cfg.paths.local_temp_dir)
        transcript_path = _rebuild_transcript_from_db(ytid, rebuild_dir)
        if not transcript_path or not transcript_path.exists():
            raise click.ClickException(
                "Unable to rebuild transcript from DB words; ensure transcription loaded words into the database."
            )

    text = _load_transcript_text(transcript_path)
    if not text.strip():
        raise click.ClickException(f"Transcript at {transcript_path} is empty")

    chunker = TranscriptChunker(
        chunk_size=chunk_size or cfg.analysis.chunk_size_words,
        overlap=chunk_overlap or cfg.analysis.chunk_overlap_words,
    )
    raw_chunks = chunker.chunk(text)
    if not raw_chunks:
        raise click.ClickException("Chunker produced no chunks; check chunk size settings.")

    # Attach metadata needed by downstream passes
    chunk_payload = []
    for idx, chunk in enumerate(raw_chunks):
        payload = {
            "chunk_id": chunk.get("chunk_id", idx),
            "text": chunk.get("text", ""),
            "word_count": chunk.get("word_count"),
            "start_sec": chunk.get("start_sec"),
            "end_sec": chunk.get("end_sec"),
            "speaker": chunk.get("speaker"),
        }
        if transcript_path.suffix.lower() == ".vtt":
            payload["vtt_path"] = str(transcript_path)
        chunk_payload.append(payload)

    db = AnalysisDatabase()
    db.connect()
    try:
        config_row = db.get_analysis_config(config_id)
        if not config_row:
            raise click.ClickException(f"Config id '{config_id}' not found in analysis_configs; add it via the web UI first.")
        config_obj = AnalysisConfig.model_validate(config_row["config_json"])
        job_id = create_analysis_job(
            ytid=ytid,
            config_id=config_id,
            config=config_obj,
            chunks=chunk_payload,
            db=db,
        )
    finally:
        db.disconnect()

    # Also create a job entry in the generic jobs table for GenericWorker
    try:
        job_repo = JobRepository()
        job_config = {
            "analysis_job_id": job_id,
            "config_id": config_id,
            "ytid": ytid,
            "transcript_kind": transcript.kind or "unknown",
            "model_url": cfg.analysis.ollama.url,
            "model_name": cfg.analysis.ollama.model,
        }
        generic_job = Job(
            job_type="analysis-distributed",
            ytid=ytid,
            config=job_config,
            priority=priority,
            status=JobStatus.PENDING,
        )
        created_job = job_repo.create(generic_job)
        click.echo(click.style(f"✓ GenericWorker job created: {created_job.job_id}", fg="green"))
    except Exception as e:
        logger.error(f"Failed to create generic job for distributed analysis: {e}")
        click.echo(
            click.style(
                f"✗ Warning: Failed to create GenericWorker job (distributed analysis will not be picked up by general worker): {e}",
                fg="yellow"
            ),
            err=True,
        )

    click.echo(click.style(f"✓ Distributed analysis job created: {job_id}", fg="green"))
    click.echo(f"  Chunks: {len(chunk_payload)} | Config: {config_id} | Transcript: {transcript.kind}")


def _resolve_transcript_path(path_str: str, prefix: str | None) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    base = Path(prefix) if prefix else Path.cwd()
    return (base / path).resolve()


def _load_transcript_text(transcript_path: Path) -> str:
    if transcript_path.suffix.lower() in {".vtt", ".srt"}:
        parser = VTTParser()
        return parser.parse(transcript_path)
    return transcript_path.read_text(encoding="utf-8", errors="ignore")


def _prepare_rebuild_dir(local_temp_dir: str | None) -> Path:
    """
    Choose a workspace for reconstructed transcripts. Prefer configured temp dir.
    """
    if local_temp_dir:
        base = Path(local_temp_dir)
        if not base.is_absolute():
            base = Path.cwd() / base
    else:
        base = Path.cwd() / "tmp"
    target = base / "analysis_rebuild"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _rebuild_transcript_from_db(ytid: str, output_dir: Path) -> Path | None:
    """
    Use the words table to reconstruct a transcript when the stored file is missing.
    """
    try:
        with get_connection() as conn:
            rebuilt = export_vtt_from_db(conn, ytid, output_dir)
    except Exception as exc:
        logger.error("Failed to rebuild transcript for %s: %s", ytid, exc, exc_info=True)
        return None
    if not rebuilt:
        logger.error("Rebuild returned no transcript for %s; words table may be empty.", ytid)
        return None
    return rebuilt
