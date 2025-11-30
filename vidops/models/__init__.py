# vidops/models/__init__.py

"""
vidops.models

This package contains the data models for the VidOps application.
These models are implemented as dataclasses and represent the core
entities of the system, such as Videos, Jobs, Workers, and Transcripts.

They are designed to map directly to database tables and provide
methods for serialization and deserialization (e.g., `from_row`, `to_dict`).
"""

from .job import Job, JobStatus
from .video import Video, Asset
from .worker import Worker, WorkerStatus
from .transcript import Transcript, Word

__all__ = [
    "Job",
    "JobStatus",
    "Video",
    "Asset",
    "Worker",
    "WorkerStatus",
    "Transcript",
    "Word",
]
