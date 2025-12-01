# vidops/workers/subtitle.py

import logging
import os
import signal
import time
from datetime import timedelta
from typing import Optional, Sequence

from vidops.config import load_config
from vidops.dal import JobRepository, WorkerRepository
from vidops.models import Worker, WorkerStatus, JobStatus
from vidops.services import get_subtitle_service

logger = logging.getLogger(__name__)


class SubtitleWorker:
    """
    Worker dedicated to subtitle pipeline jobs (dl-subs + convert-captions).
    """

    def __init__(self):
        self.config = load_config()
        self.worker_repo = WorkerRepository()
        self.job_repo = JobRepository()
        self.subtitle_service = get_subtitle_service()
        self.worker_type = "subtitle"
        self.worker_id = f"{self.config.workers.machine_alias}-{self.worker_type}-{os.getpid()}"
        self.machine_alias = self.config.workers.machine_alias
        self.pid = os.getpid()
        self.hostname = os.uname().nodename
        self.job_types: Sequence[str] = ("dl_subs", "convert_captions")
        self.running = False
        self.current_job_id: Optional[str] = None

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    def _register_worker(self):
        worker_model = Worker(
            worker_id=self.worker_id,
            machine_alias=self.machine_alias,
            worker_type=self.worker_type,
            status=WorkerStatus.IDLE,
            pid=self.pid,
            hostname=self.hostname,
            capabilities=["yt_dlp"],
        )
        self.worker_repo.register(worker_model)
        logger.info("SubtitleWorker %s registered (%s)", self.worker_id, WorkerStatus.IDLE.value)

    def _heartbeat(self):
        self.worker_repo.heartbeat(self.worker_id)
        logger.info("Heartbeat for %s", self.worker_id)

    def _update_status(self, status: WorkerStatus, job_id: Optional[str] = None):
        self.worker_repo.update_status(self.worker_id, status, current_job_id=job_id, worker_type=self.worker_type)

    def _process_single_job(self) -> bool:
        job = self.job_repo.claim_next(
            worker=self._worker_model(),
            lease_duration=timedelta(seconds=self.config.workers.heartbeat_interval * 4),
            job_types=list(self.job_types),
        )
        if not job:
            return False

        self.current_job_id = job.job_id
        self._update_status(WorkerStatus.BUSY, job.job_id)
        try:
            self.subtitle_service.process_job(job)
        except Exception as exc:
            logger.error("Error processing job %s: %s", job.job_id, exc, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_message=str(exc))
        finally:
            self.current_job_id = None
            self._update_status(WorkerStatus.IDLE)
        return True

    def _worker_model(self) -> Worker:
        return Worker(
            worker_id=self.worker_id,
            machine_alias=self.machine_alias,
            worker_type=self.worker_type,
            pid=self.pid,
            hostname=self.hostname,
            capabilities=["yt_dlp"],
        )

    def run(self):
        logger.info("Starting SubtitleWorker %s", self.worker_id)
        self.running = True

        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        self._register_worker()

        processed = 0
        while self.running:
            try:
                if self._process_single_job():
                    processed += 1
                    if 0 < self.config.workers.max_jobs <= processed:
                        logger.info("Processed %s jobs (max=%s); stopping", processed, self.config.workers.max_jobs)
                        break
                else:
                    self._heartbeat()
                    time.sleep(self.config.workers.heartbeat_interval)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Unhandled error in subtitle worker loop: %s", exc, exc_info=True)
                self._update_status(WorkerStatus.ERRORED)
                break

        logger.info("SubtitleWorker %s shutting down", self.worker_id)
        self._update_status(WorkerStatus.STOPPING)
        time.sleep(1)

    def _handle_shutdown_signal(self, signum, frame):
        logger.warning("Received signal %s; shutting down", signum)
        self.running = False
        if self.current_job_id:
            logger.info("Releasing job %s before exit", self.current_job_id)
            self.job_repo.release(self.current_job_id)
