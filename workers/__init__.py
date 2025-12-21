# vidops/workers/__init__.py

"""
vidops.workers

This package contains the worker processes that claim and execute jobs
from the database queues.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .analysis import AnalysisWorker
    from .analysis_distributed import AnalysisWorker as DistributedAnalysisWorker
    from .clipping import ClippingWorker
    from .dates import DatesWorker
    from .diarization import DiarizeWorker
    from .download import DownloadWorker
    from .extra_utils import ExtraUtilsWorker
    from .general import GenericWorker
    from .stitching import StitchWorker
    from .subtitle import SubtitleWorker
    from .transcription import TranscriptionWorker
    from .voice import VoiceFilterWorker

__all__ = [
    "GenericWorker",
    "DownloadWorker",
    "TranscriptionWorker",
    "ClippingWorker",
    "AnalysisWorker",
    "DistributedAnalysisWorker",
    "DiarizeWorker",
    "StitchWorker",
    "SubtitleWorker",
    "VoiceFilterWorker",
    "DatesWorker",
    "ExtraUtilsWorker",
]

_WORKER_IMPORTS = {
    "GenericWorker": (".general", "GenericWorker"),
    "DownloadWorker": (".download", "DownloadWorker"),
    "TranscriptionWorker": (".transcription", "TranscriptionWorker"),
    "ClippingWorker": (".clipping", "ClippingWorker"),
    "AnalysisWorker": (".analysis", "AnalysisWorker"),
    "DistributedAnalysisWorker": (".analysis_distributed", "AnalysisWorker"),
    "DiarizeWorker": (".diarization", "DiarizeWorker"),
    "StitchWorker": (".stitching", "StitchWorker"),
    "SubtitleWorker": (".subtitle", "SubtitleWorker"),
    "VoiceFilterWorker": (".voice", "VoiceFilterWorker"),
    "DatesWorker": (".dates", "DatesWorker"),
    "ExtraUtilsWorker": (".extra_utils", "ExtraUtilsWorker"),
}


def __getattr__(name: str):
    target = _WORKER_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__} has no attribute {name}")
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + list(_WORKER_IMPORTS.keys())))
