"""
Stateful analysis engine. Owns the OllamaAnalyzer, HotTargetRunner, AnalysisDatabase,
and per-job context cache. Public surface: run_task, process_job, aggregate_results,
shutdown. Caller (worker or queue bridge) is responsible for the claim loop, status
updates, metrics emission, and signal handling.

See spec: ~/projects/vidops_cleanup_2026/2026-05-06-analysis-engine-extraction-design.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from configuration import load_config
from dal import AnalysisResultsRepository
from dal.analysis_task_repository import AnalysisDatabase, AnalysisTaskRepository
from models import AnalysisTask
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
    """Per-(analysis_job_id) cache for chunk text, config, drills, results-so-far."""

    analysis_job_id: int
    config: Optional[Any] = None
    chunk_results: Dict[int, dict] = field(default_factory=dict)
    chunk_texts: Dict[int, str] = field(default_factory=dict)
    drills: List[Any] = field(default_factory=list)
    aggregate: Optional[dict] = None


@dataclass
class TaskResult:
    """Engine returns this from run_task. Worker uses it to update task row + emit metrics."""

    status: Literal["ok", "failed", "skipped"]
    duration_s: float
    pass_id: str
    analysis_job_id: int
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    payload: Optional[dict] = None


@dataclass
class JobResult:
    """Engine returns this from process_job (one-shot full-job execution from the bridge)."""

    analysis_job_id: int
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

        self.job_contexts: Dict[int, JobContext] = {}
        self._shutdown_called = False

    def run_task(self, task: AnalysisTask, *, worker_id: str) -> TaskResult:
        raise NotImplementedError("Implemented in Task 2")

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
