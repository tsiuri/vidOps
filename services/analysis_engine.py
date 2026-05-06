"""
Stateful analysis engine. Owns the OllamaAnalyzer, HotTargetRunner, AnalysisDatabase,
and per-job context cache. Public surface: run_task, process_job, aggregate_results,
shutdown. Caller (worker or queue bridge) is responsible for the claim loop, status
updates, metrics emission, and signal handling.

See spec: ~/projects/vidops_cleanup_2026/2026-05-06-analysis-engine-extraction-design.md
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from configuration import load_config
from dal import AnalysisResultsRepository
from dal.analysis_task_repository import AnalysisDatabase, AnalysisTaskRepository
from models import AnalysisTask
from scripts.analysis.analysis_config import AnalysisConfig, Drill
from scripts.analysis.analyze_transcript import OllamaAnalyzer
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
    ) -> None:
        self.config = load_config()
        self.model_name = model_name
        self.model_url = model_url
        self.model_profile_id = model_profile_id
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
        name = type(exc).__name__
        if name in ("OutOfMemoryError", "CUDAOutOfMemoryError"):
            return "oom"
        if name in ("TimeoutError", "ConnectionTimeout"):
            return "timeout"
        msg = str(exc).lower()
        if name in ("KeyError", "ValueError") and "config" in msg:
            return "config"
        if name in ("KeyError", "ValueError"):
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

        Currently:
        - chunk_analysis is wired to OllamaAnalyzer.analyze_chunk
        - sentiment_pass and categories_pass are lightweight stubs
        - other passes are simple placeholders

        Job-level passes (aggregate_results, hot_targets, drills, db_store,
        local_json, markdown_report) are not yet handled by the engine; they
        remain on the worker until Task 3 / Task 4. For those, this method
        raises NotImplementedError so callers know to fall back.
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
            # Job-level passes are still owned by the worker (Task 3 / Task 4).
            raise NotImplementedError(
                f"Job-level pass '{pass_id}' is not yet handled by AnalysisEngine"
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
