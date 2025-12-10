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
import time
import sys
import signal
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
import logging

from vidops.config import load_config
from vidops.dal.analysis_task_repository import AnalysisDatabase, AnalysisTaskRepository
from vidops.models import AnalysisTask
from vidops.dal import AnalysisResultsRepository
from vidops.analysis.llm import OllamaAnalyzer
from vidops.monitoring.exporter import start_metrics_server, stop_metrics_server
from vidops.monitoring.metrics import (
    tasks_claimed_total,
    tasks_completed_total,
    tasks_failed_total,
    task_processing_duration_seconds,
    task_processing_duration_summary,
    worker_uptime_seconds,
    worker_current_task_gauge,
    worker_memory_usage_bytes,
    worker_cpu_usage_percent,
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
        capabilities: List[str],
        db_host: str = "localhost",
        db_name: str = "transcripts",
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
        lease_duration_minutes: int = 60,
        metrics_port: int = 8888,
    ) -> None:
        self.machine_alias = machine_alias
        self.worker_type = worker_type
        self.model_url = model_url
        self.model_name = model_name
        self.capabilities = capabilities
        self.lease_duration = timedelta(minutes=lease_duration_minutes)
        self.metrics_port = metrics_port

        self.worker_id = f"{machine_alias}:{worker_type}:{model_name}:{os.getpid()}"

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

        # Single OllamaAnalyzer instance for chunk_analysis
        self.analyzer = OllamaAnalyzer(
            model=model_name,
            base_url=model_url,
            options={},
            custom_request="",
            log_mode="quiet",
            category_suggestions=[],
            category_map={},
        )

        self.should_exit: bool = False
        self.current_task: Optional[AnalysisTask] = None
        self.metrics_server = None
        self.startup_time = datetime.now(timezone.utc)

        # Start metrics server if port > 0
        if self.metrics_port > 0:
            try:
                self.metrics_server = start_metrics_server(host="0.0.0.0", port=self.metrics_port)
            except Exception as e:
                logger.warning("Failed to start metrics server on port %d: %s", self.metrics_port, e)

        # Initialize uptime tracking
        self._last_uptime_update = datetime.now(timezone.utc)
        worker_uptime_seconds.labels(worker_id=self.worker_id).set(0)

        logger.info("Worker initialized: %s", self.worker_id)
        logger.info("  Type: %s", worker_type)
        logger.info("  Model: %s @ %s", model_name, model_url)
        logger.info("  Capabilities: %s", ", ".join(capabilities))
        if self.metrics_server:
            logger.info("  Metrics: http://0.0.0.0:%d/metrics", self.metrics_port)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        """
        Main worker loop. Runs until interrupted.
        """
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        logger.info("Worker %s starting main loop...", self.worker_id)

        try:
            while not self.should_exit:
                try:
                    task = self.task_repo.claim_next(
                        worker_id=self.worker_id,
                        worker_capabilities=self.capabilities,
                        lease_duration=self.lease_duration,
                    )

                    if not task:
                        # No tasks available – back off briefly
                        # Update all gauges periodically
                        self._update_health_metrics()
                        time.sleep(5)
                        continue

                    self.current_task = task

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

                    result = self._execute_pass(task)

                    if result is not None:
                        # Attach worker metadata
                        result_meta = dict(result)
                        result_meta.setdefault("worker_id", self.worker_id)
                        result_meta.setdefault(
                            "completed_at",
                            datetime.now(timezone.utc).isoformat(),
                        )
                        ok = self.task_repo.mark_completed(task.task_id, result_meta)
                        if ok:
                            # Record successful task completion
                            duration = result.get("duration_sec", 0)
                            tasks_completed_total.labels(
                                worker_id=self.worker_id,
                                pass_id=task.pass_id,
                                status="ok",
                            ).inc()
                            task_processing_duration_seconds.labels(
                                worker_id=self.worker_id,
                                pass_id=task.pass_id,
                            ).observe(duration)
                            task_processing_duration_summary.labels(
                                worker_id=self.worker_id,
                                pass_id=task.pass_id,
                            ).observe(duration)
                            logger.info("✓ Task %s completed", task.task_id)
                        else:
                            logger.error("Failed to mark task %s as completed", task.task_id)
                    else:
                        error_msg = f"Pass execution returned None for pass_id={task.pass_id}"
                        ok = self.task_repo.mark_failed(task.task_id, error_msg)
                        if ok:
                            # Record failed task
                            tasks_failed_total.labels(
                                worker_id=self.worker_id,
                                pass_id=task.pass_id,
                                error_type="none_result",
                            ).inc()
                            errors_total.labels(
                                worker_id=self.worker_id,
                                error_type="pass_execution_failed",
                            ).inc()
                            last_error_timestamp.labels(
                                worker_id=self.worker_id,
                            ).set(datetime.now(timezone.utc).timestamp())
                            logger.error("Task %s failed: %s", task.task_id, error_msg)
                        else:
                            logger.error("Failed to mark task %s as failed", task.task_id)

                    # After finishing the task, check if we can aggregate the job
                    try:
                        if self.task_repo.is_job_complete(task.job_id):
                            logger.info("Job %s appears complete – aggregating results", task.job_id)
                            agg_start = datetime.now(timezone.utc)
                            self._aggregate_job_results(task.job_id)
                            agg_duration = (datetime.now(timezone.utc) - agg_start).total_seconds()
                            jobs_aggregated_total.labels(worker_id=self.worker_id).inc()
                            job_aggregation_duration_seconds.labels(worker_id=self.worker_id).observe(agg_duration)
                    except Exception as exc:
                        logger.error("Error checking/aggregating job %s: %s", task.job_id, exc)
                        errors_total.labels(
                            worker_id=self.worker_id,
                            error_type="job_aggregation_error",
                        ).inc()
                        last_error_timestamp.labels(
                            worker_id=self.worker_id,
                        ).set(datetime.now(timezone.utc).timestamp())

                    self.current_task = None
                    worker_current_task_gauge.labels(worker_id=self.worker_id).set(0)

                except KeyboardInterrupt:
                    logger.info("Received KeyboardInterrupt, exiting loop...")
                    break
                except Exception as exc:
                    logger.error("Unexpected error in worker loop: %s", exc)
                    import traceback

                    traceback.print_exc()
                    time.sleep(5)
        finally:
            logger.info("Worker %s shutting down", self.worker_id)
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

    def _execute_pass(self, task: AnalysisTask) -> Optional[Dict[str, Any]]:
        """
        Execute the LLM pass on the chunk.

        Currently:
        - chunk_analysis is wired to OllamaAnalyzer.analyze_chunk
        - sentiment_pass and categories_pass are lightweight stubs
        - other passes are simple placeholders
        """
        start_time = datetime.now(timezone.utc)

        chunk_text = task.chunk_text or ""
        chunk_metadata = task.chunk_metadata or {}
        pass_id = task.pass_id

        logger.info(
            "Executing pass %s on job %s (task_id=%s, chunk_id=%s)",
            pass_id,
            task.job_id,
            task.task_id,
            task.chunk_id,
        )

        try:
            if not chunk_text.strip():
                logger.warning("Task %s has empty chunk_text; marking as failed", task.task_id)
                return None

            if pass_id == "chunk_analysis":
                result = self._pass_chunk_analysis(task, chunk_text, chunk_metadata)
            elif pass_id == "sentiment_pass":
                result = self._pass_sentiment(chunk_text, chunk_metadata)
            elif pass_id == "categories_pass":
                result = self._pass_categories(chunk_text, chunk_metadata)
            elif pass_id == "subchunks":
                result = self._pass_subchunks(chunk_text, chunk_metadata)
            elif pass_id in (
                "aggregate_results",
                "hot_targets",
                "drills",
                "db_store",
                "local_json",
                "markdown_report",
            ):
                result = {
                    "pass_id": pass_id,
                    "status": "completed",
                    "note": "stub implementation",
                    "metadata": chunk_metadata,
                }
            else:
                result = {
                    "pass_id": pass_id,
                    "status": "unknown_pass",
                    "metadata": chunk_metadata,
                }

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            result.setdefault("duration_sec", duration)
            result.setdefault("pass_id", pass_id)
            return result
        except Exception as exc:
            logger.error("Error executing pass %s for task %s: %s", pass_id, task.task_id, exc)
            import traceback

            traceback.print_exc()
            return None

    def _pass_chunk_analysis(
        self,
        task: AnalysisTask,
        chunk_text: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Real implementation of chunk_analysis using OllamaAnalyzer.
        """
        # Use the existing single-chunk helper
        analysis = self.analyzer.analyze_chunk(chunk_text, int(task.chunk_id))
        # Attach bookkeeping / metadata
        return {
            "pass_id": "chunk_analysis",
            "status": "completed",
            "analysis": analysis,
            "metadata": metadata,
            "model_used": self.model_name,
        }

    def _pass_sentiment(self, chunk_text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stub implementation of sentiment_pass.

        For now, this simply marks a neutral sentiment and echoes metadata.
        """
        return {
            "pass_id": "sentiment_pass",
            "status": "completed",
            "sentiment": "neutral",
            "metadata": metadata,
        }

    def _pass_categories(self, chunk_text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stub implementation of categories_pass.

        Can be extended later to call a lightweight classification model.
        """
        return {
            "pass_id": "categories_pass",
            "status": "completed",
            "categories": [],
            "metadata": metadata,
        }

    def _pass_subchunks(self, chunk_text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stub implementation of subchunks; currently just marks completion.

        A future version could delegate to the existing subchunk/speaker pipeline.
        """
        return {
            "pass_id": "subchunks",
            "status": "completed",
            "metadata": metadata,
        }

    # ------------------------------------------------------------------
    # Health Metrics
    # ------------------------------------------------------------------

    def _update_health_metrics(self) -> None:
        """Update worker health gauges periodically."""
        import os
        try:
            # Update uptime
            now = datetime.now(timezone.utc)
            uptime_seconds = (now - self.startup_time).total_seconds()
            worker_uptime_seconds.labels(worker_id=self.worker_id).set(uptime_seconds)

            # Update memory usage (read from /proc/self/status)
            try:
                with open("/proc/self/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            # VmRSS is in kB, convert to bytes
                            memory_kb = int(line.split()[1])
                            memory_bytes = memory_kb * 1024
                            worker_memory_usage_bytes.labels(worker_id=self.worker_id).set(memory_bytes)
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

        progress = self.task_repo.get_job_progress(job_id)
        total = progress.get("total", len(tasks))
        completed = progress.get("completed", 0)
        failed = progress.get("failed", 0)

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
    parser.add_argument("--machine-alias", required=False, default=os.uname().nodename)
    parser.add_argument("--worker-type", required=False, default="analysis_gpu")
    parser.add_argument("--model-url", required=False, default="http://localhost:11434")
    parser.add_argument("--model-name", required=False, default="qwen2.5:7b-instruct")
    parser.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        help="Capability tag for this worker (repeatable)",
    )
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-name", default="transcripts")
    parser.add_argument("--db-user", default=None)
    parser.add_argument("--db-password", default=None)
    parser.add_argument("--lease-minutes", type=int, default=60)

    args = parser.parse_args(argv)

    caps = args.capabilities or [args.model_name, "gpu_8gb"]

    worker = AnalysisWorker(
        machine_alias=args.machine_alias,
        worker_type=args.worker_type,
        model_url=args.model_url,
        model_name=args.model_name,
        capabilities=caps,
        db_host=args.db_host,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
        lease_duration_minutes=args.lease_minutes,
    )
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
