# vidops/cli/pipeline.py

import click
import uuid
import time
from typing import Optional

from services import (
    get_download_service,
    get_transcription_service,
    get_diarization_service,
)
from dal import JobRepository, VideoRepository
from models import Video
from configuration import load_config
from db import get_connection


@click.group()
def pipeline():
    """Enqueue and manage multi-stage processing pipelines."""
    pass


@pipeline.command("enqueue")
@click.argument("ytid_or_url")
@click.option(
    "--skip-download",
    is_flag=True,
    help="Skip download (video already present)",
)
@click.option("--skip-transcription", is_flag=True, help="Skip transcription")
@click.option("--skip-diarization", is_flag=True, help="Skip diarization")
@click.option("--skip-analysis", is_flag=True, help="Skip analysis")
@click.option(
    "--transcription-model",
    default=None,
    help="Whisper model (default from config.yaml)",
)
@click.option(
    "--transcription-language",
    default=None,
    help="Transcription language (default from config.yaml)",
)
@click.option(
    "--diarization-device",
    default=None,
    help="Diarization device (default from config.yaml)",
)
@click.option("--analysis-config-id", type=int, default=1, help="Analysis config ID")
@click.option("--priority", type=int, default=50, help="Pipeline priority")
def enqueue_pipeline(
    ytid_or_url: str,
    skip_download: bool,
    skip_transcription: bool,
    skip_diarization: bool,
    skip_analysis: bool,
    transcription_model: str | None,
    transcription_language: str | None,
    diarization_device: str | None,
    analysis_config_id: int,
    priority: int,
):
    """
    Enqueue a full processing pipeline for a video.

    All jobs are created immediately with dependency tracking.
    Jobs wait in PENDING until prerequisites complete.

    Examples:
        # Full pipeline from URL
        vo pipeline enqueue https://youtube.com/watch?v=XYZ

        # Skip diarization
        vo pipeline enqueue XYZ --skip-diarization

        # Custom transcription model
        vo pipeline enqueue XYZ --transcription-model large-v3

        # Already downloaded, start from transcription
        vo pipeline enqueue XYZ --skip-download
    """
    config = load_config()
    job_repo = JobRepository()
    video_repo = VideoRepository()

    # Generate unique pipeline ID
    pipeline_id = f"pipe_{uuid.uuid4().hex[:8]}"

    # Extract YTID
    if ytid_or_url.startswith("http"):
        # Extract from URL
        ytid = ytid_or_url.split("watch?v=")[-1].split("&")[0]
        url = ytid_or_url
    else:
        ytid = ytid_or_url
        url = f"https://youtube.com/watch?v={ytid}"

    click.echo(f"Enqueuing pipeline {pipeline_id} for {ytid}")

    # Create placeholder video record so dependent stages can reference it
    # The download job will fill in full metadata when it completes
    placeholder_video = Video(
        ytid=ytid,
        url=url,
        title=f"[Pending] {ytid}"  # Placeholder title, will be updated by download job
    )
    video_repo.upsert(placeholder_video)

    # Track created jobs
    jobs_created = []
    prev_job_id: Optional[str] = None

    # Stage 1: Download
    if not skip_download:
        download_service = get_download_service()
        download_job = download_service.enqueue_download(url=url, priority=priority)

        # Add pipeline metadata to job config
        download_job.config["pipeline_id"] = pipeline_id
        download_job.config["pipeline_stage"] = "download"
        job_repo.update_config(download_job.job_id, download_job.config)

        jobs_created.append(("download", download_job.job_id))
        prev_job_id = download_job.job_id
        click.echo(f"  ✓ Download job created: {download_job.job_id}")

    # Stage 2: Transcription
    if not skip_transcription:
        transcription_service = get_transcription_service()

        trans_model = transcription_model or config.transcription.model
        trans_lang = transcription_language or config.transcription.language

        trans_job = transcription_service.enqueue_video(
            ytid=ytid,
            model=trans_model,
            language=trans_lang,
            priority=priority,
        )

        # Add pipeline metadata and dependency
        trans_job.config["pipeline_id"] = pipeline_id
        trans_job.config["pipeline_stage"] = "transcription"
        if prev_job_id:
            trans_job.config["depends_on"] = prev_job_id
        job_repo.update_config(trans_job.job_id, trans_job.config)

        jobs_created.append(("transcription", trans_job.job_id))
        prev_job_id = trans_job.job_id

        dep_msg = (
            f" (depends on {trans_job.config['depends_on'][:8]}...)"
            if "depends_on" in trans_job.config
            else ""
        )
        click.echo(f"  ✓ Transcription job created: {trans_job.job_id}{dep_msg}")

    # Stage 3: Diarization
    if not skip_diarization:
        diarization_service = get_diarization_service()

        # Determine transcript kind from transcription config
        trans_model = transcription_model or config.transcription.model
        transcript_kind = f"words_whisper_{trans_model}"

        diar_job = diarization_service.enqueue_diarization_job(
            ytid=ytid,
            transcript_kind=transcript_kind,
            device=diarization_device,  # None = use config default
            priority=priority,
            force_enqueue=True,  # Skip validation - transcript will exist when job runs
        )

        # Add pipeline metadata and dependency
        diar_job.config["pipeline_id"] = pipeline_id
        diar_job.config["pipeline_stage"] = "diarization"
        if prev_job_id:
            diar_job.config["depends_on"] = prev_job_id
        job_repo.update_config(diar_job.job_id, diar_job.config)

        jobs_created.append(("diarization", diar_job.job_id))
        prev_job_id = diar_job.job_id

        dep_msg = (
            f" (depends on {diar_job.config['depends_on'][:8]}...)"
            if "depends_on" in diar_job.config
            else ""
        )
        click.echo(f"  ✓ Diarization job created: {diar_job.job_id}{dep_msg}")

    # Stage 4: Analysis
    if not skip_analysis:
        # Note: Analysis job creation is deferred until transcription/diarization complete
        # This allows us to properly chunk the transcript that may not exist yet
        click.echo(f"  ℹ Analysis will be enqueued after diarization completes (manual: vo analyze enqueue {ytid} --config-id {analysis_config_id})")

    click.echo(
        f"\n✓ Pipeline {pipeline_id} enqueued with {len(jobs_created)} stages"
    )
    click.echo("\nPipeline flow:")
    for idx, (stage, job_id) in enumerate(jobs_created):
        prefix = "  " + ("└─" if idx == len(jobs_created) - 1 else "├─")
        click.echo(f"{prefix} {stage}: {job_id}")

    click.echo(f"\nMonitor progress:")
    click.echo(f"  vo status jobs")
    click.echo(f"  vo pipeline status {pipeline_id}")


@pipeline.command("status")
@click.argument("pipeline_id")
def pipeline_status(pipeline_id: str):
    """Show status of all jobs in a pipeline."""
    try:
        from tabulate import tabulate
    except ImportError:
        click.echo(
            "tabulate module not found. Install with: pip install tabulate",
            err=True,
        )
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id, job_type, status, priority,
                       config->>'depends_on' as depends_on,
                       created_at, completed_at
                FROM jobs
                WHERE config->>'pipeline_id' = %s
                ORDER BY created_at ASC
                """,
                (pipeline_id,),
            )
            rows = cur.fetchall()

    if not rows:
        click.echo(f"No jobs found for pipeline {pipeline_id}")
        return

    # Format as table
    headers = ["Job ID", "Stage", "Status", "Depends On", "Created", "Completed"]
    table_data = []
    for row in rows:
        job_id, job_type, status, priority, depends_on, created_at, completed_at = row
        table_data.append(
            [
                job_id[:12],
                job_type,
                status,
                depends_on[:8] if depends_on else "-",
                created_at.strftime("%H:%M:%S"),
                completed_at.strftime("%H:%M:%S") if completed_at else "-",
            ]
        )

    click.echo(f"\nPipeline: {pipeline_id}")
    click.echo(tabulate(table_data, headers=headers, tablefmt="simple"))
