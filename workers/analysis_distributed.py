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
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
import logging
from pathlib import Path
try:
    import torch  # Optional, for GPU cleanup
except Exception:
    torch = None

from configuration import load_config
from dal.analysis_task_repository import AnalysisDatabase, AnalysisTaskRepository
from models import AnalysisTask
from dal import AnalysisResultsRepository
from scripts.analysis.analyze_transcript import OllamaAnalyzer, AnalysisAggregator, VTTParser, TranscriptChunker
from scripts.analysis.analysis_config import AnalysisConfig, HotTargetRule, Drill
from scripts.analysis.drills import DrillExecutor
from scripts.analysis.llm.hot_targets import HotTargetRunner
from monitoring.exporter import start_metrics_server, stop_metrics_server
from monitoring.metrics import (
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


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("AnalysisWorker")


@dataclass
class JobContext:
    """Caches per-job state so aggregation/hot-target passes can use chunk outputs."""

    job_id: str
    ytid: str
    config_id: str
    config: AnalysisConfig
    total_chunks: int
    chunk_results: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    aggregated_result: Optional[Dict[str, Any]] = None
    metadata_template: Dict[str, Any] = field(default_factory=dict)
    chunk_texts: Dict[int, str] = field(default_factory=dict)
    chunk_metadata_map: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    drill_results_map: Dict[str, Any] = field(default_factory=dict)
    drill_summary: List[Dict[str, Any]] = field(default_factory=list)
    drill_spans: List[Dict[str, Any]] = field(default_factory=list)

    def is_ready_for_aggregation(self) -> bool:
        return len(self.chunk_results) >= self.total_chunks


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
        self.job_contexts: Dict[str, JobContext] = {}

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
        self.hot_target_runner = HotTargetRunner(
            model=model_name,
            base_url=model_url,
            options=getattr(self.analyzer, "options", {}) or {},
            log_mode="quiet",
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

        # Prime contexts using any completed chunk_analysis tasks so job-level passes can run
        for task in tasks:
            if task.pass_id == "chunk_analysis" and task.result_json and task.status.value == "completed":
                self._hydrate_chunk_context(task)

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

            result = self._execute_pass(task)
            if result is not None:
                result_meta = dict(result)
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
                error_msg = f"Pass execution returned None for pass_id={task.pass_id}"
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
            self._cleanup_vram()

    def shutdown(self) -> None:
        """Clean up resources when used outside the long-running loop."""
        if self.metrics_server:
            try:
                stop_metrics_server()
            except Exception:
                pass
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

    def _get_job_context(self, task: AnalysisTask) -> JobContext:
        ctx = self.job_contexts.get(task.job_id)
        if ctx:
            return ctx

        metadata = task.chunk_metadata or {}
        config_id = metadata.get("config_id") or "default"
        config = self._load_analysis_config(config_id)
        total_chunks = int(metadata.get("total_chunks") or metadata.get("chunk_count") or 1)
        template = {
            "video_id": task.ytid,
            "title": metadata.get("title") or task.ytid,
            "date": metadata.get("date") or metadata.get("recorded_date") or "unknown",
            "duration": metadata.get("duration"),
        }
        ctx = JobContext(
            job_id=task.job_id,
            ytid=task.ytid,
            config_id=config_id,
            config=config,
            total_chunks=total_chunks,
            metadata_template=template,
        )
        self.job_contexts[task.job_id] = ctx
        return ctx

    def _load_analysis_config(self, config_id: str) -> AnalysisConfig:
        row = None
        try:
            row = self.db.get_analysis_config(config_id)
        except Exception:
            row = None

        if not row:
            # Fallback to default AnalysisConfig skeleton
            config = AnalysisConfig(
                id=config_id,
                name=f"default-{config_id}",
                passes=[
                    {"id": "chunk_analysis", "phase": "chunk", "enabled": True},  # type: ignore[arg-type]
                ],
            )
        else:
            payload = row.get("config_json") if isinstance(row, dict) else None
            if not payload:
                payload = {}
            config = AnalysisConfig.model_validate(payload)

        # Attach drills managed via the dedicated drills table.
        try:
            drill_rows = self.db.list_drills_for_config(config_id)
        except Exception as exc:
            logger.warning("Failed to load drills for config %s: %s", config_id, exc)
            drill_rows = []
        if drill_rows:
            normalized_drills: List[Drill] = []
            for d in drill_rows:
                try:
                    drill_payload = {
                        "id": d.get("id"),
                        "name": d["name"],
                        "description": d.get("description", ""),
                        "prompt": d.get("prompt", ""),
                        "scope": d.get("scope", "chunks"),
                        "depends_on": d.get("depends_on") or [],
                        "output_shape": d.get("output_shape", "span"),
                        "always": bool(d.get("always")),
                        "min_hits": int(d.get("min_hits") or 0),
                        "keywords": d.get("keywords") or [],
                        "match": d.get("match") or [],
                        "category": d.get("category"),
                        "cooldown": int(d.get("cooldown") or 0),
                        "detail_pass": d.get("detail_pass") or {},
                    }
                    normalized_drills.append(Drill.model_validate(drill_payload))
                except Exception as exc:
                    logger.warning("Failed to normalize drill %s: %s", d.get("name"), exc)
            if normalized_drills:
                config.drills = normalized_drills

        return config

        logger.info("Worker initialized: %s", self.worker_id)
        logger.info("  Type: %s", worker_type)
        logger.info("  Model: %s @ %s", model_name, model_url)
        logger.info("  Capabilities: %s", ", ".join(capabilities))
        if self.metrics_server:
            logger.info("  Metrics: http://0.0.0.0:%d/metrics", self.metrics_port)
        logger.info("  Configuration: http://127.0.0.1:5000/ (analysis configuration website)")
        logger.info("  Documentation: See docs/ANALYSIS.md for setup and troubleshooting")

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
                                error_category="pass_execution_failed",
                            ).inc()
                            last_error_timestamp.labels(
                                worker_id=self.worker_id,
                                error_category="pass_execution_failed",
                            ).set(datetime.now(timezone.utc).timestamp())
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
                    worker_current_task_gauge.labels(worker_id=self.worker_id).set(0)

                except KeyboardInterrupt:
                    logger.info("Received KeyboardInterrupt, exiting loop...")
                    break
                except Exception as exc:
                    logger.error("Unexpected error in worker loop: %s", exc)
                    import traceback

                    traceback.print_exc()
                    try:
                        if self.db.conn:
                            self.db.conn.rollback()
                    except Exception:
                        pass
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
        context = self._get_job_context(task)

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
                result = self._pass_chunk_analysis(task, chunk_text, chunk_metadata, context)
            elif pass_id == "sentiment_pass":
                result = self._pass_sentiment(task, chunk_text, chunk_metadata, context)
            elif pass_id == "categories_pass":
                result = self._pass_categories(task, chunk_text, chunk_metadata, context)
            elif pass_id == "subchunks":
                result = self._pass_subchunks(task, chunk_text, chunk_metadata, context)
            elif pass_id in (
                "aggregate_results",
                "hot_targets",
                "drills",
                "db_store",
                "local_json",
                "markdown_report",
            ):
                result = self._handle_job_level_pass(task, pass_id, chunk_text, chunk_metadata, context)
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
        context: JobContext,
    ) -> Dict[str, Any]:
        """
        Real implementation of chunk_analysis using OllamaAnalyzer.
        """
        # Use the existing single-chunk helper
        chunk_idx = int(task.chunk_id)
        analysis = self.analyzer.analyze_chunk(chunk_text, chunk_idx)
        self._ensure_chunk_defaults(analysis, chunk_text)
        # Attach bookkeeping / metadata
        context.chunk_results[chunk_idx] = analysis
        context.chunk_texts[chunk_idx] = chunk_text
        context.chunk_metadata_map[chunk_idx] = metadata or {}
        return {
            "pass_id": "chunk_analysis",
            "status": "completed",
            "analysis": analysis,
            "metadata": metadata,
            "model_used": self.model_name,
        }

    def _ensure_chunk_defaults(self, analysis: Dict[str, Any], chunk_text: str) -> None:
        text = (chunk_text or "").strip()
        if text and not analysis.get("summary"):
            analysis["summary"] = text[:600]
        if text and not analysis.get("key_points"):
            summary_src = analysis.get("summary") or text
            words = summary_src.split()
            trimmed = " ".join(words[:50]).strip()
            if len(words) > 50:
                trimmed = f"{trimmed}…"
            analysis["key_points"] = [trimmed] if trimmed else []
        if text and not analysis.get("notable_quotes"):
            quotes = re.findall(r'"([^"]{10,})"', text)
            if quotes:
                analysis["notable_quotes"] = quotes[:3]
        if not analysis.get("topics"):
            cats = analysis.get("categories")
            if isinstance(cats, list) and cats:
                analysis["topics"] = cats[:5]

    def _hydrate_chunk_context(self, task: AnalysisTask) -> None:
        """Hydrate in-memory context from an already-completed chunk task."""
        try:
            chunk_idx = int(task.chunk_id)
        except Exception:
            return
        context = self._get_job_context(task)
        analysis = {}
        if isinstance(task.result_json, dict):
            analysis = task.result_json.get("analysis") or task.result_json
        self._ensure_chunk_defaults(analysis, task.chunk_text or "")
        context.chunk_results[chunk_idx] = analysis
        context.chunk_texts[chunk_idx] = task.chunk_text or ""
        context.chunk_metadata_map[chunk_idx] = task.chunk_metadata or {}

    def _pass_sentiment(
        self,
        task: AnalysisTask,
        chunk_text: str,
        metadata: Dict[str, Any],
        context: JobContext,
    ) -> Dict[str, Any]:
        analysis = context.chunk_results.get(int(task.chunk_id))
        if not analysis:
            analysis = self.analyzer.analyze_chunk(chunk_text, int(task.chunk_id))
            context.chunk_results[int(task.chunk_id)] = analysis
        label, score = self._score_sentiment(chunk_text)
        sentiment = analysis.get("sentiment") or label
        return {
            "pass_id": "sentiment_pass",
            "status": "completed",
            "sentiment": sentiment,
            "details": {
                "heuristic_score": score,
                "chunks_considered": len(chunk_text.split()),
            },
            "metadata": metadata,
        }

    def _pass_categories(
        self,
        task: AnalysisTask,
        chunk_text: str,
        metadata: Dict[str, Any],
        context: JobContext,
    ) -> Dict[str, Any]:
        analysis = context.chunk_results.get(int(task.chunk_id))
        categories = []
        if analysis:
            categories = analysis.get("categories") or []
        else:
            categories = self._extract_categories_from_text(chunk_text)
        normalized = sorted({c.strip().lower() for c in categories if isinstance(c, str) and c.strip()})
        return {
            "pass_id": "categories_pass",
            "status": "completed",
            "categories": normalized,
            "metadata": metadata,
        }

    def _pass_subchunks(
        self,
        task: AnalysisTask,
        chunk_text: str,
        metadata: Dict[str, Any],
        context: JobContext,
    ) -> Dict[str, Any]:
        subchunks = self._generate_subchunks(chunk_text)
        return {
            "pass_id": "subchunks",
            "status": "completed",
            "subchunks": subchunks,
            "metadata": metadata,
        }

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
            drill_results, drill_summary, emitted_spans = self._run_drill_executor(context)
            if context.aggregated_result is not None:
                if drill_results:
                    context.aggregated_result.setdefault("drill_results", drill_results)
                if drill_summary:
                    context.aggregated_result.setdefault("drill_summary", drill_summary)
            context.drill_results_map = drill_results
            context.drill_summary = drill_summary
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

    def _run_drill_executor(self, context: JobContext) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
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

    def _generate_subchunks(self, chunk_text: str) -> List[Dict[str, Any]]:
        parser = VTTParser()
        # Convert chunk into faux VTT by splitting sentences
        sentences = re.split(r"(?<=[.!?])\s+", chunk_text.strip())
        subchunks: List[Dict[str, Any]] = []
        for idx, sentence in enumerate(sentences, start=1):
            if not sentence:
                continue
            subchunks.append(
                {
                    "subchunk_id": idx,
                    "text": sentence,
                    "word_count": len(sentence.split()),
                }
            )
            if len(subchunks) >= 5:
                break
        if not subchunks:
            subchunks.append({"subchunk_id": 1, "text": chunk_text[:200], "word_count": len(chunk_text.split())})
        return subchunks

    def _score_sentiment(self, chunk_text: str) -> tuple[str, float]:
        positive = {"great", "good", "improve", "excellent", "optimistic", "win", "positive"}
        negative = {"bad", "terrible", "worse", "concern", "issue", "problem", "negative", "angry"}
        words = [w.lower().strip(".,!?") for w in chunk_text.split()]
        pos_hits = sum(1 for w in words if w in positive)
        neg_hits = sum(1 for w in words if w in negative)
        score = (pos_hits - neg_hits) / max(1, len(words))
        if score > 0.01:
            label = "optimistic"
        elif score < -0.01:
            label = "concerned"
        else:
            label = "neutral"
        return label, score

    def _extract_categories_from_text(self, chunk_text: str) -> List[str]:
        mapping = {
            "politics": ["election", "senate", "policy", "politics"],
            "finance": ["market", "stocks", "economy", "inflation"],
            "technology": ["software", "ai", "technology", "startup"],
            "conflict": ["attack", "war", "conflict", "fight"],
        }
        lower_text = chunk_text.lower()
        categories: List[str] = []
        for label, keywords in mapping.items():
            if any(kw in lower_text for kw in keywords):
                categories.append(label)
        return categories

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
