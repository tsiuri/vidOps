# vidops/cli/worker.py

import click
import os
import subprocess
import sys
from pathlib import Path
from workers import (
    GenericWorker,
    TranscriptionWorker,
    ClippingWorker,
    AnalysisWorker,
    DiarizeWorker,
    StitchWorker,
    SubtitleWorker,
    DownloadWorker,
    VoiceFilterWorker,
    DatesWorker,
    ExtraUtilsWorker,
)
from workers.analysis_distributed import AnalysisWorker as DistributedAnalysisWorker
from configuration import load_config
import logging

RUN_WEBUI_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_webui.py"

logger = logging.getLogger(__name__)


def ensure_webui_running(host: str = "127.0.0.1", port: int = 5000) -> None:
    """Ensure the unified web UI is running via scripts/run_webui.py (detached)."""
    if port <= 0:
        return
    if not RUN_WEBUI_SCRIPT.exists():
        logger.warning("Web UI script missing at %s; skipping auto-launch.", RUN_WEBUI_SCRIPT)
        return
    cmd = [
        sys.executable or "python3",
        str(RUN_WEBUI_SCRIPT),
        "--only",
        "analysis",
        "--analysis-port",
        str(port),
        "--host",
        host,
        "--detach",
    ]
    try:
        subprocess.run(cmd, check=False, cwd=str(Path(__file__).resolve().parents[1]))
    except Exception as exc:
        logger.warning("Failed to auto-start web UI: %s", exc)

@click.group()
def worker():
    """Manage and start worker processes."""
    pass

@worker.command("start")
@click.argument(
    "worker_type",
    required=False,
    default="general",
    type=click.Choice([
        'general',
        'download',
        'transcription',
        'clipping',
        'analysis',
        'analysis-distributed',
        'diarization',
        'stitching',
        'subtitle',
        'voice',
        'dates',
        'extra_utils',
    ]),
)
@click.option(
    "--machine-alias",
    default=None,
    help="Unique identifier for this worker machine (defaults to hostname)."
)
@click.option(
    "--worker-id",
    default=None,
    help="Worker identifier suffix (e.g., 'gpu-0', 'cpu-1')."
)
@click.option(
    "--model-url",
    default=None,
    help="Ollama model server URL (defaults to config value)."
)
@click.option(
    "--model-name",
    default=None,
    help="Ollama model name (defaults to config value)."
)
@click.option(
    "--capabilities",
    multiple=True,
    help="Worker capabilities (repeatable, e.g., --capabilities gpu_8gb --capabilities qwen2.5:7b)."
)
@click.option(
    "--lease-minutes",
    type=int,
    default=60,
    help="Task lease duration in minutes (default: 60)."
)
@click.option(
    "--metrics-port",
    type=int,
    default=8888,
    help="Port for Prometheus metrics export (default: 8888, set to 0 to disable)."
)
@click.option(
    "--web-port",
    type=int,
    default=5000,
    help="Port for web UI server (analysis config & drills; default: 5000, set to 0 to disable)."
)
def start_worker(
    worker_type: str,
    machine_alias: str,
    worker_id: str,
    model_url: str,
    model_name: str,
    capabilities: tuple,
    lease_minutes: int,
    metrics_port: int,
    web_port: int,
):
    """Start a worker process."""
    click.echo(f"Starting {worker_type} worker...")

    # Default project root to where the worker is launched (unless explicitly set)
    os.environ.setdefault("VIDOPS_PROJECT_ROOT", str(Path.cwd()))
    click.echo(f"Using project root: {os.environ['VIDOPS_PROJECT_ROOT']}")

    if web_port > 0:
        ensure_webui_running(port=web_port)

    # This is a basic way to start. In a production system,
    # you might want to use process management tools like systemd or supervisor
    # which would call the worker script directly.

    web_status_msg = (
        f"http://127.0.0.1:{web_port}/ (managed by run_webui.py)"
        if web_port > 0
        else "disabled"
    )

    if worker_type == "general":
        click.echo(f"  Metrics: {'enabled on port ' + str(metrics_port) if metrics_port > 0 else 'disabled'}")
        click.echo(f"  Web UI: {web_status_msg}")
        worker_instance = GenericWorker(web_port=0, metrics_port=metrics_port)
        worker_instance.run()
    elif worker_type == "download":
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
    elif worker_type == "analysis-distributed":
        try:
            config = load_config()

            # Use provided values or fall back to config
            _machine_alias = machine_alias or os.uname().nodename
            _model_url = model_url or config.analysis.ollama.url
            _model_name = model_name or config.analysis.ollama.model
            if capabilities:
                _capabilities = list(capabilities)
            elif config.analysis.default_capabilities:
                _capabilities = list(config.analysis.default_capabilities)
            else:
                _capabilities = [_model_name, "gpu_8gb"]
            if _model_name and _model_name not in _capabilities:
                _capabilities.append(_model_name)

            click.echo(f"  Machine: {_machine_alias}")
            click.echo(f"  Model: {_model_name} @ {_model_url}")
            click.echo(f"  Capabilities: {', '.join(_capabilities)}")
            click.echo(f"  Lease duration: {lease_minutes} minutes")
            if metrics_port > 0:
                click.echo(f"  Metrics: http://0.0.0.0:{metrics_port}/metrics")
            else:
                click.echo(f"  Metrics: disabled")
            click.echo(f"  Web UI: {web_status_msg}")

            worker_instance = DistributedAnalysisWorker(
                machine_alias=_machine_alias,
                worker_type="analysis_gpu" if "gpu" in _capabilities else "analysis_cpu",
                model_url=_model_url,
                model_name=_model_name,
                capabilities=_capabilities,
                db_host=config.database.host,
                db_name=config.database.name,
                db_user=config.database.user,
                db_password=config.database.password,
                lease_duration_minutes=lease_minutes,
                metrics_port=metrics_port,
            )
            click.echo("Worker initialized. Starting main loop...")
            worker_instance.run_forever()
        except Exception as e:
            click.echo(click.style(f"✗ Failed to start distributed analysis worker: {e}", fg="red"), err=True)
            logger.error(f"Distributed analysis worker error: {e}", exc_info=True)
    elif worker_type == "diarization":
        try:
            if metrics_port > 0:
                click.echo(f"  Metrics: http://0.0.0.0:{metrics_port}/metrics")
            else:
                click.echo(f"  Metrics: disabled")

            worker_instance = DiarizeWorker(metrics_port=metrics_port)
            click.echo("Worker initialized. Starting main loop...")
            worker_instance.run()
        except Exception as e:
            click.echo(click.style(f"✗ Failed to start diarization worker: {e}", fg="red"), err=True)
            logger.error(f"Diarization worker error: {e}", exc_info=True)
    elif worker_type == "stitching":
        worker_instance = StitchWorker()
        worker_instance.run()
    elif worker_type == "subtitle":
        worker_instance = SubtitleWorker()
        worker_instance.run()
    elif worker_type == "voice":
        worker_instance = VoiceFilterWorker()
        worker_instance.run()
    elif worker_type == "dates":
        worker_instance = DatesWorker()
        worker_instance.run()
    elif worker_type == "extra_utils":
        worker_instance = ExtraUtilsWorker()
        worker_instance.run()
    else:
        click.echo(click.style(f"Error: Unknown worker type '{worker_type}'", fg="red"), err=True)
