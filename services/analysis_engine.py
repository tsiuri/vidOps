"""
Stateful analysis engine. Owns the OllamaAnalyzer, HotTargetRunner, AnalysisDatabase,
and per-job context cache. Public surface: run_task, process_job, aggregate_results,
shutdown. Caller (worker or queue bridge) is responsible for the claim loop, status
updates, metrics emission, and signal handling.

See spec: ~/projects/vidops_cleanup_2026/2026-05-06-analysis-engine-extraction-design.md
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from configuration import load_config
from dal import AnalysisResultsRepository
from dal.analysis_task_repository import AnalysisDatabase, AnalysisTaskRepository
from models import AnalysisTask
from scripts.analysis.analysis_config import AnalysisConfig, Drill
from scripts.analysis.analyze_transcript import AnalysisAggregator, OllamaAnalyzer
from scripts.analysis.drills import DrillExecutor
from scripts.analysis.llm.hot_targets import HotTargetRunner

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> Optional[float]:
    """Coerce to float, returning None for blanks/invalid values."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


@dataclass
class TaskResult:
    """Engine returns this from run_task. Worker uses it to update task row + emit metrics."""

    status: Literal["ok", "failed", "skipped"]
    duration_s: float
    pass_id: str
    job_id: str
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    payload: Optional[dict] = None


@dataclass
class JobResult:
    """Engine returns this from process_job (one-shot full-job execution from the bridge)."""

    job_id: str
    tasks_total: int
    tasks_ok: int
    tasks_failed: int
    aggregate_status: Literal["ok", "partial", "failed"]
    duration_s: float


class AnalysisEngine:
    """
    Stateful analysis engine. One instance per worker process or per bridge invocation.
    """

    def __init__(
        self,
        *,
        model_name: str,
        model_url: str,
        model_profile_id: Optional[int],
        ollama_options: Optional[dict] = None,
        artifacts_root: Optional[Path] = None,
        machine_alias: Optional[str] = None,
    ) -> None:
        self.config = load_config()
        self.model_name = model_name
        self.model_url = model_url
        self.model_profile_id = model_profile_id
        self.machine_alias = machine_alias or getattr(
            getattr(self.config, "workers", None), "machine_alias", "unknown"
        )
        self.artifacts_root = artifacts_root or Path(self.config.paths.central_storage_root)

        self.db = AnalysisDatabase()
        self.db.connect()
        self.task_repo = AnalysisTaskRepository(self.db)
        self.results_repo = AnalysisResultsRepository(self.db)

        self.analyzer = OllamaAnalyzer(
            model=model_name,
            base_url=model_url,
            options=ollama_options or {},
            log_mode="quiet",
        )
        self.hot_target_runner = HotTargetRunner(
            model=model_name,
            base_url=model_url,
            options=getattr(self.analyzer, "options", {}) or {},
            log_mode="quiet",
        )

        self.job_contexts: Dict[str, JobContext] = {}
        self._shutdown_called = False

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def run_task(self, task: AnalysisTask, *, worker_id: str) -> TaskResult:
        """
        Execute a single task. Never raises on data errors — failures come back as
        status="failed" with an error_category. Worker is responsible for the
        analysis_tasks row update + metrics emission based on the returned TaskResult.
        """
        started = time.monotonic()
        try:
            ctx = self._get_job_context(task)
            payload = self._execute_pass(task, ctx, worker_id=worker_id)
            duration_s = time.monotonic() - started
            if payload is None:
                return TaskResult(
                    status="failed",
                    duration_s=duration_s,
                    pass_id=task.pass_id,
                    job_id=task.job_id,
                    error_category="internal",
                    error_message=f"Pass execution returned None for pass_id={task.pass_id}",
                )
            return TaskResult(
                status="ok",
                duration_s=duration_s,
                pass_id=task.pass_id,
                job_id=task.job_id,
                payload=payload,
            )
        except Exception as exc:
            category = self._classify_error(exc)
            logger.exception("run_task failed: pass=%s job=%s", task.pass_id, task.job_id)
            return TaskResult(
                status="failed",
                duration_s=time.monotonic() - started,
                pass_id=task.pass_id,
                job_id=task.job_id,
                error_category=category,
                error_message=str(exc)[:500],
            )

    def process_job(
        self,
        analysis_job_id: int,
        *,
        worker_id: str,
        force_job_level: bool = False,
    ) -> JobResult:
        raise NotImplementedError("Implemented in Task 4")

    def aggregate_results(self, analysis_job_id: int) -> dict:
        raise NotImplementedError("Implemented in Task 4")

    def shutdown(self) -> None:
        """Disconnect DB, free CUDA cache. Idempotent."""
        if self._shutdown_called:
            return
        self._shutdown_called = True
        try:
            self.db.disconnect()
        except Exception as exc:
            logger.warning("Engine DB disconnect error: %s", exc)
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _classify_error(self, exc: Exception) -> str:
        # OOM — torch's specific class first if available, then MemoryError fallback
        oom_types: tuple = (MemoryError,)
        try:
            import torch

            if hasattr(torch.cuda, "OutOfMemoryError"):
                oom_types = (torch.cuda.OutOfMemoryError, MemoryError)
        except ImportError:
            pass
        if isinstance(exc, oom_types):
            return "oom"
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, (KeyError, ValueError)) and "config" in str(exc).lower():
            return "config"
        if isinstance(exc, (KeyError, ValueError)):
            return "data"
        return "internal"

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

    # ------------------------------------------------------------------
    # Pass execution
    # ------------------------------------------------------------------

    def _execute_pass(
        self,
        task: AnalysisTask,
        context: JobContext,
        *,
        worker_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute the LLM pass on the chunk.

        Chunk-level:
        - chunk_analysis is wired to OllamaAnalyzer.analyze_chunk
        - sentiment_pass / categories_pass are lightweight heuristic passes
        - subchunks splits the chunk into faux subchunks

        Job-level passes (aggregate_results, hot_targets, drills, db_store,
        local_json, markdown_report) are dispatched to ``_handle_job_level_pass``.
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

        if not chunk_text.strip() and pass_id in (
            "chunk_analysis",
            "sentiment_pass",
            "categories_pass",
            "subchunks",
        ):
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
            result = self._handle_job_level_pass(
                task, pass_id, chunk_text, chunk_metadata, context
            )
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
        analysis: Dict[str, Any] = {}
        if isinstance(task.result_json, dict):
            analysis = task.result_json.get("analysis") or task.result_json
        self._ensure_chunk_defaults(analysis, task.chunk_text or "")
        context.chunk_results[chunk_idx] = analysis
        context.chunk_texts[chunk_idx] = task.chunk_text or ""
        context.chunk_metadata_map[chunk_idx] = task.chunk_metadata or {}

    def _load_all_chunk_texts(self, context: JobContext) -> None:
        """
        Ensure context.chunk_texts is populated for all chunks in the job.
        Useful for job-level passes like hot_targets that need to scan the whole video.
        """
        # If we already have enough chunks, skip DB query
        if len(context.chunk_texts) >= context.total_chunks:
            return

        tasks = self.task_repo.get_job_tasks(context.job_id)
        for t in tasks:
            if not t.chunk_text:
                continue

            try:
                cid = int(t.chunk_id)
            except (ValueError, TypeError):
                continue

            # Prioritize chunk-level tasks (usually pass_id='chunk_analysis') for the text source
            # as they are guaranteed to correspond to that specific chunk.
            # However, any task with matching chunk_id should theoretically have the same text.
            if cid not in context.chunk_texts:
                context.chunk_texts[cid] = t.chunk_text
            elif t.pass_id == "chunk_analysis":
                # Overwrite with authoritative source if available
                context.chunk_texts[cid] = t.chunk_text

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
        categories: List[str] = []
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

    # ------------------------------------------------------------------
    # Heuristic helpers (used by sentiment/categories/subchunks passes)
    # ------------------------------------------------------------------

    def _generate_subchunks(self, chunk_text: str) -> List[Dict[str, Any]]:
        # Convert chunk into faux subchunks by splitting sentences
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

    # ------------------------------------------------------------------
    # Job-level passes
    # ------------------------------------------------------------------

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
                self._load_all_chunk_texts(context)

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
                detections, llm_spans = self._detect_hot_targets(
                    chunk_text, context.config, chunk_id=int(task.chunk_id)
                )
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
                self._load_all_chunk_texts(context)
            else:
                # Legacy: only run on this specific chunk
                # Also ensure we have the text for this chunk (should be hydrated but safe to check)
                cid = int(task.chunk_id)
                if cid not in context.chunk_texts:
                    context.chunk_texts[cid] = task.chunk_text or ""
                chunk_filter = {cid}

            drill_results, drill_summary, emitted_spans = self._run_drill_executor(
                context, chunk_filter=chunk_filter
            )
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

    def _run_drill_executor(
        self,
        context: JobContext,
        chunk_filter: Optional[Set[int]] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
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
        for key in (
            "tldr_one_sentence",
            "summary_paragraph",
            "political_overview",
            "controversies",
            "speaker_overview",
        ):
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

    def _detect_hot_targets(
        self,
        chunk_text: str,
        config: AnalysisConfig,
        chunk_id: int,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
            spec_dict = (
                spec.model_dump()
                if hasattr(spec, "model_dump")
                else spec.dict()
                if hasattr(spec, "dict")
                else spec
                if isinstance(spec, dict)
                else {}
            )
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
            logger.error(
                "DB storage adapter missing store_full_analysis_with_chunks; cannot persist analysis for %s",
                context.job_id,
            )
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
            logger.error(
                "Failed to store full analysis for job %s (ytid=%s): %s",
                context.job_id,
                context.ytid,
                exc,
                exc_info=True,
            )
            return False

    def _build_topic_person_spans(
        self,
        chunk_analyses: List[Dict[str, Any]],
        ytid: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
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
            topic_spans.append(
                {
                    "ytid": ytid,
                    "topic": norm_topic.title(),
                    "normalized_topic": norm_topic,
                    "chunk_ids": sorted(chunk_ids),
                    "context": context_snippet,
                    "sentiment": sentiment if sentiment and sentiment != "unknown" else None,
                    "source_pass": "chunk_analysis",
                }
            )

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
            person_spans.append(
                {
                    "ytid": ytid,
                    "person_name": norm_name.title(),
                    "normalized_name": norm_name,
                    "chunk_ids": sorted(chunk_ids),
                    "context": context_snippet,
                    "sentiment": sentiment if sentiment and sentiment != "unknown" else None,
                    "polarity": None,
                    "source_pass": "chunk_analysis",
                }
            )

        return {"topic_spans": topic_spans, "person_spans": person_spans}

    def _write_local_artifacts(
        self,
        context: JobContext,
        aggregated: Dict[str, Any],
        pass_id: str,
    ) -> Dict[str, str]:
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

    def _excerpt_for_matches(self, text: str, keyword: str) -> str:
        if not keyword:
            return text[:160]
        idx = text.lower().find(keyword.lower())
        if idx == -1:
            return text[:160]
        start = max(0, idx - 60)
        end = min(len(text), idx + len(keyword) + 60)
        return text[start:end].strip()
