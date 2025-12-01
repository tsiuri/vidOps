# vidops/workers/__init__.py

"""
vidops.workers

This package contains the worker processes that claim and execute jobs
from the database queues.
"""

from .download import DownloadWorker
from .transcription import TranscriptionWorker
from .clipping import ClippingWorker
from .analysis import AnalysisWorker
from .diarization import DiarizeWorker
from .stitching import StitchWorker
from .subtitle import SubtitleWorker # Import the new SubtitleWorker
from .voice import VoiceFilterWorker
from .general import GenericWorker
from .dates import DatesWorker
from .extra_utils import ExtraUtilsWorker

__all__ = [
    "GenericWorker",
    "DownloadWorker",
    "TranscriptionWorker",
    "ClippingWorker",
    "AnalysisWorker",
    "DiarizeWorker",
    "StitchWorker",
    "SubtitleWorker", # Export the SubtitleWorker
    "VoiceFilterWorker",
    "DatesWorker",
    "ExtraUtilsWorker",
]
