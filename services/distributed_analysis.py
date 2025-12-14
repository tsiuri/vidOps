# vidops/services/distributed_analysis.py

"""
Distributed analysis service for GenericWorker integration.

This is now a thin bridge that delegates to the full-featured AnalysisWorker
implementation so the dedicated worker CLI and the GenericWorker path share
one code path (DB store, spans, drills, etc.).
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from models import Job, JobStatus
from workers.analysis_distributed import AnalysisWorker

logger = logging.getLogger(__name__)


class DistributedAnalysisService:
    """
    Bridge between GenericWorker (jobs table) and the distributed analysis worker.

    When GenericWorker claims a job with job_type="analysis-distributed" (or the
    legacy "analysis" alias), we hydrate an AnalysisWorker instance and run the
    job's analysis_tasks through the same pass implementations used by the
    dedicated distributed worker.
    """

    def __init__(
        self,
        job_repo=None,
        db_host: str = "localhost",
        db_name: str = "transcripts",
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
        model_url: str = "http://localhost:11434",
        model_name: str = "llama3",
        capabilities: Optional[List[str]] = None,
        machine_alias: Optional[str] = None,
    ):
        self.job_repo = job_repo
        self.db_host = db_host
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.model_url = model_url
        self.model_name = model_name
        self.capabilities = capabilities or []
        self.machine_alias = machine_alias

    def _build_capabilities(self, model_name: str) -> List[str]:
        caps = list(self.capabilities)
        if not caps:
            caps = [model_name, "gpu_8gb"]
        elif model_name not in caps:
            caps.append(model_name)
        return caps

    def process_job(self, job: Job) -> None:
        """
        Main entry point for GenericWorker.
        Processes a distributed analysis job using the shared AnalysisWorker.
        """
        worker: Optional[AnalysisWorker] = None
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job missing ytid.")
            return

        try:
            analysis_job_id = job.config.get("analysis_job_id")
            if not analysis_job_id:
                raise ValueError(f"Job {job.job_id} missing 'analysis_job_id' in config")

            config_id = job.config.get("config_id")
            if not config_id:
                raise ValueError(f"Job {job.job_id} missing 'config_id' in config")

            model_url = job.config.get("model_url") or self.model_url
            model_name = job.config.get("model_name") or self.model_name
            capabilities = self._build_capabilities(model_name)
            machine_alias = self.machine_alias or job.claimed_by or self.db_host

            worker = AnalysisWorker(
                machine_alias=machine_alias,
                worker_type="analysis_bridge",
                model_url=model_url,
                model_name=model_name,
                capabilities=capabilities,
                db_host=self.db_host,
                db_name=self.db_name,
                db_user=self.db_user,
                db_password=self.db_password,
                lease_duration_minutes=60,
                metrics_port=0,  # disable metrics server for the bridge path
            )

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING)

            success = worker.process_analysis_job(
                analysis_job_id=analysis_job_id,
                force_job_level_passes=True,
            )

            result = {
                "analysis_job_id": analysis_job_id,
                "config_id": config_id,
                "status": "completed" if success else "incomplete",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            if success:
                self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=result)
                logger.info(
                    "Distributed analysis job %s (analysis_job_id=%s) completed",
                    job.job_id,
                    analysis_job_id,
                )
            else:
                self.job_repo.update_status(
                    job.job_id, JobStatus.FAILED, "Analysis tasks did not complete"
                )
                logger.error(
                    "Distributed analysis job %s (analysis_job_id=%s) ended incomplete",
                    job.job_id,
                    analysis_job_id,
                )

        except Exception as exc:
            error_msg = f"Distributed analysis failed for job {job.job_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

        finally:
            try:
                if worker:
                    worker.shutdown()
            except Exception:
                pass
