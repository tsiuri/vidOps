# vo_cli.py

# Auto-activate venv if available and not already using it
import sys
import os
import importlib.util
import subprocess
from pathlib import Path

_VENV_DIR = Path(__file__).parent / ".venv"
_VENV_PYTHON = None
_VENV_CANDIDATES = [
    _VENV_DIR / "Scripts" / "python.exe",
    _VENV_DIR / "bin" / "python",
]
for candidate in _VENV_CANDIDATES:
    if candidate.exists():
        _VENV_PYTHON = candidate
        break
# sys.prefix points to venv when activated; differs from sys.base_prefix
if _VENV_PYTHON and Path(sys.prefix) != _VENV_DIR.resolve():
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

def _map_gpu_index(gpu_index: str) -> str:
    """
    Map a logical GPU index to CUDA_VISIBLE_DEVICES using VIDOPS_GPU_INDEX_MAP.
    Format: "0:1,1:0" (defaults to identity if unset or invalid).
    """
    mapping = os.environ.get("VIDOPS_GPU_INDEX_MAP", "")
    if not mapping:
        return gpu_index
    mapped = {}
    for pair in mapping.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        left, right = pair.split(":", 1)
        left = left.strip()
        right = right.strip()
        if left and right:
            mapped[left] = right
    return mapped.get(gpu_index, gpu_index)

_EARLY_GPU = _parse_early_gpu_flag()
if _EARLY_GPU is not None:
    if _EARLY_GPU.lower() == "cpu":
        # CPU-only mode: hide all GPUs from CUDA
        # TODO: CPU-only worker not fully implemented yet
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    elif _EARLY_GPU.isdigit():
        # Optional GPU index map via VIDOPS_GPU_INDEX_MAP for non-standard numbering.
        os.environ["CUDA_VISIBLE_DEVICES"] = _map_gpu_index(_EARLY_GPU)
    # "auto" or invalid values: don't set CUDA_VISIBLE_DEVICES, let CUDA decide

# Store for later use by worker CLI
os.environ["_VIDOPS_GPU_FLAG"] = _EARLY_GPU if _EARLY_GPU else ""
# -----------------------------------------------------------------------------

_AUTO_INSTALL_TIMEOUT = 120
_FULL_INSTALL_TIMEOUT = 900


def _resolve_requirement(requirements_path: Path, package: str) -> str:
    if not requirements_path.exists():
        return package
    needle = package.lower()
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("--"):
            continue
        base = line.split(";", 1)[0].strip()
        base_lower = base.lower()
        if (
            base_lower == needle
            or base_lower.startswith(needle + "[")
            or base_lower.startswith(needle + "==")
            or base_lower.startswith(needle + ">")
            or base_lower.startswith(needle + "<")
            or base_lower.startswith(needle + "~")
        ):
            return line
    return package


def _ensure_modules(modules: dict[str, str]) -> None:
    missing: list[str] = []
    requirements_path = Path(__file__).with_name("requirements.txt")
    for module_name, pip_name in modules.items():
        if importlib.util.find_spec(module_name) is None:
            requirement = _resolve_requirement(requirements_path, pip_name)
            missing.append(requirement)

    if not missing:
        return

    if importlib.util.find_spec("pip") is None:
        try:
            subprocess.run(
                [sys.executable, "-m", "ensurepip", "--upgrade"],
                check=False,
                timeout=60,
            )
        except Exception:
            pass

    print(f"Missing dependencies detected: {', '.join(missing)}")
    requirements_path = Path(__file__).with_name("requirements.txt")

    if requirements_path.exists():
        timeout = int(os.environ.get("VIDOPS_INSTALL_TIMEOUT", _FULL_INSTALL_TIMEOUT))
        assume_yes = os.environ.get("VIDOPS_ASSUME_YES") == "1"
        if timeout > _AUTO_INSTALL_TIMEOUT and not assume_yes:
            if sys.stdin and sys.stdin.isatty():
                prompt = (
                    f"Install full requirements now? This can take several minutes "
                    f"(timeout {timeout}s). [y/N]: "
                )
                resp = input(prompt).strip().lower()
                if resp not in {"y", "yes"}:
                    print("Install cancelled. Run pip install -r requirements.txt manually.")
                    raise SystemExit(1)
            else:
                print(
                    "Missing dependencies and long install required. "
                    "Set VIDOPS_ASSUME_YES=1 or run pip install -r requirements.txt manually."
                )
                raise SystemExit(1)

        print(f"Attempting full install via pip (timeout={timeout}s)...")
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(requirements_path)]
    else:
        timeout = _AUTO_INSTALL_TIMEOUT
        print(f"Attempting minimal install via pip (timeout={timeout}s)...")
        cmd = [sys.executable, "-m", "pip", "install", *missing]

    try:
        result = subprocess.run(
            cmd,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print("Auto-install timed out. Run pip install -r requirements.txt manually.")
        raise SystemExit(1)
    except Exception as exc:
        print(f"Auto-install failed: {exc}")
        raise SystemExit(1)

    if result.returncode != 0:
        print("Auto-install failed. Run pip install -r requirements.txt manually.")
        raise SystemExit(1)


_ensure_modules(
    {
        "click": "click",
        "yaml": "PyYAML",
        "psycopg2": "psycopg2-binary",
        "httpx": "httpx",
    }
)

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
