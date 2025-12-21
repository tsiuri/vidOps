#!/usr/bin/env python3
"""
Unified launcher for VidOps web interfaces.

This helper starts the merged Analysis + QuickClip Flask app (port 5000)
and an optional monitoring/ops UI (default port 8000). It checks whether
each port is already in use before starting, reports status, and keeps all
child processes alive until you press Ctrl+C.

Examples:
  python scripts/run_webui.py
  python scripts/run_webui.py --skip monitoring
  python scripts/run_webui.py --monitoring-cmd "docker-compose -f docker-compose.monitoring.yml up" --monitoring-port 8000
  python scripts/run_webui.py --status-only
"""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable or "python3"
PORT_CHECK_SUPPORTED = True


@dataclass
class WebUISpec:
    name: str
    description: str
    command: Sequence[str]
    port: Optional[int]
    host: str = "127.0.0.1"
    cwd: Path = PROJECT_ROOT
    env: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    wait_timeout: int = 30
    optional: bool = False


@dataclass
class ProcessHandle:
    spec: WebUISpec
    process: subprocess.Popen


def port_checks_available() -> bool:
    return PORT_CHECK_SUPPORTED


def disable_port_checks_once(reason: str) -> None:
    global PORT_CHECK_SUPPORTED
    if PORT_CHECK_SUPPORTED:
        PORT_CHECK_SUPPORTED = False
        print(f"[webui] Port probing disabled: {reason}")


def is_port_open(host: str, port: int) -> bool:
    """Return True if a TCP port is accepting connections."""
    if not port_checks_available():
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0
    except PermissionError as exc:
        disable_port_checks_once(f"socket permission error ({exc})")
        return False
    except OSError:
        return False


def wait_for_port(host: str, port: int, timeout: int) -> Optional[bool]:
    """Wait until a port becomes reachable, or timeout expires."""
    if not port_checks_available():
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_checks_available():
            return None
        if is_port_open(host, port):
            if not port_checks_available():
                return None
            return True
        time.sleep(0.3)
    return False


def parse_list(values: Optional[Iterable[str]]) -> List[str]:
    if not values:
        return []
    result: List[str] = []
    for value in values:
        if not value:
            continue
        parts = [item.strip() for item in value.split(",")]
        result.extend([p for p in parts if p])
    return result


def parse_command(command_str: Optional[str], default: Sequence[str]) -> List[str]:
    if command_str:
        return shlex.split(command_str)
    return list(default)


def build_specs(args: argparse.Namespace) -> Dict[str, WebUISpec]:
    analysis_cmd = parse_command(
        args.analysis_cmd,
        [PYTHON, "-m", "web.app"],
    )
    analysis_spec = WebUISpec(
        name="analysis",
        description="Analysis + QuickClip Flask UI",
        command=analysis_cmd,
        port=args.analysis_port,
        host=args.host,
        cwd=PROJECT_ROOT,
        url=f"http://{args.host}:{args.analysis_port}/" if args.analysis_port else None,
    )

    monitoring_cmd_str = (
        args.monitoring_cmd or os.environ.get("VIDOPS_MONITORING_CMD")
    )
    if monitoring_cmd_str:
        monitoring_cmd = shlex.split(monitoring_cmd_str)
    else:
        monitoring_cmd = [
            PYTHON,
            "-m",
            "http.server",
            str(args.monitoring_port),
        ]

    monitoring_cwd = PROJECT_ROOT / "monitoring"
    if not monitoring_cwd.exists():
        monitoring_cwd = PROJECT_ROOT

    monitoring_spec = WebUISpec(
        name="monitoring",
        description="Monitoring / dashboards UI",
        command=monitoring_cmd,
        port=args.monitoring_port,
        host=args.host,
        cwd=monitoring_cwd,
        url=f"http://{args.host}:{args.monitoring_port}/" if args.monitoring_port else None,
        optional=True,
    )

    return {
        analysis_spec.name: analysis_spec,
        monitoring_spec.name: monitoring_spec,
    }


def filter_specs(
    specs: Dict[str, WebUISpec],
    only: List[str],
    skip: List[str],
) -> List[WebUISpec]:
    unknown_only = [name for name in only if name not in specs]
    if unknown_only:
        raise SystemExit(f"Unknown apps requested via --only: {', '.join(unknown_only)}")
    unknown_skip = [name for name in skip if name not in specs]
    if unknown_skip:
        raise SystemExit(f"Unknown apps requested via --skip: {', '.join(unknown_skip)}")

    selected = [
        spec for spec in specs.values()
        if (not only or spec.name in only) and spec.name not in skip
    ]
    if not selected:
        raise SystemExit("No web UIs selected. Use --list to see available apps.")
    return selected


def describe(spec: WebUISpec) -> str:
    if spec.port:
        return f"{spec.description} ({spec.host}:{spec.port})"
    return spec.description


def launch_spec(spec: WebUISpec) -> Optional[ProcessHandle]:
    try:
        process = subprocess.Popen(
            spec.command,
            cwd=str(spec.cwd),
            env=spec.env or os.environ.copy(),
        )
        return ProcessHandle(spec=spec, process=process)
    except FileNotFoundError as exc:
        print(f"[{spec.name}] ✗ Command not found: {exc}")
    except Exception as exc:
        print(f"[{spec.name}] ✗ Failed to start: {exc}")
    return None


def terminate_process(process: subprocess.Popen, timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        deadline = time.time() + timeout
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        if process.poll() is None:
            process.kill()
    except Exception:
        process.kill()


def monitor_processes(handles: List[ProcessHandle], stop_event: threading.Event) -> None:
    try:
        while handles and not stop_event.is_set():
            time.sleep(0.5)
            for handle in list(handles):
                ret = handle.process.poll()
                if ret is not None:
                    handles.remove(handle)
                    status = "stopped" if ret == 0 else f"exited ({ret})"
                    print(f"[{handle.spec.name}] {status}")
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        if handles:
            print("\nShutting down web UIs…")
            for handle in handles:
                terminate_process(handle.process)


def print_status(specs: List[WebUISpec]) -> None:
    if not port_checks_available():
        print("Port status checks are unavailable in this environment.")
    for spec in specs:
        if spec.port is None:
            status = "no port configured"
        else:
            running = is_port_open(spec.host, spec.port) if port_checks_available() else False
            if not port_checks_available():
                status = "unknown"
            else:
                status = "RUNNING" if running else "stopped"
        url = f" ({spec.url})" if spec.url else ""
        print(f"[{spec.name}] {status}{url}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch VidOps web UIs.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for health checks (default: 127.0.0.1)")
    parser.add_argument("--analysis-port", type=int, default=5000, help="Port for analysis/QuickClip UI (default: 5000)")
    parser.add_argument("--analysis-cmd", help="Override command for the analysis UI (default: python -m web.app)")
    parser.add_argument("--monitoring-port", type=int, default=8000, help="Port for monitoring UI health checks (default: 8000)")
    parser.add_argument("--monitoring-cmd", help="Command to start monitoring UI (default: python -m http.server from ./monitoring or VIDOPS_MONITORING_CMD)")
    parser.add_argument("--skip", action="append", help="Skip launching specific apps (comma-separated list or repeat flag)")
    parser.add_argument("--only", action="append", help="Restrict launch to specific apps (comma-separated list or repeat flag)")
    parser.add_argument("--status-only", action="store_true", help="Only report status of each app without launching")
    parser.add_argument("--detach", action="store_true", help="Start/ensure apps then exit without watching processes (useful for automation)")
    parser.add_argument("--list", action="store_true", help="List available apps")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    specs = build_specs(args)

    if args.list:
        print("Available web UIs:")
        for spec in specs.values():
            print(f"  - {spec.name}: {describe(spec)}")
        return

    selected = filter_specs(specs, parse_list(args.only), parse_list(args.skip))

    if args.status_only:
        print_status(selected)
        return

    running: List[ProcessHandle] = []
    stop_event = threading.Event()
    results: List[str] = []

    for spec in selected:
        summary = f"[{spec.name}] {describe(spec)}"
        if spec.port and is_port_open(spec.host, spec.port):
            print(f"{summary} — already running, leaving as-is.")
            results.append(f"{spec.name}: already running ({spec.url or spec.port})")
            continue

        print(f"{summary} — starting via {' '.join(spec.command)}")
        handle = launch_spec(spec)
        if not handle:
            results.append(f"{spec.name}: failed to start")
            continue
        running.append(handle)

        # Give the process a brief moment to error out before waiting on ports
        time.sleep(0.2)
        if handle.process.poll() is not None:
            code = handle.process.poll()
            print(f"[{spec.name}] exited immediately with code {code}")
            results.append(f"{spec.name}: exited ({code})")
            running.pop()
            continue

        ready: Optional[bool] = True
        if spec.port:
            ready = wait_for_port(spec.host, spec.port, spec.wait_timeout)
            if ready is None:
                status = "status unknown (port checks disabled)"
                extra = ""
            else:
                status = "ready" if ready else "not reachable yet"
                extra = f"→ {spec.url}" if ready and spec.url else ""
            print(f"[{spec.name}] {status} {extra}".rstrip())
        else:
            print(f"[{spec.name}] started (no port health check)")
        if ready is None:
            summary = "status unknown"
        elif spec.port:
            summary = spec.url or f"port {spec.port}"
        else:
            summary = "no port check"
        results.append(f"{spec.name}: started ({summary})")

    if results:
        print("\nLaunch summary:")
        for line in results:
            print(f"  - {line}")

    if running and not args.detach:
        print("\nPress Ctrl+C to stop all launched web UIs.")
        monitor_processes(running, stop_event)
    elif running and args.detach:
        print("\nLaunched web UIs in detached mode; not monitoring child processes.")
    else:
        print("\nNo new processes were started (all were already active or failed).")


if __name__ == "__main__":
    main()
