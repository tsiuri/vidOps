# vidops/cli/worker.py

import click
from vidops.workers import (
    TranscriptionWorker,
    ClippingWorker,
    AnalysisWorker,
    DiarizeWorker,
    StitchWorker,
    SubtitleWorker,
    DownloadWorker,
    VoiceFilterWorker,
)
import logging

logger = logging.getLogger(__name__)

@click.group()
def worker():
    """Manage and start worker processes."""
    pass

@worker.command("start")
@click.argument(
    "worker_type",
    type=click.Choice(['download', 'transcription', 'clipping', 'analysis', 'diarization', 'stitching', 'subtitle', 'voice']),
    default='transcription'
)
def start_worker(worker_type: str):
    """Start a worker process."""
    click.echo(f"Starting {worker_type} worker...")
    
    # This is a basic way to start. In a production system,
    # you might want to use process management tools like systemd or supervisor
    # which would call the worker script directly.
    
    if worker_type == "download":
        worker_instance = DownloadWorker()
        worker_instance.run()
    elif worker_type == "transcription":
        worker_instance = TranscriptionWorker()
        worker_instance.run()
    elif worker_type == "clipping":
        worker_instance = ClippingWorker()
        worker_instance.run()
    elif worker_type == "analysis":
        worker_instance = AnalysisWorker()
        worker_instance.run()
    elif worker_type == "diarization":
        worker_instance = DiarizeWorker()
        worker_instance.run()
    elif worker_type == "stitching":
        worker_instance = StitchWorker()
        worker_instance.run()
    elif worker_type == "subtitle":
        worker_instance = SubtitleWorker()
        worker_instance.run()
    elif worker_type == "voice":
        worker_instance = VoiceFilterWorker()
        worker_instance.run()
    else:
        click.echo(click.style(f"Error: Unknown worker type '{worker_type}'", fg="red"), err=True)
