# vidops/workers/download.py

import logging
import time
import signal
import os
from typing import Optional

from vidops.dal import JobRepository, WorkerRepository
from vidops.models import Worker, WorkerStatus, JobStatus
from vidops.services import get_download_service
from vidops.config import load_config

logger = logging.getLogger(__name__)

class DownloadWorker:
    """
    Worker that processes download jobs from the queue.
    Claims jobs, downloads videos with yt-dlp, and reports results.
    """

    def __init__(self, worker_id: Optional[str] = None, machine_alias: Optional[str] = None):
        self.config = load_config()
        self.worker_id = worker_id or f"download_worker_{int(time.time())}"
        self.machine_alias = machine_alias or self.config.workers.machine_alias

        self.job_repo = JobRepository()
        self.worker_repo = WorkerRepository()
        self.service = get_download_service()
        self.current_job_id: Optional[str] = None

        self.worker_obj = Worker(
            worker_id=self.worker_id,
            worker_type="download",
            machine_alias=self.machine_alias,
            status=WorkerStatus.REGISTERING
        )

        self.poll_interval = 2  # seconds
        self.heartbeat_interval = max(1, self.config.workers.heartbeat_interval)
        self.last_heartbeat = 0
        self._shutdown_requested = False
        self.max_jobs = self.config.workers.max_jobs  # 0 = infinite

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        if self._shutdown_requested:
            # Second signal: fall back to default behavior
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return
        self._shutdown_requested = True
        logger.warning("Received signal %s, cancelling current job and exiting", signum)
        if self.current_job_id:
            try:
                self.job_repo.update_status(
                    self.current_job_id,
                    JobStatus.FAILED,
                    error_message=f"Cancelled by signal {signum}",
                )
                self._update_status(WorkerStatus.IDLE, None)
            except Exception as exc:
                logger.error("Failed to cancel job %s: %s", self.current_job_id, exc, exc_info=True)
        raise SystemExit(1)

    def run(self):
        """Main worker loop"""
        logger.info(f"Download worker {self.worker_id} starting on {self.machine_alias}")

        try:
            # Register worker
            self._register_worker()

            jobs_processed = 0

            # Main processing loop
            while not self._shutdown_requested:
                # Process one job if available
                if self._process_single_job():
                    jobs_processed += 1

                    # Check if we've hit max_jobs limit
                    if self.max_jobs > 0 and jobs_processed >= self.max_jobs:
                        logger.info(f"Reached max_jobs limit ({self.max_jobs}), shutting down")
                        break

                    # Continue immediately to next job
                    continue

                # No job available, sleep and send heartbeat
                time.sleep(self.poll_interval)
                self._heartbeat()

        except KeyboardInterrupt:
            logger.info("Worker interrupted by user")
        except Exception as e:
            logger.error(f"Worker error: {e}", exc_info=True)
            self._update_status(WorkerStatus.ERRORED)
        finally:
            self._shutdown()

    def _register_worker(self):
        """Register this worker in the database"""
        try:
            self.worker_repo.register(self.worker_obj)
            self._update_status(WorkerStatus.IDLE)
            logger.info(f"Worker {self.worker_id} registered successfully")
        except Exception as e:
            logger.error(f"Failed to register worker: {e}")
            raise

    def _process_single_job(self) -> bool:
        """
        Attempt to claim and process a single job.

        Returns:
            True if a job was processed, False otherwise
        """
        try:
            # Claim next available download job
            job = self.job_repo.claim_next(self.worker_obj, job_types=["download"])

            if not job:
                return False

            logger.info(f"Claimed job {job.job_id} (type: {job.job_type})")
            self._update_status(WorkerStatus.BUSY, job.job_id)
            self.current_job_id = job.job_id

            # Process the job
            self.service.process_job(job)

            self._update_status(WorkerStatus.IDLE, None)
            self.current_job_id = None
            return True

        except Exception as e:
            logger.error(f"Error processing job: {e}", exc_info=True)
            self._update_status(WorkerStatus.IDLE, None)
            self.current_job_id = None
            return False

    def _heartbeat(self):
        """Send heartbeat to database"""
        current_time = time.time()
        if current_time - self.last_heartbeat >= self.heartbeat_interval:
            try:
                self.worker_repo.heartbeat(self.worker_id)
                self.last_heartbeat = current_time
                logger.debug(f"Heartbeat sent for worker {self.worker_id}")
            except Exception as e:
                logger.warning(f"Failed to send heartbeat: {e}")

    def _update_status(self, status: WorkerStatus, current_job_id: Optional[str] = None):
        """Update worker status in database"""
        try:
            self.worker_obj.status = status
            self.worker_obj.current_job_id = current_job_id
            self.worker_repo.update_status(self.worker_id, status, current_job_id)
        except Exception as e:
            logger.warning(f"Failed to update worker status: {e}")

    def _shutdown(self):
        """Clean shutdown procedure"""
        logger.info(f"Worker {self.worker_id} shutting down")
        try:
            self._update_status(WorkerStatus.STOPPING)
            # Could add cleanup logic here
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create and run worker
    worker = DownloadWorker()
    worker.run()
