"""Cross-platform process management helpers."""

from __future__ import annotations

import os
import platform
import signal
import subprocess
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class ProcessManager(ABC):
    """Abstract base for platform-specific process management."""

    @abstractmethod
    def spawn_isolated(
        self,
        cmd: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> subprocess.Popen:
        """Spawn a subprocess in an isolated group/session for clean termination."""

    @abstractmethod
    def kill_process_tree(self, proc: subprocess.Popen, timeout: float = 5.0) -> None:
        """Kill a process and all descendants, best-effort."""


class LinuxProcessManager(ProcessManager):
    """Process management for POSIX platforms."""

    def spawn_isolated(
        self,
        cmd: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> subprocess.Popen:
        return subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            text=True,
            start_new_session=True,
        )

    def kill_process_tree(self, proc: subprocess.Popen, timeout: float = 5.0) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=timeout)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()


class WindowsProcessManager(ProcessManager):
    """Process management for Windows platforms."""

    def spawn_isolated(
        self,
        cmd: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> subprocess.Popen:
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            text=True,
            creationflags=creation_flags,
        )

    def kill_process_tree(self, proc: subprocess.Popen, timeout: float = 5.0) -> None:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                check=False,
                capture_output=True,
                timeout=timeout,
            )
        except Exception:
            proc.kill()


def get_process_manager() -> ProcessManager:
    """Return a platform-appropriate process manager."""
    if platform.system() == "Windows":
        return WindowsProcessManager()
    return LinuxProcessManager()
