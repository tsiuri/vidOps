# vidops/dal/__init__.py

"""
vidops.dal (Data Access Layer)

This package contains Repository classes that provide an abstraction
layer for all database interactions. Services should use these
repositories to interact with the database instead of writing
raw SQL queries.
"""

from .videos import VideoRepository
from .jobs import JobRepository
from .workers import WorkerRepository
from .transcripts import TranscriptRepository, WordRepository
from .cache import FilesystemCache
from .hits import HitsRepository
from .quickclip import QuickClipRepository
from .analysis_task_repository import AnalysisTaskRepository
from .analysis_results_repository import AnalysisResultsRepository

__all__ = [
    "VideoRepository",
    "JobRepository",
    "WorkerRepository",
    "TranscriptRepository",
    "WordRepository",
    "FilesystemCache",
    "HitsRepository",
    "QuickClipRepository",
    "AnalysisTaskRepository",
    "AnalysisResultsRepository",
]
