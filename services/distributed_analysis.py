# vidops/services/distributed_analysis.py

"""
Distributed analysis service for GenericWorker integration.

This service claims and processes distributed analysis tasks when a GenericWorker
encounters a job with job_type="analysis-distributed".

Architecture:
- Job stored in jobs table with job_type="analysis-distributed"
- Job config contains analysis_job_id (ref to analysis_tasks records)
- Service processes all analysis_tasks for that job
- Results aggregated into analysis_results when complete
- Main job marked as COMPLETED
"""

import logging
import time
import gc
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from dal.analysis_task_repository import AnalysisDatabase, AnalysisTaskRepository
from dal import AnalysisResultsRepository
from models import Job, JobStatus, AnalysisTask
from scripts.analysis.analyze_transcript import OllamaAnalyzer, AnalysisAggregator
from scripts.analysis.analysis_config import AnalysisConfig

logger = logging.getLogger(__name__)


class DistributedAnalysisService:
    """
    Bridge between GenericWorker (jobs table) and distributed analysis system (analysis_tasks).

    When GenericWorker claims a job with job_type="analysis-distributed":
    1. Extract analysis_job_id from config
    2. Claim and process all tasks for that job
    3. Execute each pass via OllamaAnalyzer
    4. Aggregate results when done
    5. Mark job complete
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
    ):
        self.job_repo = job_repo
        self.db_host = db_host
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.model_url = model_url
        self.model_name = model_name

        # DB connection will be opened per job
        self.db: Optional[AnalysisDatabase] = None
        self.task_repo: Optional[AnalysisTaskRepository] = None
        self.results_repo: Optional[AnalysisResultsRepository] = None
        self.analyzer: Optional[OllamaAnalyzer] = None

    def process_job(self, job: Job) -> None:
        """
        Main entry point for GenericWorker.
        Processes a distributed analysis job.
        """
        if not job.ytid:
            self.job_repo.update_status(
                job.job_id, JobStatus.FAILED, "Job missing ytid."
            )
            return

        try:
            # Extract analysis job ID from config
            analysis_job_id = job.config.get("analysis_job_id")
            if not analysis_job_id:
                raise ValueError(
                    f"Job {job.job_id} missing 'analysis_job_id' in config"
                )

            config_id = job.config.get("config_id")
            if not config_id:
                raise ValueError(
                    f"Job {job.job_id} missing 'config_id' in config"
                )

            model_url = job.config.get("model_url") or self.model_url
            model_name = job.config.get("model_name") or self.model_name

            # Initialize DB and repos
            self._init_db(model_url, model_name)

            # Mark job as RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING)

            # Process all tasks for this job
            self._process_job_tasks(job, analysis_job_id, config_id)

            # Aggregate results when all tasks done
            self._aggregate_and_complete(job, analysis_job_id)

            # Mark job complete
            result = {
                "analysis_job_id": analysis_job_id,
                "status": "completed",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=result)
            logger.info(
                "Distributed analysis job %s (analysis_job_id=%s) completed",
                job.job_id,
                analysis_job_id,
            )

        except Exception as exc:
            error_msg = f"Distributed analysis failed for job {job.job_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

        finally:
            self._cleanup_gpu()
            self._cleanup_db()

    def _init_db(self, model_url: str, model_name: str) -> None:
        """Initialize database and repos."""
        self.db = AnalysisDatabase(
            host=self.db_host,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password,
        )
        self.db.connect()
        self.task_repo = AnalysisTaskRepository(self.db)
        self.results_repo = AnalysisResultsRepository(self.db)
        self.analyzer = OllamaAnalyzer(
            model=model_name,
            base_url=model_url,
            options={},
            custom_request="",
            log_mode="quiet",
            category_suggestions=[],
            category_map={},
        )

    def _cleanup_gpu(self) -> None:
        """Release GPU/VRAM resources and clean up analyzer."""
        try:
            # Explicitly delete the analyzer to release resources
            self.analyzer = None

            # Force garbage collection
            gc.collect()

            # Clear CUDA cache if available
            try:
                import torch
                torch.cuda.empty_cache()
                logger.debug("CUDA cache cleared")
            except (ImportError, RuntimeError):
                pass  # CUDA not available or not in use
        except Exception as exc:
            logger.warning("Error cleaning up GPU resources: %s", exc)

    def _cleanup_db(self) -> None:
        """Close database connections."""
        try:
            if self.db:
                self.db.disconnect()
        except Exception as exc:
            logger.warning("Error closing database: %s", exc)

    def _process_job_tasks(self, job: Job, analysis_job_id: str, config_id: str) -> None:
        """
        Claim and process all tasks for this analysis job.
        """
        # Load config
        config = self._load_analysis_config(config_id)

        # Get all tasks for this job (should be in PENDING or CLAIMED state)
        tasks = self.task_repo.get_job_tasks(analysis_job_id)
        if not tasks:
            logger.warning(
                "No tasks found for analysis job %s", analysis_job_id
            )
            return

        logger.info(
            "Processing %d tasks for analysis job %s",
            len(tasks),
            analysis_job_id,
        )

        for task in tasks:
            # Skip if already completed/failed
            if task.status.value in ("completed", "failed"):
                logger.debug(
                    "Task %s already in %s state, skipping",
                    task.task_id,
                    task.status.value,
                )
                continue

            try:
                # Execute the pass
                result = self._execute_task(task, config)

                if result is not None:
                    result_meta = dict(result)
                    result_meta.setdefault(
                        "completed_at",
                        datetime.now(timezone.utc).isoformat(),
                    )
                    ok = self.task_repo.mark_completed(task.task_id, result_meta)
                    if ok:
                        logger.info("✓ Task %s completed", task.task_id)
                    else:
                        logger.error("Failed to mark task %s as completed", task.task_id)
                else:
                    error_msg = f"Pass execution returned None for pass_id={task.pass_id}"
                    ok = self.task_repo.mark_failed(task.task_id, error_msg)
                    if ok:
                        logger.error("Task %s failed: %s", task.task_id, error_msg)
                    else:
                        logger.error("Failed to mark task %s as failed", task.task_id)

            except Exception as exc:
                logger.error("Error processing task %s: %s", task.task_id, exc, exc_info=True)
                try:
                    self.task_repo.mark_failed(task.task_id, str(exc))
                except Exception:
                    pass

            # Avoid hammering Ollama
            time.sleep(0.1)

    def _execute_task(self, task: AnalysisTask, config: AnalysisConfig) -> Optional[Dict[str, Any]]:
        """
        Execute a single analysis task (simplified from DistributedAnalysisWorker).
        """
        start_time = datetime.now(timezone.utc)

        chunk_text = task.chunk_text or ""
        chunk_metadata = task.chunk_metadata or {}
        pass_id = task.pass_id

        logger.info(
            "Executing pass %s on task %s (chunk_id=%s)",
            pass_id,
            task.task_id,
            task.chunk_id,
        )

        try:
            if not chunk_text.strip():
                logger.warning(
                    "Task %s has empty chunk_text; marking as failed",
                    task.task_id,
                )
                return None

            if pass_id == "chunk_analysis":
                result = self.analyzer.analyze_chunk(chunk_text, int(task.chunk_id))
            else:
                # Placeholder for other passes
                result = {
                    "pass_id": pass_id,
                    "status": "completed",
                    "metadata": chunk_metadata,
                }

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            result.setdefault("duration_sec", duration)
            result.setdefault("pass_id", pass_id)
            return result

        except Exception as exc:
            logger.error(
                "Error executing pass %s for task %s: %s",
                pass_id,
                task.task_id,
                exc,
                exc_info=True,
            )
            return None

    def _aggregate_and_complete(self, job: Job, analysis_job_id: str) -> None:
        """
        Wait for all tasks to complete, then aggregate results.
        """
        # Wait for all tasks to complete (with timeout)
        max_wait = 3600  # 1 hour timeout
        start_time = time.time()
        check_interval = 5

        while time.time() - start_time < max_wait:
            if self.task_repo.is_job_complete(analysis_job_id):
                logger.info("All tasks complete for job %s; aggregating results", analysis_job_id)
                self._store_aggregated_results(job, analysis_job_id)
                return

            logger.debug(
                "Waiting for all tasks to complete (elapsed: %ds)",
                int(time.time() - start_time),
            )
            time.sleep(check_interval)

        # Timeout
        logger.warning(
            "Timeout waiting for all tasks to complete for job %s",
            analysis_job_id,
        )

    def _store_aggregated_results(self, job: Job, analysis_job_id: str) -> None:
        """
        Aggregate all task results and store in analysis_results.
        """
        try:
            config_id = job.config.get("config_id", "unknown")
            tasks = self.task_repo.get_job_tasks(analysis_job_id)

            if not tasks:
                logger.warning("No tasks found for aggregation")
                return

            # Count task statuses
            total_tasks = len(tasks)
            completed_tasks = sum(1 for t in tasks if t.status.value == "completed")
            failed_tasks = sum(1 for t in tasks if t.status.value == "failed")

            # Group results by pass
            results_by_pass: Dict[str, List[Dict[str, Any]]] = {}
            for t in tasks:
                if t.result_json is None:
                    continue
                pass_id = t.pass_id
                if pass_id not in results_by_pass:
                    results_by_pass[pass_id] = []
                results_by_pass[pass_id].append(
                    {
                        "chunk_id": t.chunk_id,
                        "result": t.result_json,
                    }
                )

            # Determine status: completed if all tasks done, processing otherwise
            status = "completed" if completed_tasks + failed_tasks >= total_tasks else "processing"

            # Store aggregated results using upsert_results
            self.results_repo.upsert_results(
                job_id=analysis_job_id,
                ytid=job.ytid,
                config_id=config_id,
                results_by_pass=results_by_pass,
                total_tasks=total_tasks,
                completed_tasks=completed_tasks,
                failed_tasks=failed_tasks,
                status=status,
            )
            logger.info(
                "Aggregated results for analysis job %s stored in analysis_results "
                "(%d total, %d completed, %d failed)",
                analysis_job_id,
                total_tasks,
                completed_tasks,
                failed_tasks,
            )

        except Exception as exc:
            logger.error(
                "Error storing aggregated results for job %s: %s",
                analysis_job_id,
                exc,
                exc_info=True,
            )

    def _load_analysis_config(self, config_id: str) -> AnalysisConfig:
        """Load analysis config from database."""
        try:
            row = self.db.get_analysis_config(config_id)
            if row:
                payload = row.get("config_json") if isinstance(row, dict) else None
                if payload:
                    return AnalysisConfig.model_validate(payload)
        except Exception as exc:
            logger.warning("Failed to load analysis config %s: %s", config_id, exc)

        # Fallback to minimal config
        return AnalysisConfig(
            id=config_id,
            name=f"default-{config_id}",
            passes=[
                {"id": "chunk_analysis", "phase": "chunk", "enabled": True},
            ],
        )
