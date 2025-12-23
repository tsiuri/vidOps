# vo_cli.py

# Auto-activate venv if available and not already using it
import sys
import os
from pathlib import Path

_VENV_DIR = Path(__file__).parent / ".venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python"
# sys.prefix points to venv when activated; differs from sys.base_prefix
if _VENV_PYTHON.exists() and Path(sys.prefix) != _VENV_DIR.resolve():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)

# -----------------------------------------------------------------------------
# Early GPU selection - MUST happen before any CUDA/torch imports
# Parse --gpu from sys.argv before click processes it, so we can set
# CUDA_VISIBLE_DEVICES before any library tries to initialize CUDA.
# -----------------------------------------------------------------------------
def _parse_early_gpu_flag():
    """Extract --gpu value from sys.argv before heavy imports."""
    gpu_val = None
    for i, arg in enumerate(sys.argv):
        if arg == "--gpu" and i + 1 < len(sys.argv):
            gpu_val = sys.argv[i + 1]
            break
        elif arg.startswith("--gpu="):
            gpu_val = arg.split("=", 1)[1]
            break
    return gpu_val

_EARLY_GPU = _parse_early_gpu_flag()
if _EARLY_GPU is not None:
    if _EARLY_GPU.lower() == "cpu":
        # CPU-only mode: hide all GPUs from CUDA
        # TODO: CPU-only worker not fully implemented yet
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    elif _EARLY_GPU.isdigit():
        # Specific GPU index
        os.environ["CUDA_VISIBLE_DEVICES"] = _EARLY_GPU
    # "auto" or invalid values: don't set CUDA_VISIBLE_DEVICES, let CUDA decide

# Store for later use by worker CLI
os.environ["_VIDOPS_GPU_FLAG"] = _EARLY_GPU if _EARLY_GPU else ""
# -----------------------------------------------------------------------------

import click
from db import close_pool
from cli.status import status
from cli.worker import worker
from cli.download import download
from cli.transcribe import transcribe
from cli.clipping import clip
from cli.overlord import overlord
from cli.analysis import analyze
from cli.diarization import diarize
from cli.clips import clips
from cli.dl_subs import dl_subs
from cli.stitch import stitch
from cli.voice import voice
from cli.query_ids import query_ids
from cli.convert_captions import convert_captions
from cli.dates import dates
from cli.extra_utils import extra_utils
from cli.quickclip import quickclip
from cli.pipeline import pipeline
from cli.webui import webui
from cli.hc_export import hc_export
from cli.monitor import monitor
from __init__ import __version__  # Import the version from the package

@click.group(
    help="""
    VidOps - Unified Video Processing Toolkit (Overlord System)

    This CLI allows you to manage video processing tasks such as downloading,
    transcribing, clipping, analyzing, diarizing, and stitching. It interacts
    with a centralized PostgreSQL database for job management and worker coordination.

    Use 'vo_cli.py <command> --help' for more information on a specific command.
    """
)
@click.version_option(__version__, "-v", "--version", message="VidOps Version: %(version)s")
def cli():
    """VidOps - Unified Video Processing Toolkit"""
    pass

# Register commands
cli.add_command(status)
cli.add_command(worker)
cli.add_command(download)
cli.add_command(transcribe)
cli.add_command(clip)
cli.add_command(overlord)
cli.add_command(analyze)
cli.add_command(diarize)
cli.add_command(clips)
cli.add_command(stitch)
cli.add_command(dl_subs)
cli.add_command(voice)
cli.add_command(query_ids)
cli.add_command(dates)
cli.add_command(extra_utils)
cli.add_command(convert_captions)
cli.add_command(quickclip)
cli.add_command(pipeline)
cli.add_command(webui)
cli.add_command(hc_export)
cli.add_command(monitor)

if __name__ == '__main__':
    try:
        cli()
    finally:
        # Ensure database connections are closed on exit
        close_pool()
