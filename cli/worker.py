# vidops/cli/worker.py

import click
import os
import threading
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
import socket

logger = logging.getLogger(__name__)

def start_web_server(host: str = "127.0.0.1", port: int = 5000):
    """Start Flask web server in a background thread if the port is free."""
    import sys
    from pathlib import Path
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                logger.warning("Web UI already running on http://%s:%s; skipping embedded server.", host, port)
                return
        # Add project paths so the web app can find scripts/analysis modules
        web_dir = Path(__file__).parent.parent / 'web'
        project_root = web_dir.parent
        sys.path.insert(0, str(project_root / 'scripts' / 'analysis'))
        sys.path.insert(0, str(project_root / 'scripts'))
        sys.path.insert(0, str(web_dir / 'scripts'))
        sys.path.insert(0, str(web_dir))  # This will be searched first

        from web_app import app
        # Run Flask in a daemon thread so it doesn't block worker shutdown
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Failed to start web server: {e}", exc_info=True)

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

    # This is a basic way to start. In a production system,
    # you might want to use process management tools like systemd or supervisor
    # which would call the worker script directly.

    if worker_type == "general":
        click.echo(f"  Metrics: {'enabled on port ' + str(metrics_port) if metrics_port > 0 else 'disabled'}")
        click.echo(f"  Web UI: {'enabled on http://127.0.0.1:' + str(web_port) if web_port > 0 else 'disabled'}")
        worker_instance = GenericWorker(web_port=web_port, metrics_port=metrics_port)
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

            # Start web server in background thread (skip silently if already running)
            web_thread = threading.Thread(target=start_web_server, kwargs={"host": "127.0.0.1", "port": 5000}, daemon=True)
            web_thread.start()
            click.echo(f"  Web UI: http://127.0.0.1:5000/analysis-configs")

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
