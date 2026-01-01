# vidops/workers/heartbeat.py

from __future__ import annotations

import logging
import threading
from typing import Optional

from dal import JobRepository, WorkerRepository


class WorkerHeartbeat:
    """
    Background heartbeat for workers.

    Keeps worker last_heartbeat fresh and optionally touches the current job
    so long-running jobs don't look stale.
    """

    def __init__(
        self,
        worker_repo: WorkerRepository,
        worker_id: str,
        interval_seconds: int,
        job_repo: Optional[JobRepository] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.worker_repo = worker_repo
        self.worker_id = worker_id
        self.interval_seconds = max(1, int(interval_seconds))
        self.job_repo = job_repo
        self.logger = logger or logging.getLogger(__name__)

        self._current_job_id: Optional[str] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def set_current_job(self, job_id: Optional[str]) -> None:
        with self._lock:
            self._current_job_id = job_id

    def _get_current_job(self) -> Optional[str]:
        with self._lock:
            return self._current_job_id

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self._tick()

    def _tick(self) -> None:
        try:
            self.worker_repo.heartbeat(self.worker_id)
        except Exception as exc:
            self.logger.warning(
                "Failed to send heartbeat for worker %s: %s",
                self.worker_id,
                exc,
            )

        job_id = self._get_current_job()
        if not job_id or not self.job_repo:
            return

        try:
            self.job_repo.touch(job_id)
        except Exception as exc:
            self.logger.warning(
                "Failed to touch job %s for worker %s: %s",
                job_id,
                self.worker_id,
                exc,
            )
