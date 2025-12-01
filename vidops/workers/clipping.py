# vidops/workers/clipping.py

import logging
import os
import time
from datetime import timedelta
import signal
from typing import Optional

from vidops.config import load_config
from vidops.models import Worker, WorkerStatus, JobStatus
from vidops.dal import WorkerRepository, JobRepository
from vidops.services import get_clipping_service # Import the clipping service factory

logger = logging.getLogger(__name__)

class ClippingWorker:
    """
    A worker process that claims and processes clipping jobs from the database.
    """
    
    def __init__(self):
        self.config = load_config()
        self.worker_id = f"{self.config.workers.machine_alias}-clipping-{os.getpid()}"
        self.worker_type = "clipping"
        self.machine_alias = self.config.workers.machine_alias
        self.pid = os.getpid()
        self.hostname = os.uname().nodename
        self.worker_repo = WorkerRepository() # Uses generic workers table
        self.job_repo = JobRepository() # Uses generic jobs table
        self.clipping_service = get_clipping_service()
        self.running = False
        self.current_job_id: Optional[str] = None
        
        # Configure logging
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    def _register_worker(self):
        """Registers or updates the worker's presence in the database."""
        worker_model = Worker(
            worker_id=self.worker_id,
            machine_alias=self.machine_alias,
            worker_type=self.worker_type,
            status=WorkerStatus.IDLE,
            pid=self.pid,
            hostname=self.hostname,
            capabilities=["ffmpeg"] # Placeholder
        )
        self.worker_repo.register(worker_model)
        logger.info(f"ClippingWorker '{self.worker_id}' registered as {WorkerStatus.IDLE.value}.")

    def _heartbeat(self):
        """Sends a heartbeat to the database."""
        self.worker_repo.heartbeat(self.worker_id)

    def _update_status(self, status: WorkerStatus, job_id: Optional[str] = None):
        """Updates the worker's status in the database."""
        self.worker_repo.update_status(self.worker_id, status, job_id)

    def _process_single_job(self):
        """Claims and processes a single job."""
        job = self.job_repo.claim_next(worker=self._get_self_worker_model(), lease_duration=timedelta(minutes=self.config.workers.heartbeat_interval * 2))
        
        if not job:
            logger.debug(f"ClippingWorker '{self.worker_id}' found no pending jobs.")
            self._update_status(WorkerStatus.IDLE)
            return False

        self.current_job_id = job.job_id
        logger.info(f"ClippingWorker '{self.worker_id}' claimed job '{job.job_id}' (YTID: {job.ytid}).")
        self._update_status(WorkerStatus.BUSY, job.job_id)
        try:
            self.clipping_service.process_job(job)
            logger.info(f"Job '{job.job_id}' (YTID: {job.ytid}) completed successfully.")
        except Exception as e:
            logger.error(f"Error processing job '{job.job_id}': {e}", exc_info=True)
            # The service.process_job method already updates job status to FAILED on error
        finally:
            self.current_job_id = None
            self._update_status(WorkerStatus.IDLE)
        return True

    def _get_self_worker_model(self) -> Worker:
        """Constructs a Worker model for the current worker instance."""
        return Worker(
            worker_id=self.worker_id,
            machine_alias=self.machine_alias,
            worker_type=self.worker_type,
            pid=self.pid,
            hostname=self.hostname,
            capabilities=["ffmpeg"] # Dummy capabilities for now
        )

    def run(self):
        """Main loop for the worker."""
        logger.info(f"Starting ClippingWorker '{self.worker_id}'...")
        self.running = True
        
        # Signal handling
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

        self._register_worker()

        processed_jobs_count = 0
        while self.running:
            try:
                if self._process_single_job():
                    processed_jobs_count += 1
                    # If max_jobs is set and reached, gracefully shut down
                    if self.config.workers.max_jobs > 0 and processed_jobs_count >= self.config.workers.max_jobs:
                        logger.info(f"Processed {processed_jobs_count} jobs, reaching max_jobs limit. Shutting down.")
                        self.running = False
                else:
                    # No job claimed, send heartbeat and sleep
                    self._heartbeat()
                    time.sleep(self.config.workers.heartbeat_interval)
            except Exception as e:
                logger.error(f"Unhandled error in worker main loop: {e}", exc_info=True)
                self.running = False # Exit on unhandled errors
                self._update_status(WorkerStatus.ERRORED)

        logger.info(f"ClippingWorker '{self.worker_id}' shutting down.")
        self._update_status(WorkerStatus.STOPPING)
        time.sleep(1) # Give some time for status update to commit

    def _handle_shutdown_signal(self, signum, frame):
        """Gracefully shuts down the worker on receiving a signal."""
        logger.warning(f"Received signal {signum}. Initiating graceful shutdown...")
        self.running = False
        if self.current_job_id:
            logger.info(f"Releasing current job '{self.current_job_id}' before shutdown.")
            self.job_repo.release(self.current_job_id)
