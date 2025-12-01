# vidops/workers/general.py

import logging
import os
import signal
import time
from datetime import timedelta
from typing import Callable, Dict, Optional

from vidops.config import load_config
from vidops.dal import JobRepository, WorkerRepository
from vidops.models import JobStatus, Worker, WorkerStatus
from vidops.services import (
    get_analysis_service,
    get_clipping_service,
    get_diarization_service,
    get_download_service,
    get_stitching_service,
    get_subtitle_service,
    get_transcription_service,
    get_voice_service,
)

logger = logging.getLogger(__name__)


class GenericWorker:
    """
    Generic worker that stays untyped until a job is claimed.
    After each job it reverts to the starter "general" state so the next loop
    can service any job type in the queue.
    """

    def __init__(self):
        self.config = load_config()
        self.worker_repo = WorkerRepository()
        self.job_repo = JobRepository()
        self.worker_id = f"{self.config.workers.machine_alias}-general-{os.getpid()}"
        self.machine_alias = self.config.workers.machine_alias
        self.current_job_id: Optional[str] = None
        self.base_worker_type = "general"
        self._shutdown_requested = False
        self.max_jobs = self.config.workers.max_jobs
        self.worker_obj = Worker(
            worker_id=self.worker_id,
            worker_type=self.base_worker_type,
            machine_alias=self.machine_alias,
            status=WorkerStatus.REGISTERING,
            pid=os.getpid(),
            hostname=os.uname().nodename,
        )
        self.service_factories: Dict[str, Callable] = {
            "download": get_download_service,
            "transcription": get_transcription_service,
            "clipping": get_clipping_service,
            "analysis": get_analysis_service,
            "diarization": get_diarization_service,
            "stitching": get_stitching_service,
            "subtitle_download": get_subtitle_service,
            "voice": get_voice_service,
        }
        self._services: Dict[str, object] = {}

        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

    def run(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        logger.info("Generic worker %s starting on %s", self.worker_id, self.machine_alias)
        self._register()
        jobs_processed = 0

        while not self._shutdown_requested:
            try:
                processed = self._process_single_job()
                if processed:
                    jobs_processed += 1
                    if 0 < self.max_jobs <= jobs_processed:
                        logger.info(
                            "Processed %s jobs (max_jobs=%s); shutting down",
                            jobs_processed,
                            self.max_jobs,
                        )
                        break
                    continue

                # No job claimed; heartbeat + sleep
                self._heartbeat()
                time.sleep(self.config.workers.heartbeat_interval)
            except Exception as exc:
                logger.error("Unhandled error in worker loop: %s", exc, exc_info=True)
                self._update_state(WorkerStatus.ERRORED)
                break

        logger.info("Generic worker %s shutting down", self.worker_id)
        self._update_state(WorkerStatus.STOPPING)

    # ------------------------------------------------------------------ internals
    def _register(self):
        self.worker_repo.register(self.worker_obj)
        logger.info("Registered worker %s (%s); waiting for jobs", self.worker_id, self.machine_alias)
        self._update_state(WorkerStatus.IDLE, worker_type=self.base_worker_type)

    def _heartbeat(self):
        self.worker_repo.heartbeat(self.worker_id)
        logger.info("Heartbeat for worker %s", self.worker_id)

    def _update_state(
        self,
        status: WorkerStatus,
        job_id: Optional[str] = None,
        worker_type: Optional[str] = None,
    ):
        wt = worker_type or self.worker_obj.worker_type
        self.worker_obj.status = status
        self.worker_obj.current_job_id = job_id
        self.worker_obj.worker_type = wt
        self.worker_repo.update_status(
            self.worker_id,
            status,
            current_job_id=job_id,
            worker_type=wt,
        )

    def _process_single_job(self) -> bool:
        job = self.job_repo.claim_next(
            worker=self.worker_obj,
            lease_duration=timedelta(seconds=self.config.workers.heartbeat_interval * 4),
        )
        if not job:
            return False

        self.current_job_id = job.job_id
        claimed_type = job.job_type or self.base_worker_type
        self._update_state(WorkerStatus.BUSY, job.job_id, worker_type=claimed_type)

        try:
            service = self._get_service_for_job(claimed_type)
        except KeyError:
            error_msg = f"No service registered for job_type '{claimed_type}'"
            logger.error(error_msg)
            self.job_repo.update_status(
                job.job_id,
                JobStatus.FAILED,
                error_message=error_msg,
            )
        else:
            try:
                service.process_job(job)
            except Exception as exc:
                logger.error("Error processing job %s: %s", job.job_id, exc, exc_info=True)
                # individual services should update job status, but ensure it's failed
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message=str(exc),
                )

        finally:
            self.current_job_id = None
            self._update_state(
                WorkerStatus.IDLE,
                job_id=None,
                worker_type=self.base_worker_type,
            )
            logger.info("Job %s completed and worker %s returned to IDLE", job.job_id, self.worker_id)
        return True

    def _get_service_for_job(self, job_type: str):
        if job_type not in self.service_factories:
            raise KeyError(job_type)
        if job_type not in self._services:
            self._services[job_type] = self.service_factories[job_type]()
        return self._services[job_type]

    def _handle_shutdown_signal(self, signum, frame):
        logger.warning("Received signal %s, initiating shutdown", signum)
        self._shutdown_requested = True
        if self.current_job_id:
            logger.info("Releasing job %s before exit", self.current_job_id)
            self.job_repo.release(self.current_job_id)
