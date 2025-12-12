# vidops/workers/diarization.py

import logging
import os
import time
import signal
from datetime import datetime, timedelta, timezone
from typing import Optional

from configuration import load_config
from models import Worker, WorkerStatus, JobStatus
from dal import WorkerRepository, JobRepository
from services import get_diarization_service
from monitoring.metrics import (
    jobs_claimed_total,
    jobs_completed_total,
    job_processing_duration_seconds,
    job_processing_duration_summary,
    generic_worker_uptime_seconds,
    generic_worker_memory_usage_bytes,
    generic_worker_cpu_usage_percent,
    worker_current_job_gauge,
    generic_errors_total,
)

logger = logging.getLogger(__name__)

class DiarizeWorker:
    """
    A worker process that claims and processes diarization jobs from the database.
    """
    
    def __init__(self, metrics_port: int = 0):
        self.config = load_config()
        self.worker_id = f"{self.config.workers.machine_alias}-diarization-{os.getpid()}"
        self.worker_type = "diarization"
        self.machine_alias = self.config.workers.machine_alias
        self.pid = os.getpid()
        self.hostname = os.uname().nodename
        self.worker_repo = WorkerRepository()
        self.job_repo = JobRepository()
        self.diarization_service = get_diarization_service()
        self.running = False
        self.current_job_id: Optional[str] = None
        self.heartbeat_interval = max(1, self.config.workers.heartbeat_interval)

        # Metrics initialization
        self.startup_time = datetime.now(timezone.utc)
        self.metrics_port = metrics_port
        self.metrics_server = None
        if metrics_port > 0:
            try:
                from monitoring.exporter import MetricsServer
                self.metrics_server = MetricsServer(port=metrics_port)
                self.metrics_server.start()
                logger.info("Metrics server started on port %d", metrics_port)
            except Exception as e:
                logger.warning("Failed to start metrics server: %s", e)

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
            capabilities=["audio_processing"] # Placeholder
        )
        self.worker_repo.register(worker_model)
        logger.info(f"DiarizeWorker '{self.worker_id}' registered as {WorkerStatus.IDLE.value}.")

    def _heartbeat(self):
        """Sends a heartbeat to the database."""
        self.worker_repo.heartbeat(self.worker_id)

    def _update_status(self, status: WorkerStatus, job_id: Optional[str] = None):
        """Updates the worker's status in the database."""
        self.worker_repo.update_status(self.worker_id, status, job_id)

    def _process_single_job(self):
        """Claims and processes a single job."""
        job = self.job_repo.claim_next(
            worker=self._get_self_worker_model(),
            lease_duration=timedelta(seconds=self.heartbeat_interval * 4),
            job_types=[self.worker_type],
        )

        if job:
            self.current_job_id = job.job_id
            job_start_time = time.time()

            # Record job claim
            jobs_claimed_total.labels(
                worker_id=self.worker_id,
                job_type=self.worker_type,
            ).inc()
            worker_current_job_gauge.labels(
                worker_id=self.worker_id,
                worker_type=self.worker_type,
            ).set(int(job.job_id) if job.job_id.isdigit() else hash(job.job_id) % 1000000)

            logger.info(f"DiarizeWorker '{self.worker_id}' claimed job '{job.job_id}' (YTID: {job.ytid}).")
            self._update_status(WorkerStatus.BUSY, job.job_id)
            try:
                self.diarization_service.process_job(job)

                # Record successful job completion
                job_duration = time.time() - job_start_time
                jobs_completed_total.labels(
                    worker_id=self.worker_id,
                    job_type=self.worker_type,
                    status="success",
                ).inc()
                job_processing_duration_seconds.labels(
                    worker_id=self.worker_id,
                    job_type=self.worker_type,
                ).observe(job_duration)
                job_processing_duration_summary.labels(
                    worker_id=self.worker_id,
                    job_type=self.worker_type,
                ).observe(job_duration)

                logger.info(f"Job '{job.job_id}' (YTID: {job.ytid}) completed successfully in {job_duration:.2f}s.")
            except Exception as e:
                # Record failed job completion
                job_duration = time.time() - job_start_time
                jobs_completed_total.labels(
                    worker_id=self.worker_id,
                    job_type=self.worker_type,
                    status="failed",
                ).inc()
                job_processing_duration_seconds.labels(
                    worker_id=self.worker_id,
                    job_type=self.worker_type,
                ).observe(job_duration)
                generic_errors_total.labels(
                    worker_id=self.worker_id,
                    worker_type=self.worker_type,
                    error_category=type(e).__name__,
                ).inc()

                logger.error(f"Error processing job '{job.job_id}': {e}", exc_info=True)
                # The service.process_job method already updates job status to FAILED on error
            finally:
                self.current_job_id = None
                worker_current_job_gauge.labels(
                    worker_id=self.worker_id,
                    worker_type=self.worker_type,
                ).set(0)
                self._update_status(WorkerStatus.IDLE)
        else:
            logger.debug(f"DiarizeWorker '{self.worker_id}' found no pending jobs.")
            self._update_status(WorkerStatus.IDLE)
        return bool(job) # Return True if a job was processed, False otherwise

    def _get_self_worker_model(self) -> Worker:
        """Constructs a Worker model for the current worker instance."""
        return Worker(
            worker_id=self.worker_id,
            machine_alias=self.machine_alias,
            worker_type=self.worker_type,
            pid=self.pid,
            hostname=self.hostname,
            capabilities=["audio_processing"] # Dummy capabilities for now
        )

    def _update_health_metrics(self) -> None:
        """Update worker health gauges periodically."""
        try:
            # Update uptime
            now = datetime.now(timezone.utc)
            uptime_seconds = (now - self.startup_time).total_seconds()
            generic_worker_uptime_seconds.labels(
                worker_id=self.worker_id,
                worker_type=self.worker_type,
            ).set(uptime_seconds)

            # Update memory usage (read from /proc/self/status)
            try:
                with open("/proc/self/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            # VmRSS is in kB, convert to bytes
                            memory_kb = int(line.split()[1])
                            memory_bytes = memory_kb * 1024
                            generic_worker_memory_usage_bytes.labels(
                                worker_id=self.worker_id,
                                worker_type=self.worker_type,
                            ).set(memory_bytes)
                            break
            except Exception:
                pass  # Non-Linux systems won't have /proc/self/status

            # Update CPU usage (simplified: read from /proc/self/stat)
            try:
                with open("/proc/self/stat", "r") as f:
                    stat_data = f.read().split()
                    # Rough CPU estimate: utime + stime in jiffies
                    utime = int(stat_data[13])
                    stime = int(stat_data[14])
                    total_time = (utime + stime) / 100.0  # Approximate percentage
                    cpu_percent = min(total_time, 100.0)  # Cap at 100%
                    generic_worker_cpu_usage_percent.labels(
                        worker_id=self.worker_id,
                        worker_type=self.worker_type,
                    ).set(cpu_percent)
            except Exception:
                pass

        except Exception as e:
            logger.debug("Error updating health metrics: %s", e)

    def run(self):
        """Main loop for the worker."""
        logger.info(f"Starting DiarizeWorker '{self.worker_id}'...")
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
                    # No job claimed, send heartbeat and update metrics
                    self._heartbeat()
                    self._update_health_metrics()
                    time.sleep(self.heartbeat_interval)
            except Exception as e:
                logger.error(f"Unhandled error in worker main loop: {e}", exc_info=True)
                self.running = False # Exit on unhandled errors
                self._update_status(WorkerStatus.ERRORED)

        logger.info(f"DiarizeWorker '{self.worker_id}' shutting down.")
        self._update_status(WorkerStatus.STOPPING)
        time.sleep(1) # Give some time for status update to commit

    def _handle_shutdown_signal(self, signum, frame):
        """Gracefully shuts down the worker on receiving a signal."""
        logger.warning(f"Received signal {signum}. Initiating graceful shutdown...")
        self.running = False
        if self.current_job_id:
            logger.info(f"Releasing current job '{self.current_job_id}' before shutdown.")
            self.job_repo.release(self.current_job_id)
