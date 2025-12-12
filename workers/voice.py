# vidops/workers/voice.py

import logging
import os
import signal
import time
from datetime import timedelta
from typing import Optional

from configuration import load_config
from dal import JobRepository, WorkerRepository
from models import Worker, WorkerStatus
from services import get_voice_service

logger = logging.getLogger(__name__)


class VoiceFilterWorker:
    """
    Worker that processes `job_type='voice'` jobs using VoiceFilterService.
    """

    def __init__(self):
        self.config = load_config()
        self.worker_type = "voice"
        self.worker_id = f"{self.config.workers.machine_alias}-voice-{os.getpid()}"
        self.machine_alias = self.config.workers.machine_alias
        self.pid = os.getpid()
        self.hostname = os.uname().nodename

        self.job_repo = JobRepository()
        self.worker_repo = WorkerRepository()
        self.service = get_voice_service()

        self.worker_obj = Worker(
            worker_id=self.worker_id,
            worker_type=self.worker_type,
            machine_alias=self.machine_alias,
            capabilities=["resemblyzer"],
            pid=self.pid,
            hostname=self.hostname,
        )

        self.poll_interval = max(1, self.config.workers.heartbeat_interval)
        self.max_jobs = self.config.workers.max_jobs
        self.heartbeat_interval = max(1, self.config.workers.heartbeat_interval)
        self._shutdown_requested = False
        self.last_heartbeat = 0.0

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info("Voice worker %s received signal %s", self.worker_id, signum)
        self._shutdown_requested = True

    def run(self):
        logger.info("Voice worker %s starting on %s", self.worker_id, self.machine_alias)
        self._register_worker()

        processed = 0
        try:
            while not self._shutdown_requested:
                if self._process_single_job():
                    processed += 1
                    if self.max_jobs > 0 and processed >= self.max_jobs:
                        logger.info("Voice worker processed %d jobs; stopping per max_jobs", processed)
                        break
                    continue

                time.sleep(self.poll_interval)
                self._heartbeat()
        finally:
            self._update_status(WorkerStatus.STOPPING)

    def _register_worker(self):
        self.worker_repo.register(self.worker_obj)
        self._update_status(WorkerStatus.IDLE)

    def _heartbeat(self):
        now = time.time()
        if now - self.last_heartbeat >= self.heartbeat_interval:
            self.worker_repo.heartbeat(self.worker_id)
            self.last_heartbeat = now

    def _update_status(self, status: WorkerStatus, current_job_id: Optional[str] = None):
        self.worker_obj.status = status
        self.worker_obj.current_job_id = current_job_id
        self.worker_repo.update_status(self.worker_id, status, current_job_id)

    def _process_single_job(self) -> bool:
        job = self.job_repo.claim_next(
            self.worker_obj,
            job_types=[self.worker_type],
            lease_duration=timedelta(seconds=self.heartbeat_interval * 4),
        )
        if not job:
            return False

        logger.info("Processing voice job %s (ytid=%s)", job.job_id, job.ytid)
        self._update_status(WorkerStatus.BUSY, job.job_id)
        try:
            self.service.process_job(job)
        finally:
            self._update_status(WorkerStatus.IDLE, None)
        return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    worker = VoiceFilterWorker()
    worker.run()
