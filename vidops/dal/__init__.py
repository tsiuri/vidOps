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

__all__ = [
    "VideoRepository",
    "JobRepository",
    "WorkerRepository",
    "TranscriptRepository",
    "WordRepository",
    "FilesystemCache",
]
