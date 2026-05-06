#!/usr/bin/env python3
"""
Analysis worker process for distributed transcript analysis.

This worker:
- Connects to the transcripts database
- Claims tasks from analysis_tasks via AnalysisTaskRepository
- Runs the appropriate LLM "pass" on the chunk text
- Writes results back to analysis_tasks
- Aggregates results into analysis_results when jobs complete
"""

from __future__ import annotations

import os
import platform
import time
import sys
import signal
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
import logging

try:
    import psutil
except Exception:
    psutil = None

from configuration import load_config
from dal.analysis_task_repository import AnalysisDatabase, AnalysisTaskRepository
from models import AnalysisTask, Worker, WorkerStatus
from dal import WorkerRepository
from workers.heartbeat import WorkerHeartbeat
from services.analysis_engine import AnalysisEngine
from web.monitoring.exporter import start_metrics_server, stop_metrics_server
from web.monitoring.metrics import (
    tasks_claimed_total,
    tasks_completed_total,
    tasks_failed_total,
    task_processing_duration_seconds,
    task_processing_duration_summary,
    worker_uptime_seconds,
    worker_current_task_gauge,
    worker_memory_usage_bytes,
    worker_cpu_usage_percent,
    worker_vram_gb_gauge,
    pending_tasks_gauge,
    claimed_but_not_completed_gauge,
    jobs_aggregated_total,
    job_aggregation_duration_seconds,
    errors_total,
    last_error_timestamp,
)


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("AnalysisWorker")


class AnalysisWorker:
    """
    A worker process that claims analysis tasks and executes them.
    """

    def __init__(
        self,
        machine_alias: str,
        worker_type: str,
        model_url: str,
        model_name: str,
        available_vram_gb: float = 0.0,
        model_profile_id: Optional[int] = None,
        db_host: str = "localhost",
        db_name: str = "transcripts",
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
        lease_duration_minutes: int = 60,
        metrics_port: int = 8888,
        debug: bool = False,
    ) -> None:
        if debug:
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug logging enabled")

        self.machine_alias = machine_alias
        self.worker_type = worker_type
        self.model_url = model_url
        self.model_name = model_name
        self.available_vram_gb = max(float(available_vram_gb or 0), 0.0)
        self.model_profile_id = model_profile_id
        self.lease_duration = timedelta(minutes=lease_duration_minutes)
        self.metrics_port = metrics_port

        self.worker_id = f"{machine_alias}:{worker_type}:{model_name}:{os.getpid()}"
        self.config = load_config()
        self.is_bridge_mode = worker_type == "analysis_bridge"

        # Only create worker registration if not running as bridge
        # (Bridge mode = running within GenericWorker, which handles worker registration)
        if not self.is_bridge_mode:
            self.worker_repo = WorkerRepository()
            self.worker_obj = Worker(
                worker_id=self.worker_id,
                machine_alias=self.machine_alias,
                worker_type=self.worker_type,
                status=WorkerStatus.REGISTERING,
                vram_gb=self.available_vram_gb,
                pid=os.getpid(),
                hostname=platform.node(),
            )
            self.heartbeat = WorkerHeartbeat(
                worker_repo=self.worker_repo,
                worker_id=self.worker_id,
                interval_seconds=self.config.workers.heartbeat_interval,
                job_repo=None,
                logger=logger,
            )
        else:
            self.worker_repo = None
            self.worker_obj = None
            self.heartbeat = None

        # DB + repos
        self.db = AnalysisDatabase(
            host=db_host,
            dbname=db_name,
            user=db_user,
            password=db_password,
        )
        self.db.connect()
        self.task_repo = AnalysisTaskRepository(self.db)

        # Resolve model profile options if available (passed to engine)
        profile_options: Dict[str, Any] = {}
        if self.model_profile_id:
            profile = self.db.get_analysis_model_profile(self.model_profile_id)
            if profile:
                profile_model = profile.get("model_name")
                if profile_model and profile_model != self.model_name:
                    logger.warning(
                        "Model profile %s uses model '%s' but worker is configured for '%s'",
                        self.model_profile_id,
                        profile_model,
                        self.model_name,
                    )
                opts = profile.get("options") or {}
                if isinstance(opts, dict):
                    profile_options = opts

        # AnalysisEngine owns the OllamaAnalyzer + HotTargetRunner + per-job context cache
        # and now hosts every per-pass implementation (chunk-level + job-level).
        self.engine = AnalysisEngine(
            model_name=self.model_name,
            model_url=self.model_url,
            model_profile_id=self.model_profile_id,
            ollama_options=profile_options,
            machine_alias=self.machine_alias,
        )

        try:
            logger.info(
                "Checking connection to Ollama server at %s...",
                self.engine.analyzer.base_url,
            )
            self.engine.analyzer.check_connection()
            logger.info("Ollama connection successful.")
        except ConnectionError as e:
            logger.fatal("Ollama connection check failed: %s", e)
            sys.exit(1)

        self.should_exit: bool = False
        self.current_task: Optional[AnalysisTask] = None
        self.metrics_server = None
        self.startup_time = datetime.now(timezone.utc)
        self._psutil_process = None
        if psutil:
            try:
                self._psutil_process = psutil.Process(os.getpid())
                self._psutil_process.cpu_percent(interval=None)
            except Exception:
                self._psutil_process = None

        # Start metrics server if port > 0
        if self.metrics_port > 0:
            try:
                self.metrics_server = start_metrics_server(host="0.0.0.0", port=self.metrics_port)
            except Exception as e:
                logger.warning("Failed to start metrics server on port %d: %s", self.metrics_port, e)

        # Initialize uptime tracking
        self._last_uptime_update = datetime.now(timezone.utc)
        worker_uptime_seconds.labels(worker_id=self.worker_id).set(0)
        worker_vram_gb_gauge.labels(worker_id=self.worker_id).set(self.available_vram_gb)

        self._register_worker()
        if self.heartbeat:
            self.heartbeat.start()

    def _register_worker(self) -> None:
        if not self.is_bridge_mode:
            self.worker_repo.register(self.worker_obj)
            self._update_worker_status(WorkerStatus.IDLE)

    def _update_worker_status(self, status: WorkerStatus, current_job_id: Optional[str] = None) -> None:
        if not self.is_bridge_mode:
            self.worker_obj.status = status
            self.worker_obj.current_job_id = current_job_id
            
            # Sanitize job_id: The 'workers' table has a foreign key to 'jobs'.
            # Analysis IDs (legacy format with colons) are NOT in 'jobs', so using them
            # causes a crash. Only pass job_ids that look like generic jobs (no colons).
            safe_job_id = current_job_id
            if safe_job_id and ":" in safe_job_id:
                safe_job_id = None

            self.worker_repo.update_status(
                self.worker_id,
                status,
                current_job_id=safe_job_id,
                worker_type=self.worker_type,
            )

    def shutdown(self) -> None:
        """Clean up resources when used outside the long-running loop."""
        if self.heartbeat:
            self.heartbeat.stop()
        self._update_worker_status(WorkerStatus.STOPPING)
        if self.metrics_server:
            try:
                stop_metrics_server()
            except Exception:
                pass
        try:
            self.engine.shutdown()
        except Exception as exc:
            logger.warning("Engine shutdown error: %s", exc)
        try:
            self.db.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        """Register signal handlers with platform guards."""
        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            try:
                signal.signal(sig, self._handle_signal)
            except (ValueError, OSError, AttributeError) as exc:
                logger.debug("Skipping signal handler for %s: %s", sig, exc)

    def run_forever(self) -> None:
        """
        Main worker loop. Runs until interrupted.
        """
        self._register_signal_handlers()

        logger.info("Worker %s starting main loop...", self.worker_id)

        try:
            while not self.should_exit:
                try:
                    task = self.task_repo.claim_next(
                        worker_id=self.worker_id,
                        worker_vram_gb=self.available_vram_gb,
                        lease_duration=self.lease_duration,
                    )

                    if not task:
                        # No tasks available – back off briefly
                        self._update_worker_status(WorkerStatus.IDLE)
                        # Update all gauges periodically
                        self._update_health_metrics()
                        time.sleep(5)
                        continue

                    self.current_task = task
                    if self.heartbeat:
                        self.heartbeat.set_current_job(task.job_id)
                    self._update_worker_status(WorkerStatus.BUSY, task.job_id)

                    # Record task claim in metrics
                    tasks_claimed_total.labels(
                        worker_id=self.worker_id,
                        pass_id=task.pass_id,
                    ).inc()
                    worker_current_task_gauge.labels(worker_id=self.worker_id).set(task.task_id)

                    logger.info(
                        "Claimed task %s for job %s (ytid=%s, pass=%s, chunk_id=%s)",
                        task.task_id,
                        task.job_id,
                        task.ytid,
                        task.pass_id,
                        task.chunk_id,
                    )

                    task_result = self.engine.run_task(task, worker_id=self.worker_id)

                    if task_result.status == "ok" and task_result.payload is not None:
                        # Attach worker metadata
                        result_meta = dict(task_result.payload)
                        result_meta.setdefault("worker_id", self.worker_id)
                        result_meta.setdefault(
                            "completed_at",
                            datetime.now(timezone.utc).isoformat(),
                        )
                        ok = self.task_repo.mark_completed(task.task_id, result_meta)
                        if ok:
                            # Record successful task completion
                            duration = task_result.duration_s
                            tasks_completed_total.labels(
                                worker_id=self.worker_id,
                                pass_id=task_result.pass_id,
                                status="ok",
                            ).inc()
                            task_processing_duration_seconds.labels(
                                worker_id=self.worker_id,
                                pass_id=task_result.pass_id,
                            ).observe(duration)
                            task_processing_duration_summary.labels(
                                worker_id=self.worker_id,
                                pass_id=task_result.pass_id,
                            ).observe(duration)
                            logger.info("✓ Task %s completed", task.task_id)
                        else:
                            logger.error("Failed to mark task %s as completed", task.task_id)
                    else:
                        error_msg = (
                            task_result.error_message
                            or f"Pass execution failed for pass_id={task.pass_id}"
                        )
                        error_category = task_result.error_category or "pass_execution_failed"
                        ok = self.task_repo.mark_failed(task.task_id, error_msg)
                        if ok:
                            # Record failed task
                            tasks_failed_total.labels(
                                worker_id=self.worker_id,
                                pass_id=task_result.pass_id,
                                error_type=error_category,
                            ).inc()
                            errors_total.labels(
                                worker_id=self.worker_id,
                                error_category=error_category,
                            ).inc()
                            last_error_timestamp.labels(
                                worker_id=self.worker_id,
                                error_category=error_category,
                            ).set(datetime.now(timezone.utc).timestamp())
                            task_processing_duration_seconds.labels(
                                worker_id=self.worker_id,
                                pass_id=task_result.pass_id,
                            ).observe(task_result.duration_s)
                            logger.error("Task %s failed: %s", task.task_id, error_msg)
                        else:
                            logger.error("Failed to mark task %s as failed", task.task_id)

                    # After finishing the task, check if we can aggregate the job
                    try:
                        if self.task_repo.is_job_complete(task.job_id):
                            logger.info("Job %s appears complete – aggregating results", task.job_id)
                            agg_start = datetime.now(timezone.utc)
                            try:
                                self.engine.aggregate_results(task.job_id)
                                agg_status = "success"
                            except Exception as agg_exc:
                                logger.error(
                                    "Aggregation failed for job %s: %s", task.job_id, agg_exc
                                )
                                agg_status = "failed"
                            agg_duration = (datetime.now(timezone.utc) - agg_start).total_seconds()
                            jobs_aggregated_total.labels(
                                worker_id=self.worker_id,
                                status=agg_status,
                            ).inc()
                            job_aggregation_duration_seconds.labels(worker_id=self.worker_id).observe(agg_duration)
                    except Exception as exc:
                        logger.error("Error checking/aggregating job %s: %s", task.job_id, exc)
                        try:
                            if self.db.conn:
                                self.db.conn.rollback()
                        except Exception:
                            pass
                        errors_total.labels(
                            worker_id=self.worker_id,
                            error_category="job_aggregation_error",
                        ).inc()
                        last_error_timestamp.labels(
                            worker_id=self.worker_id,
                            error_category="job_aggregation_error",
                        ).set(datetime.now(timezone.utc).timestamp())

                    self.current_task = None
                    if self.heartbeat:
                        self.heartbeat.set_current_job(None)
                    self._update_worker_status(WorkerStatus.IDLE)
                    worker_current_task_gauge.labels(worker_id=self.worker_id).set(0)

                except ConnectionError as e:
                    logger.fatal("Ollama connection error, terminating worker: %s", e)
                    self.should_exit = True
                    if self.current_task:
                        self.task_repo.mark_failed(self.current_task.task_id, f"Ollama connection error: {e}")
                except KeyboardInterrupt:
                    logger.info("Received KeyboardInterrupt, exiting loop...")
                    break
                except Exception as exc:
                    logger.error("Unexpected error in worker loop: %s", exc)
                    import traceback
                    traceback.print_exc()

                    if self.current_task:
                        self.task_repo.mark_failed(self.current_task.task_id, str(exc))

                    try:
                        if self.db.conn:
                            self.db.conn.rollback()
                    except Exception:
                        pass
                    time.sleep(5)
        finally:
            logger.info("Worker %s shutting down", self.worker_id)
            if self.heartbeat:
                self.heartbeat.stop()
            self._update_worker_status(WorkerStatus.STOPPING)
            if self.metrics_server:
                try:
                    stop_metrics_server()
                    logger.info("Metrics server stopped")
                except Exception as exc:
                    logger.warning("Error stopping metrics server: %s", exc)
            try:
                self.db.disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Health Metrics
    # ------------------------------------------------------------------

    def _update_health_metrics(self) -> None:
        """Update worker health gauges periodically."""
        try:
            # Update uptime
            now = datetime.now(timezone.utc)
            uptime_seconds = (now - self.startup_time).total_seconds()
            worker_uptime_seconds.labels(worker_id=self.worker_id).set(uptime_seconds)

            process = self._psutil_process
            if psutil:
                try:
                    process = process or psutil.Process(os.getpid())
                    memory_bytes = process.memory_info().rss
                    worker_memory_usage_bytes.labels(worker_id=self.worker_id).set(memory_bytes)
                    cpu_percent = process.cpu_percent(interval=None)
                    worker_cpu_usage_percent.labels(worker_id=self.worker_id).set(cpu_percent)
                except Exception:
                    pass
            else:
                # Fallback for environments without psutil
                try:
                    with open("/proc/self/status", "r") as f:
                        for line in f:
                            if line.startswith("VmRSS:"):
                                memory_kb = int(line.split()[1])
                                memory_bytes = memory_kb * 1024
                                worker_memory_usage_bytes.labels(worker_id=self.worker_id).set(memory_bytes)
                                break
                except Exception:
                    pass
                try:
                    with open("/proc/self/stat", "r") as f:
                        stat_data = f.read().split()
                        utime = int(stat_data[13])
                        stime = int(stat_data[14])
                        total_time = (utime + stime) / 100.0
                        cpu_percent = min(total_time, 100.0)
                        worker_cpu_usage_percent.labels(worker_id=self.worker_id).set(cpu_percent)
                except Exception:
                    pass

            # Update pending tasks count (query repo if available)
            try:
                if hasattr(self.task_repo, 'get_pending_count'):
                    pending_count = self.task_repo.get_pending_count()
                    pending_tasks_gauge.labels(worker_id=self.worker_id).set(pending_count)
            except Exception:
                pass

            # Update claimed but not completed
            try:
                if hasattr(self.task_repo, 'get_claimed_not_completed_count'):
                    claimed_count = self.task_repo.get_claimed_not_completed_count()
                    claimed_but_not_completed_gauge.labels(worker_id=self.worker_id).set(claimed_count)
            except Exception:
                pass

        except Exception as e:
            logger.debug("Error updating health metrics: %s", e)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _handle_signal(self, signum, frame) -> None:
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        logger.info("Received signal %s", signum)
        if self.current_task:
            logger.info(
                "Currently processing task %s; will finish current task before exit",
                self.current_task.task_id,
            )
        self.should_exit = True


def main(argv: Optional[List[str]] = None) -> int:
    """
    Minimal CLI entry point for running a worker via the command line.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Run an analysis worker that consumes tasks from analysis_tasks.")
    parser.add_argument("--machine-alias", required=False, default=platform.node())
    parser.add_argument("--worker-type", required=False, default="analysis_gpu")
    parser.add_argument("--model-url", required=False, default="http://localhost:11434")
    parser.add_argument("--model-name", required=False, default="qwen2.5:7b-instruct")
    parser.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        help="Capability tag for this worker (repeatable)",
    )
    parser.add_argument(
        "--vram-gb",
        type=float,
        default=0.0,
        help="Available VRAM in GB for scheduling (default: 0).",
    )
    parser.add_argument(
        "--model-profile-id",
        type=int,
        default=None,
        help="Analysis model profile id (used for task matching).",
    )
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-name", default="transcripts")
    parser.add_argument("--db-user", default=None)
    parser.add_argument("--db-password", default=None)
    parser.add_argument("--lease-minutes", type=int, default=60)
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args(argv)

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    worker = AnalysisWorker(
        machine_alias=args.machine_alias,
        worker_type=args.worker_type,
        model_url=args.model_url,
        model_name=args.model_name,
        available_vram_gb=args.vram_gb,
        model_profile_id=args.model_profile_id,
        db_host=args.db_host,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
        lease_duration_minutes=args.lease_minutes,
        debug=args.debug,
    )
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
