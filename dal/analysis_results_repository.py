"""Compatibility wrapper for the scripts.analysis results repository."""

from __future__ import annotations

from dal.analysis_task_repository import AnalysisDatabase
from scripts.analysis.persistence.analysis_results_repository import (
    AnalysisResultsRepository as _BaseAnalysisResultsRepository,
)


class AnalysisResultsRepository(_BaseAnalysisResultsRepository):
    """Alias to the canonical scripts.analysis implementation."""

    pass


__all__ = ["AnalysisDatabase", "AnalysisResultsRepository"]
