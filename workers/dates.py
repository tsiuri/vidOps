# vidops/workers/dates.py

import logging
import os
import platform
import signal
import time
from typing import Optional

from configuration import load_config
from dal import JobRepository, WorkerRepository
from models import Worker, WorkerStatus
from services import get_dates_service
from workers.heartbeat import WorkerHeartbeat

logger = logging.getLogger(__name__)


class DatesWorker:
    """
    Worker that processes `job_type='dates'` jobs using DatesService.
    """

    def __init__(self):
        self.config = load_config()
        self.worker_type = "dates"
        self.worker_id = f"{self.config.workers.machine_alias}-dates-{os.getpid()}"
        self.machine_alias = self.config.workers.machine_alias
        self.pid = os.getpid()
        self.hostname = platform.node()

        self.job_repo = JobRepository()
        self.worker_repo = WorkerRepository()
        self.service = get_dates_service()

        self.worker_obj = Worker(
            worker_id=self.worker_id,
            worker_type=self.worker_type,
            machine_alias=self.machine_alias,
            capabilities=["dates"],
            pid=self.pid,
            hostname=self.hostname,
        )

        self.poll_interval = self.config.workers.heartbeat_interval
        self.max_jobs = self.config.workers.max_jobs
        self._shutdown_requested = False
        self.current_job_id: Optional[str] = None
        self.heartbeat = WorkerHeartbeat(
            worker_repo=self.worker_repo,
            job_repo=self.job_repo,
            worker_id=self.worker_id,
            interval_seconds=self.config.workers.heartbeat_interval,
            logger=logger,
        )

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info("Dates worker %s received signal %s", self.worker_id, signum)
        self._shutdown_requested = True

    def run(self):
        logger.info("Dates worker %s starting on %s", self.worker_id, self.machine_alias)
        self._register_worker()
        self.heartbeat.start()

        processed = 0
        try:
            while not self._shutdown_requested:
                if self._process_single_job():
                    processed += 1
                    if self.max_jobs > 0 and processed >= self.max_jobs:
                        logger.info("Dates worker processed %d jobs; stopping per max_jobs", processed)
                        break
                    continue

                time.sleep(self.poll_interval)
        finally:
            self.heartbeat.stop()
            self._update_status(WorkerStatus.STOPPING)

    def _register_worker(self):
        self.worker_repo.register(self.worker_obj)
        self._update_status(WorkerStatus.IDLE)

    def _update_status(self, status: WorkerStatus, current_job_id: Optional[str] = None):
        self.worker_obj.status = status
        self.worker_obj.current_job_id = current_job_id
        self.worker_repo.update_status(self.worker_id, status, current_job_id)

    def _process_single_job(self) -> bool:
        job = self.job_repo.claim_next(self.worker_obj, job_types=[self.worker_type])
        if not job:
            return False

        logger.info("Processing dates job %s", job.job_id)
        self.current_job_id = job.job_id
        self.heartbeat.set_current_job(job.job_id)
        self._update_status(WorkerStatus.BUSY, job.job_id)
        try:
            self.service.process_job(job)
        finally:
            self.current_job_id = None
            self.heartbeat.set_current_job(None)
            self._update_status(WorkerStatus.IDLE, None)
        return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    worker = DatesWorker()
    worker.run()
