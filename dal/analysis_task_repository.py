"""VidOps wrappers around the scripts.analysis task repository.

The core distributed analysis implementation now lives under scripts/analysis.
This module keeps the existing import paths working while ensuring defaults are
loaded from the VidOps configuration.
"""

from __future__ import annotations

from typing import Optional

from configuration import load_config
from scripts.analysis.analysis_task import AnalysisTask, TaskStatus
from scripts.analysis.analysis_task_repository import AnalysisTaskRepository as _BaseRepository
from scripts.analysis.db_storage import AnalysisDatabase as _BaseDatabase


class AnalysisDatabase(_BaseDatabase):
    """Configuration-aware database wrapper for distributed analysis tables."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        dbname: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        config = load_config()
        super().__init__(
            dbname=dbname or config.database.name,
            host=host or config.database.host,
            port=port or config.database.port,
            user=user or config.database.user,
            password=password or config.database.password,
        )


class AnalysisTaskRepository(_BaseRepository):
    """Alias to the canonical scripts.analysis repository class."""

    pass


__all__ = [
    "AnalysisDatabase",
    "AnalysisTaskRepository",
    "AnalysisTask",
    "TaskStatus",
]
