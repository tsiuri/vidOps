import sys
import subprocess
from pathlib import Path
from typing import Iterable, List

import click


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_webui.py"


def _extend_args(args: List[str], flag: str, values: Iterable[str]) -> None:
    for value in values:
        if value:
            args.extend([flag, value])


@click.command(help="Launch or inspect VidOps web UIs via scripts/run_webui.py")
@click.option("--status-only", is_flag=True, help="Only report status without launching any UI")
@click.option("--list", "list_only", is_flag=True, help="List available web UIs")
@click.option("--skip", multiple=True, help="Skip launching specific apps (repeatable)")
@click.option("--only", multiple=True, help="Limit launch to these apps (repeatable)")
@click.option("--analysis-port", type=int, help="Override analysis/QuickClip UI port")
@click.option("--analysis-cmd", help="Override analysis UI command string")
@click.option("--monitoring-port", type=int, help="Override monitoring UI port")
@click.option("--monitoring-cmd", help="Override monitoring UI command string")
@click.option("--host", help="Host used for readiness checks (default 127.0.0.1)")
def webui(
    status_only: bool,
    list_only: bool,
    skip: Iterable[str],
    only: Iterable[str],
    analysis_port: int,
    analysis_cmd: str,
    monitoring_port: int,
    monitoring_cmd: str,
    host: str,
) -> None:
    """Invoke scripts/run_webui.py with passthrough options."""
    if not SCRIPT_PATH.exists():
        raise click.ClickException(f"Missing helper script: {SCRIPT_PATH}")

    cmd: List[str] = [sys.executable, str(SCRIPT_PATH)]
    if status_only:
        cmd.append("--status-only")
    if list_only:
        cmd.append("--list")
    if analysis_port:
        cmd.extend(["--analysis-port", str(analysis_port)])
    if analysis_cmd:
        cmd.extend(["--analysis-cmd", analysis_cmd])
    if monitoring_port:
        cmd.extend(["--monitoring-port", str(monitoring_port)])
    if monitoring_cmd:
        cmd.extend(["--monitoring-cmd", monitoring_cmd])
    if host:
        cmd.extend(["--host", host])

    _extend_args(cmd, "--skip", skip)
    _extend_args(cmd, "--only", only)

    try:
        subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"webui launcher exited with {exc.returncode}") from exc
