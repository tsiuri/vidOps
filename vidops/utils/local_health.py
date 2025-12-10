# vidops/utils/local_health.py
"""
Utilities for detecting worker-local health issues.

Used by services to distinguish local failures from job failures.
"""

import errno
import logging
import os
from pathlib import Path
from typing import Optional

from vidops.exceptions import (
    DiskSpaceError,
    GPUUnavailableError,
    MountUnavailableError,
    LocalPermissionError,
)

logger = logging.getLogger(__name__)


def check_disk_space(path: Path, min_gb: float = 30.0) -> None:
    """
    Check if path has sufficient free disk space.

    Args:
        path: Directory to check
        min_gb: Minimum required free space in GB (default: 30.0)

    Raises:
        DiskSpaceError: If insufficient space available
    """
    try:
        stat = os.statvfs(str(path))
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)

        if free_gb < min_gb:
            raise DiskSpaceError(
                f"Insufficient disk space on {path}: {free_gb:.2f}GB free, {min_gb}GB required"
            )
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise DiskSpaceError(f"No space left on device: {path}") from e
        # Other OSErrors are re-raised (might be mount issues, permissions, etc.)
        raise


def check_mount_available(path: Path) -> None:
    """
    Check if a mount point is available and accessible.

    Args:
        path: Path to check (typically a mount point)

    Raises:
        MountUnavailableError: If mount is not available or accessible
    """
    if not path.exists():
        raise MountUnavailableError(f"Mount point does not exist: {path}")

    if not path.is_dir():
        raise MountUnavailableError(f"Mount point is not a directory: {path}")

    # Try to list directory to verify read access
    try:
        list(path.iterdir())
    except PermissionError as e:
        raise LocalPermissionError(f"Permission denied accessing mount: {path}") from e
    except OSError as e:
        raise MountUnavailableError(f"Cannot access mount: {path}: {e}") from e


def check_write_permission(path: Path) -> None:
    """
    Check if we have write permission to a directory.

    Args:
        path: Directory to check

    Raises:
        LocalPermissionError: If write permission denied
    """
    if not path.exists():
        # Try to create it
        try:
            path.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise LocalPermissionError(f"Permission denied creating directory: {path}") from e
        except OSError as e:
            if e.errno == errno.EACCES:
                raise LocalPermissionError(f"Permission denied: {path}") from e
            raise

    # Test write access with a temp file
    try:
        test_file = path / f".write_test_{os.getpid()}"
        test_file.touch()
        test_file.unlink()
    except PermissionError as e:
        raise LocalPermissionError(f"Permission denied writing to: {path}") from e
    except OSError as e:
        if e.errno == errno.EACCES:
            raise LocalPermissionError(f"Permission denied: {path}") from e
        elif e.errno == errno.ENOSPC:
            raise DiskSpaceError(f"No space left on device: {path}") from e
        raise


def check_gpu_available(device: Optional[str] = None) -> None:
    """
    Check if GPU is available.

    Args:
        device: Specific device to check (e.g., "cuda:0"), or None for any CUDA device

    Raises:
        GPUUnavailableError: If GPU is not available
    """
    try:
        import torch
    except ImportError:
        # torch not installed - if job requires GPU, this is a worker config issue
        raise GPUUnavailableError("PyTorch not installed on this worker")

    if not torch.cuda.is_available():
        raise GPUUnavailableError("CUDA/GPU not available on this worker")

    if device and device.startswith("cuda"):
        device_num = int(device.split(":")[-1]) if ":" in device else 0
        if device_num >= torch.cuda.device_count():
            raise GPUUnavailableError(
                f"GPU device {device} not available (only {torch.cuda.device_count()} devices)"
            )


def wrap_os_error(error: OSError, path: Path) -> Exception:
    """
    Convert OSError to appropriate WorkerLocalError.

    Args:
        error: The OSError that occurred
        path: Path where the error occurred

    Returns:
        Appropriate exception (DiskSpaceError, LocalPermissionError, etc.)
    """
    if error.errno == errno.ENOSPC:
        return DiskSpaceError(f"No space left on device: {path}")
    elif error.errno == errno.EACCES:
        # Check if it's a local path (tmp/, workspace) vs central storage
        path_str = str(path).lower()
        if any(local in path_str for local in ["tmp/", "tmp\\", "/tmp", "workspace"]):
            return LocalPermissionError(f"Permission denied (local): {path}")
        # Permission denied on central storage is a job issue (file doesn't exist for us)
        return error
    elif error.errno == errno.ENOENT:
        # Check if it's a mount point
        if any(mount in str(path) for mount in ["/mnt/", "/media/"]):
            return MountUnavailableError(f"Mount point missing: {path}")
        # Regular "file not found" - let caller decide if it's local or job issue
        return error
    else:
        return error
