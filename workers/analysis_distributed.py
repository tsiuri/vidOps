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
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple, Set
import logging
from pathlib import Path
try:
    import torch  # Optional, for GPU cleanup
except Exception:
    torch = None

try:
    import psutil
except Exception:
    psutil = None

from configuration import load_config
from dal.analysis_task_repository import AnalysisDatabase, AnalysisTaskRepository
from models import AnalysisTask, Worker, WorkerStatus
from dal import AnalysisResultsRepository, WorkerRepository
from scripts.analysis.analyze_transcript import AnalysisAggregator
from workers.heartbeat import WorkerHeartbeat
from scripts.analysis.analysis_config import AnalysisConfig, Drill
from scripts.analysis.drills import DrillExecutor
from services.analysis_engine import AnalysisEngine, JobContext, TaskResult
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
        self.results_repo = AnalysisResultsRepository(self.db)

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

        # AnalysisEngine owns the OllamaAnalyzer + HotTargetRunner + per-job context cache.
        self.engine = AnalysisEngine(
            model_name=self.model_name,
            model_url=self.model_url,
            model_profile_id=self.model_profile_id,
            ollama_options=profile_options,
        )
        # Re-expose analyzer / hot_target_runner / job_contexts for the worker-level
        # job-level passes that still live here (Task 3 will move them).
        self.analyzer = self.engine.analyzer
        self.hot_target_runner = self.engine.hot_target_runner
        self.job_contexts = self.engine.job_contexts

        try:
            logger.info("Checking connection to Ollama server at %s...", self.analyzer.base_url)
            self.analyzer.check_connection()
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

    # ------------------------------------------------------------------
    # Pass dispatch helpers
    # ------------------------------------------------------------------

    _JOB_LEVEL_PASSES = {
        "aggregate_results",
        "hot_targets",
        "drills",
        "db_store",
        "local_json",
        "markdown_report",
    }

    def _dispatch_pass(self, task: AnalysisTask) -> "TaskResult":
        """
        Bridge between worker and engine pass implementations.

        Chunk-level passes go through ``engine.run_task``. Job-level passes still
        live on the worker until Task 3; for those we call the worker's
        ``_handle_job_level_pass`` directly. This collapses in Task 5 once both
        layers are unified. The return type mirrors ``TaskResult`` so callers
        can branch uniformly on ``status``.
        """
        if task.pass_id not in self._JOB_LEVEL_PASSES:
            return self.engine.run_task(task, worker_id=self.worker_id)

        start = time.monotonic()
        start_time = datetime.now(timezone.utc)
        try:
            context = self.engine._get_job_context(task)
            chunk_text = task.chunk_text or ""
            chunk_metadata = task.chunk_metadata or {}
            payload = self._handle_job_level_pass(
                task, task.pass_id, chunk_text, chunk_metadata, context
            )
            duration_s = time.monotonic() - start
            duration_dt = (datetime.now(timezone.utc) - start_time).total_seconds()
            if payload is None:
                return TaskResult(
                    status="failed",
                    duration_s=duration_s,
                    pass_id=task.pass_id,
                    job_id=task.job_id,
                    error_category="internal",
                    error_message=f"Pass execution returned None for pass_id={task.pass_id}",
                )
            payload = dict(payload)
            payload.setdefault("duration_sec", duration_dt)
            payload.setdefault("pass_id", task.pass_id)
            return TaskResult(
                status="ok",
                duration_s=duration_s,
                pass_id=task.pass_id,
                job_id=task.job_id,
                payload=payload,
            )
        except Exception as exc:
            logger.exception(
                "Error executing job-level pass %s for task %s",
                task.pass_id,
                task.task_id,
            )
            return TaskResult(
                status="failed",
                duration_s=time.monotonic() - start,
                pass_id=task.pass_id,
                job_id=task.job_id,
                error_category="internal",
                error_message=str(exc)[:500],
            )

    # ------------------------------------------------------------------
    # Single-job execution (for GenericWorker bridge)
    # ------------------------------------------------------------------

    def process_analysis_job(self, analysis_job_id: str, force_job_level_passes: bool = False) -> bool:
        """
        Run all tasks for a specific analysis_job_id once (no worker loop).

        Used by GenericWorker/DistributedAnalysisService so both paths share the
        same pass implementations and DB store behavior.
        """
        tasks = self.task_repo.get_job_tasks(analysis_job_id)
        if not tasks:
            logger.warning("No analysis_tasks found for job %s", analysis_job_id)
            return False
        if self.heartbeat:
            self.heartbeat.set_current_job(analysis_job_id)
        self._update_worker_status(WorkerStatus.BUSY, analysis_job_id)

        # Prime contexts using any completed chunk_analysis tasks so job-level passes can run
        for task in tasks:
            if task.pass_id == "chunk_analysis" and task.result_json and task.status.value == "completed":
                self.engine._hydrate_chunk_context(task)

        pass_priority = {
            "chunk_analysis": 0,
            "sentiment_pass": 1,
            "categories_pass": 1,
            "subchunks": 2,
            "aggregate_results": 3,
            "hot_targets": 4,
            "drills": 5,
            "db_store": 6,
            "local_json": 7,
            "markdown_report": 8,
        }

        job_level_passes = set(
            ["aggregate_results", "hot_targets", "drills", "db_store", "local_json", "markdown_report"]
        )

        ordered = sorted(
            tasks,
            key=lambda t: (pass_priority.get(t.pass_id, 99), int(t.chunk_id or 0), t.task_id),
        )

        for task in ordered:
            should_run = task.status.value not in ("completed", "failed")
            if task.pass_id in job_level_passes and force_job_level_passes:
                should_run = True

            if not should_run:
                continue

            task_result = self._dispatch_pass(task)
            if task_result.status == "ok" and task_result.payload is not None:
                result_meta = dict(task_result.payload)
                result_meta.setdefault("worker_id", self.worker_id)
                result_meta.setdefault("completed_at", datetime.now(timezone.utc).isoformat())
                if task.pass_id == "drills":
                    drill_results = result_meta.get("drill_results") or {}
                    span_count = result_meta.get("drill_span_count") or 0
                    result_meta["drill_count"] = len(drill_results)
                    result_meta["drill_span_count"] = span_count
                ok = self.task_repo.mark_completed(task.task_id, result_meta)
                if ok:
                    logger.info("✓ Task %s completed via process_analysis_job", task.task_id)
                else:
                    logger.error("Failed to mark task %s as completed", task.task_id)
            else:
                error_msg = (
                    task_result.error_message
                    or f"Pass execution failed for pass_id={task.pass_id}"
                )
                ok = self.task_repo.mark_failed(task.task_id, error_msg)
                if ok:
                    logger.error("Task %s failed: %s", task.task_id, error_msg)
                else:
                    logger.error("Failed to mark task %s as failed", task.task_id)

        try:
            if self.task_repo.is_job_complete(analysis_job_id):
                logger.info("Job %s complete after bridge execution; aggregating", analysis_job_id)
                self._aggregate_job_results(analysis_job_id)
                return True
            logger.warning("Job %s not complete after bridge execution", analysis_job_id)
            return False
        finally:
            self.current_task = None
            if self.heartbeat:
                self.heartbeat.set_current_job(None)
            self._update_worker_status(WorkerStatus.IDLE)
            self._cleanup_vram()

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
        self._cleanup_vram()

    def _cleanup_vram(self) -> None:
        """Best-effort GPU memory cleanup after a job/run."""
        try:
            if torch and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # _get_job_context and _load_analysis_config now live on AnalysisEngine.
    # Worker code that still needs them (job-level passes, _hydrate_chunk_context
    # callers in process_analysis_job) reaches through self.engine.

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

                    task_result = self._dispatch_pass(task)

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
                            agg_success = self._aggregate_job_results(task.job_id)
                            agg_duration = (datetime.now(timezone.utc) - agg_start).total_seconds()
                            agg_status = "success" if agg_success else "failed"
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
    # Pass execution
    # ------------------------------------------------------------------
    # Per-pass dispatch lives on AnalysisEngine; the worker now delegates via
    # self.engine.run_task(...). Job-level passes still live below until Task 3.

    def _handle_job_level_pass(
        self,
        task: AnalysisTask,
        pass_id: str,
        chunk_text: str,
        metadata: Dict[str, Any],
        context: JobContext,
    ) -> Dict[str, Any]:
        if pass_id == "aggregate_results":
            if not context.is_ready_for_aggregation():
                return {
                    "pass_id": pass_id,
                    "status": "waiting",
                    "note": "Waiting for all chunk_analysis tasks to finish.",
                    "metadata": metadata,
                }
            aggregated = context.aggregated_result or self._run_aggregation(context)
            return {
                "pass_id": pass_id,
                "status": "completed",
                "analysis_summary": aggregated.get("analysis", {}),
                "stats": aggregated.get("stats", {}),
                "metadata": aggregated.get("metadata", {}),
            }

        if pass_id == "hot_targets":
            # Check if this is a job-level task (chunk_id=0) or a legacy chunk task
            # New jobs have one hot_targets task with chunk_id=0.
            # Legacy jobs have many tasks with chunk_id >= 0.
            is_job_level_task = int(task.chunk_id) == 0 and metadata.get("is_job_level")

            # Fallback: if 'is_job_level' is not explicitly set, assume chunk_id=0 implies
            # job-level behavior ONLY if total_chunks > 1. If it's a 1-chunk video, it doesn't matter.
            if not is_job_level_task and int(task.chunk_id) == 0 and context.total_chunks > 1:
                # Heuristic: if we only see one hot_targets task in the DB, it's job level.
                # But checking DB is expensive. We'll assume the new code convention.
                # To be safe for legacy jobs (which might have a task for chunk 0),
                # we should only force full scan if we are sure.
                # However, the 'analyze_to_db.py' sets "is_job_level": True for the single task.
                # So we rely on that flag or the fact that it's a known job-level pass in current code.
                pass

            if is_job_level_task:
                # This is a single task meant to cover the whole video.
                # We need all chunk texts.
                self.engine._load_all_chunk_texts(context)

                all_detections = []
                all_llm_spans = []

                # Iterate over all available chunks
                total_chunks = len(context.chunk_texts)
                for i, (cid, text) in enumerate(sorted(context.chunk_texts.items()), start=1):
                    logger.debug("Hot targets: scanning chunk %s (%d/%d)", cid, i, total_chunks)
                    dets, spans = self._detect_hot_targets(text, context.config, chunk_id=cid)
                    all_detections.extend(dets)
                    all_llm_spans.extend(spans)

                return {
                    "pass_id": pass_id,
                    "status": "completed",
                    "targets": all_detections,
                    "llm_spans": all_llm_spans,
                    "metadata": metadata,
                }
            else:
                # Legacy behavior: just scan the current chunk (task.chunk_text)
                detections, llm_spans = self._detect_hot_targets(chunk_text, context.config, chunk_id=int(task.chunk_id))
                return {
                    "pass_id": pass_id,
                    "status": "completed",
                    "targets": detections,
                    "llm_spans": llm_spans,
                    "metadata": metadata,
                }

        if pass_id == "drills":
            if not context.is_ready_for_aggregation():
                return {
                    "pass_id": pass_id,
                    "status": "waiting",
                    "note": "Aggregation not ready.",
                    "metadata": metadata,
                }
            
            # Determine if this is a job-level task or legacy chunk task
            is_job_level_task = int(task.chunk_id) == 0 and metadata.get("is_job_level")
            # Fallback for job-level inference if flag missing but chunk_id=0 on multi-chunk
            if not is_job_level_task and int(task.chunk_id) == 0 and context.total_chunks > 1:
                is_job_level_task = True

            chunk_filter: Optional[Set[int]] = None
            
            if is_job_level_task:
                # Load all texts for the global pass
                self.engine._load_all_chunk_texts(context)
            else:
                # Legacy: only run on this specific chunk
                # Also ensure we have the text for this chunk (should be hydrated but safe to check)
                cid = int(task.chunk_id)
                if cid not in context.chunk_texts:
                    context.chunk_texts[cid] = task.chunk_text or ""
                chunk_filter = {cid}

            drill_results, drill_summary, emitted_spans = self._run_drill_executor(context, chunk_filter=chunk_filter)
            if context.aggregated_result is not None:
                if drill_results:
                    context.aggregated_result.setdefault("drill_results", drill_results)
                if drill_summary:
                    context.aggregated_result.setdefault("drill_summary", drill_summary)
            
            # Merging results: careful not to overwrite if we are doing incremental updates?
            # Actually, context.drill_results_map is accumulated.
            # But here we just want to update what we found.
            context.drill_results_map.update(drill_results)
            # drill_summary is a list, extend it? 
            # If we are running legacy per-chunk, we might get many small summaries.
            # Ideally we'd merge them, but for now just appending is safer than losing data.
            context.drill_summary.extend(drill_summary)
            
            formatted = self._normalize_drill_spans(emitted_spans, context)
            if formatted:
                context.drill_spans.extend(formatted)
            span_count = sum(len(v or []) for v in emitted_spans.values()) if emitted_spans else 0
            logger.info(
                "Drills completed for job %s chunk %s: %d drills, %d spans",
                task.job_id,
                task.chunk_id,
                len(drill_results or {}),
                span_count,
            )
            return {
                "pass_id": pass_id,
                "status": "completed",
                "drill_results": drill_results,
                "summary": drill_summary,
                "metadata": metadata,
            }

        if pass_id == "db_store":
            if not context.is_ready_for_aggregation():
                return {
                    "pass_id": pass_id,
                    "status": "waiting",
                    "note": "Aggregation not ready.",
                    "metadata": metadata,
                }
            aggregated = context.aggregated_result or self._run_aggregation(context)
            stored = self._store_full_analysis(context, aggregated)
            return {
                "pass_id": pass_id,
                "status": "completed" if stored else "failed",
                "note": None if stored else "DB store failed",
                "metadata": aggregated.get("metadata", {}),
            }

        if pass_id in ("local_json", "markdown_report"):
            aggregated = context.aggregated_result or self._run_aggregation(context)
            artifact = self._write_local_artifacts(context, aggregated, pass_id)
            return {
                "pass_id": pass_id,
                "status": "completed",
                "artifact": artifact,
                "metadata": aggregated.get("metadata", {}),
            }

        return {
            "pass_id": pass_id,
            "status": "completed",
            "note": "No-op for this pass.",
            "metadata": metadata,
        }

    def _run_aggregation(self, context: JobContext) -> Dict[str, Any]:
        ordered_chunks = [context.chunk_results[i] for i in sorted(context.chunk_results)]
        aggregator = AnalysisAggregator()
        metadata = dict(context.metadata_template)
        metadata.setdefault("analysis_generated_at", datetime.now(timezone.utc).isoformat())
        aggregated = aggregator.aggregate(ordered_chunks, metadata)
        self._attach_summaries(aggregated, context.config)
        context.aggregated_result = aggregated
        return aggregated

    def _run_drill_executor(self, context: JobContext, chunk_filter: Optional[Set[int]] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
        drills = getattr(context.config, "drills", []) or []
        if not drills:
            return {}, [], {}

        def drill_to_dict(drill: Any) -> Optional[Dict[str, Any]]:
            if isinstance(drill, Drill):
                if hasattr(drill, "model_dump"):
                    return drill.model_dump()
                if hasattr(drill, "dict"):
                    return drill.dict()
            elif isinstance(drill, dict):
                return drill
            else:
                try:
                    return {k: v for k, v in vars(drill).items() if not k.startswith("_")}
                except Exception:
                    return None
            return None

        specs: List[Dict[str, Any]] = []
        for raw in drills:
            spec = drill_to_dict(raw)
            if spec:
                specs.append(spec)

        if not specs:
            return {}, [], {}

        chunk_entries: List[Dict[str, Any]] = []
        chunk_text_map: Dict[int, str] = {}
        for chunk_id in sorted(context.chunk_results):
            if chunk_filter is not None and chunk_id not in chunk_filter:
                continue
            
            text = context.chunk_texts.get(chunk_id, "")
            chunk_text_map[chunk_id] = text
            analysis = context.chunk_results.get(chunk_id) or {}
            categories = analysis.get("categories") or analysis.get("analysis", {}).get("categories") or []
            topics = analysis.get("topics") or analysis.get("analysis", {}).get("topics") or []
            meta = context.chunk_metadata_map.get(chunk_id) or {}
            chunk_entries.append(
                {
                    "chunk_id": chunk_id,
                    "text": text,
                    "categories": categories,
                    "topics": topics,
                    "start_sec": _to_float(meta.get("start_sec")),
                    "end_sec": _to_float(meta.get("end_sec")),
                }
            )

        if not chunk_entries:
            return {}, [], {}

        executor = DrillExecutor(
            drills=specs,
            base_model=self.model_name,
            base_url=self.model_url,
            options=getattr(self.analyzer, "options", {}) or {},
            log_mode="quiet",
            output_shapes=getattr(context.config, "output_shapes", {}) or {},
        )
        results, summary, emitted = executor.run(chunk_entries, chunk_texts=chunk_text_map)
        return results or {}, summary or [], emitted or {}

    def _normalize_drill_spans(
        self,
        emitted_spans: Dict[str, List[Dict[str, Any]]],
        context: JobContext,
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not emitted_spans:
            return normalized
        for drill_name, spans in emitted_spans.items():
            for span in spans or []:
                chunk_id = span.get("chunk_id")
                meta = context.chunk_metadata_map.get(chunk_id) or {}
                start_sec = _to_float(span.get("start_sec"))
                end_sec = _to_float(span.get("end_sec"))
                if start_sec is None:
                    start_sec = _to_float(meta.get("start_sec"))
                if end_sec is None:
                    end_sec = _to_float(meta.get("end_sec"))
                normalized.append(
                    {
                        "description": span.get("label") or span.get("description") or drill_name,
                        "context": span.get("context") or "",
                        "chunk_ids": [chunk_id] if chunk_id is not None else [],
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                        "target_name": drill_name,
                        "parties": span.get("parties"),
                        "sentiment": span.get("sentiment"),
                        "polarity": span.get("polarity"),
                    }
                )
        return normalized

    def _attach_summaries(self, aggregated: Dict[str, Any], config: AnalysisConfig) -> None:
        """
        Generate TLDR/outline/speaker summaries for new-style jobs so the web UI
        can render the same rich cards as legacy runs.
        """
        if aggregated.get("summaries"):
            return

        backend = getattr(config, "backend_params", {}) or {}
        if backend.get("skip_summaries"):
            return

        merged: Dict[str, Any] = {}
        try:
            primary = self.analyzer.summarize(aggregated)
            if primary:
                merged.update(primary)
        except Exception as exc:
            logger.warning("Failed to generate aggregate summaries: %s", exc)

        try:
            speaker_bits = self.analyzer.summarize_speaker(aggregated)
            if speaker_bits:
                merged.setdefault("speaker_overview", speaker_bits.get("speaker_overview", ""))
                merged.setdefault("personal_themes", speaker_bits.get("personal_themes", []))
                if speaker_bits.get("noteworthy_statements"):
                    merged.setdefault("noteworthy_statements", [])
                    merged["noteworthy_statements"].extend(speaker_bits["noteworthy_statements"])
                if speaker_bits.get("personal_conflicts"):
                    merged.setdefault("personal_conflicts", [])
                    merged["personal_conflicts"].extend(speaker_bits["personal_conflicts"])
        except Exception as exc:
            logger.warning("Failed to generate speaker summaries: %s", exc)

        if not merged:
            return

        alias = backend.get("speaker_alias") or backend.get("speaker_name") or "the speaker"
        for key in ("tldr_one_sentence", "summary_paragraph", "political_overview", "controversies", "speaker_overview"):
            if merged.get(key):
                merged[key] = self._rewrite_third_person(str(merged[key]), alias)

        aggregated["summaries"] = {**aggregated.get("summaries", {}), **merged}

    def _rewrite_third_person(self, text: str, alias: str) -> str:
        """Lightweight helper mirrored from TranscriptAnalysisPipeline."""
        if not isinstance(text, str) or not text:
            return text
        parts = re.split(r'(".*?")', text, flags=re.DOTALL)
        rewritten: List[str] = []
        for part in parts:
            if len(part) >= 2 and part.startswith('"') and part.endswith('"'):
                rewritten.append(part)
                continue
            updated = part
            replacements = [
                (r"\byou\s+are\b", f"{alias} is"),
                (r"\byou\s+were\b", f"{alias} was"),
                (r"\byou're\b", f"{alias} is"),
                (r"\byoure\b", f"{alias} is"),
                (r"\byour\b", f"{alias}'s"),
                (r"\byours\b", f"{alias}'s"),
                (r"\byourself\b", f"{alias}"),
                (r"\byourselves\b", f"{alias}"),
                (r"\byou\b", alias),
            ]
            for pattern, replacement in replacements:
                updated = re.sub(pattern, replacement, updated, flags=re.IGNORECASE)
            rewritten.append(updated)
        return "".join(rewritten)

    def _detect_hot_targets(self, chunk_text: str, config: AnalysisConfig, chunk_id: int) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Return (pattern_detections, llm_spans) for the current chunk.
        """
        detections: List[Dict[str, Any]] = []
        llm_spans: List[Dict[str, Any]] = []
        if not config.hot_targets:
            return detections, llm_spans

        lower_text = chunk_text.lower()
        pattern_targets: List[Any] = []
        llm_targets: List[Any] = []
        for spec in config.hot_targets:
            enabled = getattr(spec, "enabled", True)
            if not enabled:
                continue
            prompt = getattr(spec, "prompt", None) or getattr(spec, "instruction", None)
            pattern_type = getattr(spec, "pattern_type", "keyword")
            mode = getattr(spec, "mode", None) or getattr(spec, "pattern_type", "")
            use_llm_mode = str(mode).lower() == "llm" or pattern_type == "llm_tag" or bool(prompt)
            if use_llm_mode:
                llm_targets.append(spec)
            else:
                pattern_targets.append(spec)

        # Pattern-based detections
        for spec in pattern_targets:
            pattern_type = getattr(spec, "pattern_type", "keyword")
            pattern = getattr(spec, "pattern", None)
            if not pattern:
                continue
            matches: List[str] = []
            if pattern_type == "keyword":
                keywords = pattern if isinstance(pattern, list) else [pattern]
                for kw in keywords:
                    if kw and kw.lower() in lower_text:
                        matches.append(kw)
            elif pattern_type == "regex":
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                    matches = regex.findall(chunk_text)
                except re.error:
                    matches = []
            if matches:
                detections.append(
                    {
                        "target_id": getattr(spec, "id", "unknown"),
                        "description": getattr(spec, "description", ""),
                        "matches": matches,
                        "excerpt": self._excerpt_for_matches(chunk_text, matches[0]),
                        "source": "pattern",
                    }
                )

        # LLM-based detections
        chunk_payload = [{"chunk_id": chunk_id, "text": chunk_text}]
        for spec in llm_targets:
            spec_dict = spec.model_dump() if hasattr(spec, "model_dump") else spec.dict() if hasattr(spec, "dict") else spec if isinstance(spec, dict) else {}
            spans_resp = self.hot_target_runner.run_targets([spec_dict], chunk_payload)
            for name, entries in (spans_resp or {}).items():
                for entry in entries or []:
                    spans = entry.get("spans") or []
                    if not spans:
                        continue
                    llm_spans.append(
                        {
                            "target": name,
                            "chunk_id": entry.get("chunk_id"),
                            "spans": spans,
                            "model_used": entry.get("model_used"),
                            "endpoint": entry.get("endpoint"),
                            "source": "llm",
                        }
                    )

        return detections, llm_spans

    def _store_full_analysis(self, context: JobContext, aggregated: Dict[str, Any]) -> bool:
        if not hasattr(self.db, "store_full_analysis_with_chunks"):
            logger.error("DB storage adapter missing store_full_analysis_with_chunks; cannot persist analysis for %s", context.job_id)
            return False
        chunk_analyses = [context.chunk_results[i] for i in sorted(context.chunk_results)]
        spans = self._build_topic_person_spans(chunk_analyses, context.ytid)
        try:
            self.db.store_full_analysis_with_chunks(
                aggregated,
                chunk_analyses,
                spans=spans,
                model=self.model_name,
                batch_id=0,
                analysis_type=context.config.analysis_type,
                request_text="distributed",
                diarized=context.config.diarized,
                transcription_machine=self.machine_alias,
            )
            if context.drill_spans:
                self.db.store_target_spans(
                    context.ytid,
                    context.drill_spans,
                    source_pass="drill",
                    pass_tier="drill",
                    analysis_type=context.config.analysis_type,
                    batch_id=0,
                    diarized=context.config.diarized,
                    transcription_machine=self.machine_alias,
                )
            return True
        except Exception as exc:
            logger.error("Failed to store full analysis for job %s (ytid=%s): %s", context.job_id, context.ytid, exc, exc_info=True)
            return False

    def _build_topic_person_spans(self, chunk_analyses: List[Dict[str, Any]], ytid: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Build topic and person spans by grouping chunk topics/people (legacy parity).
        """
        topic_spans: List[Dict[str, Any]] = []
        person_spans: List[Dict[str, Any]] = []

        topic_to_chunks: Dict[str, List[int]] = {}
        for chunk in chunk_analyses:
            chunk_id = chunk.get("chunk_id", 0)
            for topic in (chunk.get("topics") or []):
                if not topic:
                    continue
                norm = str(topic).lower().strip()
                topic_to_chunks.setdefault(norm, []).append(chunk_id)

        for norm_topic, chunk_ids in topic_to_chunks.items():
            first_chunk = next((c for c in chunk_analyses if c.get("chunk_id") in chunk_ids), None)
            context_snippet = (first_chunk.get("summary", "") if first_chunk else "")[:200]
            sentiment = (first_chunk.get("sentiment") if first_chunk else None)
            topic_spans.append({
                "ytid": ytid,
                "topic": norm_topic.title(),
                "normalized_topic": norm_topic,
                "chunk_ids": sorted(chunk_ids),
                "context": context_snippet,
                "sentiment": sentiment if sentiment and sentiment != "unknown" else None,
                "source_pass": "chunk_analysis",
            })

        person_to_chunks: Dict[str, List[int]] = {}
        for chunk in chunk_analyses:
            chunk_id = chunk.get("chunk_id", 0)
            for person in (chunk.get("people") or []):
                if not person:
                    continue
                norm = str(person).lower().strip()
                person_to_chunks.setdefault(norm, []).append(chunk_id)

        for norm_name, chunk_ids in person_to_chunks.items():
            first_chunk = next((c for c in chunk_analyses if c.get("chunk_id") in chunk_ids), None)
            context_snippet = (first_chunk.get("summary", "") if first_chunk else "")[:200]
            sentiment = (first_chunk.get("sentiment") if first_chunk else None)
            person_spans.append({
                "ytid": ytid,
                "person_name": norm_name.title(),
                "normalized_name": norm_name,
                "chunk_ids": sorted(chunk_ids),
                "context": context_snippet,
                "sentiment": sentiment if sentiment and sentiment != "unknown" else None,
                "polarity": None,
                "source_pass": "chunk_analysis",
            })

        return {"topic_spans": topic_spans, "person_spans": person_spans}

    def _write_local_artifacts(self, context: JobContext, aggregated: Dict[str, Any], pass_id: str) -> Dict[str, str]:
        safe_job = context.job_id.replace(":", "_")
        output_dir = Path(os.environ.get("VIDOPS_PROJECT_ROOT", ".")) / "results" / context.ytid
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts: Dict[str, str] = {}
        if pass_id in ("local_json", "markdown_report"):
            if pass_id == "local_json":
                json_path = output_dir / f"{safe_job}_analysis.json"
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(aggregated, fh, indent=2)
                artifacts["json"] = str(json_path)
            else:
                md_path = output_dir / f"{safe_job}_analysis.md"
                from scripts.analysis.analyze_transcript import generate_markdown_report

                generate_markdown_report(aggregated, md_path)
                artifacts["markdown"] = str(md_path)
        return artifacts

    # _generate_subchunks, _score_sentiment, _extract_categories_from_text
    # moved to AnalysisEngine.

    def _excerpt_for_matches(self, text: str, keyword: str) -> str:
        if not keyword:
            return text[:160]
        idx = text.lower().find(keyword.lower())
        if idx == -1:
            return text[:160]
        start = max(0, idx - 60)
        end = min(len(text), idx + len(keyword) + 60)
        return text[start:end].strip()

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
    # Aggregation
    # ------------------------------------------------------------------

    def _aggregate_job_results(self, job_id: str) -> bool:
        """
        Aggregate all task results for a job into analysis_results.

        Groups results by pass_id, with a list of {chunk_id, result} per pass.
        """
        context = self.job_contexts.get(job_id)
        tasks = self.task_repo.get_job_tasks(job_id)
        if not tasks:
            logger.warning("No tasks found for job %s; nothing to aggregate", job_id)
            return False

        # Derive ytid and config_id from the first task's metadata/job_id
        first = tasks[0]
        ytid = first.ytid
        meta0 = first.chunk_metadata or {}
        config_id = meta0.get("config_id")

        if not config_id:
            # Fallback: parse from job_id pattern "ytid:config_id:timestamp"
            parts = job_id.split(":")
            if len(parts) >= 2:
                config_id = parts[1]
            else:
                config_id = "unknown"

        # Group results by pass
        results_by_pass: Dict[str, List[Dict[str, Any]]] = {}
        for t in tasks:
            if t.result_json is None:
                continue
            results_by_pass.setdefault(t.pass_id, []).append(
                {
                    "chunk_id": t.chunk_id,
                    "result": t.result_json,
                }
            )

        try:
            progress = self.task_repo.get_job_progress(job_id)
            total = progress.get("total", len(tasks))
            completed = progress.get("completed", 0)
            failed = progress.get("failed", 0)
        except Exception as exc:
            logger.warning("Failed to read job progress for %s: %s", job_id, exc)
            total = len(tasks)
            completed = len(results_by_pass)
            failed = 0

        status = "completed"
        if total == 0 or completed + failed < total:
            status = "processing"
        elif failed > 0:
            status = "failed"

        logger.info(
            "Storing aggregated results for job %s (ytid=%s, config_id=%s, total=%s, completed=%s, failed=%s, status=%s)",
            job_id,
            ytid,
            config_id,
            total,
            completed,
            failed,
            status,
        )

        self.results_repo.upsert_results(
            job_id=job_id,
            ytid=ytid,
            config_id=config_id,
            results_by_pass=results_by_pass,
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            status=status,
        )
        if context:
            self.job_contexts.pop(job_id, None)
        return True

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
