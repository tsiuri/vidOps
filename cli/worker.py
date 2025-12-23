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
    DiarizeWorker,
    StitchWorker,
    SubtitleWorker,
    DownloadWorker,
    VoiceFilterWorker,
    DatesWorker,
    ExtraUtilsWorker,
)
from workers.analysis_distributed import AnalysisWorker as DistributedAnalysisWorker
from configuration import load_config, OLLAMA_BASE_PORT
import logging
from typing import Optional

RUN_WEBUI_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_webui.py"
DEFAULT_ANALYSIS_PORT = 5000
DEFAULT_MONITORING_PORT = 8000

logger = logging.getLogger(__name__)


def _get_gpu_flag() -> Optional[str]:
    """Get the --gpu flag value that was parsed early in vo_cli.py."""
    return os.environ.get("_VIDOPS_GPU_FLAG") or None


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
    help="Port for web UI server when --web-services is enabled (default: 5000)."
)
@click.option(
    "--web-services",
    is_flag=True,
    default=False,
    help="Start web UI services (analysis config & drills) alongside the worker."
)
@click.option(
    "--gpu",
    "gpu_flag",
    default=None,
    help="GPU index to use (0, 1, 2...), 'cpu' for CPU-only, or 'auto' (default). "
         "Sets CUDA_VISIBLE_DEVICES and loads per-GPU config (capabilities, ollama URL)."
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
    web_services: bool,
    gpu_flag: str,
):
    """Start a worker process."""
    click.echo(f"Starting {worker_type} worker...")

    # Default project root to where the worker is launched (unless explicitly set)
    os.environ.setdefault("VIDOPS_PROJECT_ROOT", str(Path.cwd()))
    click.echo(f"Using project root: {os.environ['VIDOPS_PROJECT_ROOT']}")

    # Load config and resolve GPU settings
    config = load_config()

    # Resolve GPU flag (CLI overrides early-parsed value)
    effective_gpu = gpu_flag or _get_gpu_flag()
    gpu_index: Optional[int] = None
    gpu_profile = None

    if effective_gpu is not None:
        if effective_gpu.lower() == "cpu":
            click.echo("  GPU: CPU-only mode (CUDA disabled)")
            click.echo("  NOTE: CPU-only worker not fully implemented yet")
        elif effective_gpu.lower() == "auto":
            click.echo("  GPU: auto (CUDA will select)")
        elif effective_gpu.isdigit():
            gpu_index = int(effective_gpu)
            gpu_profile = config.gpus.get_profile(gpu_index)
            gpu_name = gpu_profile.name or f"GPU {gpu_index}"
            click.echo(f"  GPU: {gpu_index} ({gpu_name})")
            click.echo(f"  CUDA_VISIBLE_DEVICES: {os.environ.get('CUDA_VISIBLE_DEVICES', 'not set')}")
            if gpu_profile.capabilities:
                click.echo(f"  Configured capabilities: {', '.join(gpu_profile.capabilities)}")
            if gpu_profile.ollama_url:
                click.echo(f"  Ollama URL: {gpu_profile.ollama_url}")
            if gpu_profile.model_name:
                click.echo(f"  Model: {gpu_profile.model_name}")
        else:
            click.echo(click.style(f"  Warning: Invalid --gpu value '{effective_gpu}', using auto", fg="yellow"))
    else:
        click.echo("  GPU: auto (use --gpu N to select specific GPU)")

    # Show available services
    click.echo("")
    click.echo("Available services:")
    click.echo(f"  Analysis UI:   http://127.0.0.1:{DEFAULT_ANALYSIS_PORT}/")
    click.echo(f"  Monitoring:    http://127.0.0.1:{DEFAULT_MONITORING_PORT}/")
    click.echo(f"  Job Monitor:   vo monitor  (curses TUI)")
    click.echo("")

    if web_services:
        ensure_webui_running(port=web_port)

    # This is a basic way to start. In a production system,
    # you might want to use process management tools like systemd or supervisor
    # which would call the worker script directly.

    web_status_msg = (
        f"http://127.0.0.1:{web_port}/ (managed by run_webui.py)"
        if web_services
        else "disabled (use --web-services to enable)"
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
    elif worker_type in ("analysis", "analysis-distributed"):
        try:
            # Use provided values, then GPU profile, then base config
            _machine_alias = machine_alias or os.uname().nodename

            # Model URL precedence: CLI > GPU profile > base config
            if model_url:
                _model_url = model_url
            elif gpu_profile and gpu_profile.ollama_url:
                _model_url = gpu_profile.ollama_url
            else:
                _model_url = config.analysis.ollama.url

            # Model name precedence: CLI > GPU profile > base config
            if model_name:
                _model_name = model_name
            elif gpu_profile and gpu_profile.model_name:
                _model_name = gpu_profile.model_name
            else:
                _model_name = config.analysis.ollama.model

            # Capabilities precedence: CLI > GPU profile > base config > default
            if capabilities:
                _capabilities = list(capabilities)
            elif gpu_profile and gpu_profile.capabilities:
                _capabilities = list(gpu_profile.capabilities)
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
            if worker_type == "analysis":
                click.echo("  (alias) Using distributed analysis worker for legacy 'analysis' type")

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
