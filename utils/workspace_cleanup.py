# vidops/utils/workspace_cleanup.py
"""
Workspace cleanup helper for large temporary artifacts.

Runs targeted, size-thresholded sweeps over known heavy directories
without scanning the entire tree. Designed to be invoked at worker
startup when workspace size exceeds a configured trigger.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Set

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceCleanupConfig:
    enabled: bool = False
    trigger_workspace_size_gb: float = 0.0
    min_bytes: int = 50_000_000
    max_deletions: int = 200
    min_age_minutes: int = 120
    paths: List[str] = field(
        default_factory=lambda: [
            "tmp/raw",
            "tmp/generated",
            "tmp/*_chunks",
            "tmp/*_chunk_results",
            "tmp/reference_builder",
            "tmp/mnt",
            "pull",
            "generated",
        ]
    )
    skip_exts: List[str] = field(default_factory=lambda: [".txt", ".tsv", ".csv", ".json", ".jsonl", ".log"])


class WorkspaceCleanup:
    """Targeted cleanup of large files in known heavy directories."""

    def __init__(self, config: WorkspaceCleanupConfig):
        self.config = config

    def run(self, workspace_root: Path, tmp_root: Path) -> dict:
        """
        Sweep allowlisted directories and remove files over the configured size.

        Returns:
            dict with counts and bytes_freed for logging/metrics.
        """
        log_dir = workspace_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "cleanup.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(file_handler)

        lock_path = workspace_root / "tmp" / ".cleanup.lock"
        lock_acquired = False

        bytes_freed = 0
        files_removed = 0
        skip_exts = {ext.lower() for ext in self.config.skip_exts}
        max_deletions = max(0, int(self.config.max_deletions))
        min_bytes = max(0, int(self.config.min_bytes))
        min_age_seconds = max(0, int(self.config.min_age_minutes) * 60)

        try:
            lock_acquired = self._acquire_lock(lock_path)
            if not lock_acquired:
                logger.warning("Workspace cleanup skipped: could not acquire lock at %s", lock_path)
                return {"files_removed": 0, "bytes_freed": 0}

            for pattern in self.config.paths:
                if max_deletions and files_removed >= max_deletions:
                    break
                targets = workspace_root.glob(pattern)
                for target in targets:
                    if max_deletions and files_removed >= max_deletions:
                        break
                    if not target.exists():
                        continue
                    if target.is_file():
                        removed, freed = self._maybe_remove_file(target, min_bytes, skip_exts, min_age_seconds)
                        if removed:
                            files_removed += 1
                            bytes_freed += freed
                        continue
                    if not target.is_dir():
                        continue
                    for file_path in self._iter_files(target):
                        if max_deletions and files_removed >= max_deletions:
                            break
                        removed, freed = self._maybe_remove_file(file_path, min_bytes, skip_exts, min_age_seconds)
                        if removed:
                            files_removed += 1
                            bytes_freed += freed
        finally:
            logger.removeHandler(file_handler)
            file_handler.close()
            if lock_acquired:
                try:
                    lock_path.unlink()
                except Exception:
                    pass

        if files_removed:
            logger.info(
                "Workspace cleanup removed %s files (%.2f GB freed)",
                files_removed,
                bytes_freed / (1024 ** 3),
            )
        else:
            logger.info("Workspace cleanup ran; no files removed")

        return {"files_removed": files_removed, "bytes_freed": bytes_freed}

    def _iter_files(self, directory: Path) -> Iterable[Path]:
        try:
            for root, _, files in os.walk(directory):
                for name in files:
                    yield Path(root) / name
        except Exception as exc:
            logger.warning("Failed to walk %s during cleanup: %s", directory, exc)

    def _maybe_remove_file(
        self,
        path: Path,
        min_bytes: int,
        skip_exts: Set[str],
        min_age_seconds: int,
    ) -> tuple[bool, int]:
        try:
            ext = path.suffix.lower()
            if ext in skip_exts:
                return False, 0
            size = path.stat().st_size
            if size < min_bytes:
                return False, 0
            if min_age_seconds:
                mtime = path.stat().st_mtime
                if (time.time() - mtime) < min_age_seconds:
                    return False, 0
            path.unlink()
            logger.info("Removed %s (%.2f MB)", path, size / (1024 ** 2))
            return True, size
        except FileNotFoundError:
            return False, 0
        except Exception as exc:
            logger.warning("Failed to remove %s: %s", path, exc)
            return False, 0

    def _acquire_lock(self, lock_path: Path) -> bool:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            return False
        except Exception:
            return False
