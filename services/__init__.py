# vidops/services/__init__.py

"""
vidops.services

This package handles the business logic and orchestrates interactions
between the CLI, DAL, and workers.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Optional

from dal import (
    VideoRepository,
    JobRepository,
    TranscriptRepository,
    WordRepository,
    FilesystemCache,
    WorkerRepository,
)
from configuration import load_config

if TYPE_CHECKING:
    from .transcription import TranscriptionService
    from .clipping import ClippingService
    from .download import DownloadService
    from .overlord import OverlordService
    from .analysis import AnalysisService
    from .distributed_analysis import DistributedAnalysisService
    from .voice_filter import VoiceFilterService
    from .diarization import DiarizationService
    from .stitching import StitchingService
    from .subtitle import SubtitleService
    from .dates import DatesService
    from .extra_utils import ExtraUtilsService
    from .hc_project import HitsClipsProjectService
    from .hc_clip_render import HCClipRenderService
    from .analysis_engine import AnalysisEngine

__all__ = [
    "TranscriptionService",
    "ClippingService",
    "DownloadService",
    "OverlordService",
    "AnalysisService",
    "DistributedAnalysisService",
    "DiarizationService",
    "StitchingService",
    "SubtitleService",
    "VoiceFilterService",
    "DatesService",
    "ExtraUtilsService",
    "HitsClipsProjectService",
    "HCClipRenderService",
    "get_hc_project_service",
    "get_hc_clip_render_service",
    "get_download_service",
    "get_transcription_service",
    "get_clipping_service",
    "get_analysis_service",
    "get_diarization_service",
    "get_stitching_service",
    "get_subtitle_service",
    "get_overlord_service",
    "get_voice_service",
    "get_dates_service",
    "get_extra_utils_service",
    "get_distributed_analysis_service",
    "get_analysis_engine",
]

_SERVICE_IMPORTS = {
    "TranscriptionService": (".transcription", "TranscriptionService"),
    "ClippingService": (".clipping", "ClippingService"),
    "DownloadService": (".download", "DownloadService"),
    "OverlordService": (".overlord", "OverlordService"),
    "AnalysisService": (".analysis", "AnalysisService"),
    "DistributedAnalysisService": (".distributed_analysis", "DistributedAnalysisService"),
    "VoiceFilterService": (".voice_filter", "VoiceFilterService"),
    "DiarizationService": (".diarization", "DiarizationService"),
    "StitchingService": (".stitching", "StitchingService"),
    "SubtitleService": (".subtitle", "SubtitleService"),
    "DatesService": (".dates", "DatesService"),
    "ExtraUtilsService": (".extra_utils", "ExtraUtilsService"),
    "HitsClipsProjectService": (".hc_project", "HitsClipsProjectService"),
    "HCClipRenderService": (".hc_clip_render", "HCClipRenderService"),
}


def __getattr__(name: str):
    target = _SERVICE_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__} has no attribute {name}")
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + list(_SERVICE_IMPORTS.keys())))


def get_hc_project_service() -> "HitsClipsProjectService":
    """Factory for the Hits & Clips Project runner service."""
    from .hc_project import HitsClipsProjectService

    return HitsClipsProjectService()


def get_hc_clip_render_service() -> "HCClipRenderService":
    """Factory for explicit HC clip rendering/rebuild service."""
    from .hc_clip_render import HCClipRenderService

    return HCClipRenderService()

# Helper function for service instantiation
def get_download_service() -> "DownloadService":
    """
    Returns a configured instance of DownloadService with its dependencies.
    """
    from .download import DownloadService

    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    fs_cache = FilesystemCache()

    return DownloadService(
        video_repo=video_repo,
        job_repo=job_repo,
        fs_cache=fs_cache
    )

def get_transcription_service() -> "TranscriptionService":
    """
    Returns a configured instance of TranscriptionService with its dependencies.
    """
    from .transcription import TranscriptionService

    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    transcript_repo = TranscriptRepository()
    word_repo = WordRepository()
    fs_cache = FilesystemCache()

    return TranscriptionService(
        video_repo=video_repo,
        job_repo=job_repo,
        transcript_repo=transcript_repo,
        word_repo=word_repo,
        fs_cache=fs_cache
    )

def get_clipping_service() -> "ClippingService":
    """
    Returns a configured instance of ClippingService with its dependencies.
    """
    from .clipping import ClippingService

    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    fs_cache = FilesystemCache()

    return ClippingService(
        video_repo=video_repo,
        job_repo=job_repo,
        fs_cache=fs_cache
    )

def get_analysis_service() -> "AnalysisService":
    """
    Returns a configured instance of AnalysisService with its dependencies.
    """
    from .analysis import AnalysisService

    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    transcript_repo = TranscriptRepository()
    word_repo = WordRepository()
    fs_cache = FilesystemCache()

    return AnalysisService(
        video_repo=video_repo,
        job_repo=job_repo,
        transcript_repo=transcript_repo,
        word_repo=word_repo,
        fs_cache=fs_cache
    )

def get_diarization_service() -> "DiarizationService":
    """
    Returns a configured instance of DiarizationService with its dependencies.
    """
    from .diarization import DiarizationService

    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    transcript_repo = TranscriptRepository()
    fs_cache = FilesystemCache()

    return DiarizationService(
        video_repo=video_repo,
        job_repo=job_repo,
        transcript_repo=transcript_repo,
        fs_cache=fs_cache
    )

def get_stitching_service() -> "StitchingService":
    """
    Returns a configured instance of StitchingService with its dependencies.
    """
    from .stitching import StitchingService

    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    fs_cache = FilesystemCache()

    return StitchingService(
        video_repo=video_repo,
        job_repo=job_repo,
        fs_cache=fs_cache
    )

def get_subtitle_service() -> "SubtitleService":
    """
    Returns a configured instance of SubtitleService with its dependencies.
    """
    from .subtitle import SubtitleService

    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    transcript_repo = TranscriptRepository()
    word_repo = WordRepository()
    fs_cache = FilesystemCache()

    return SubtitleService(
        video_repo=video_repo,
        job_repo=job_repo,
        transcript_repo=transcript_repo,
        word_repo=word_repo,
        fs_cache=fs_cache
    )

def get_overlord_service() -> "OverlordService":
    """
    Returns a configured instance of OverlordService with its dependencies.
    """
    from .overlord import OverlordService

    return OverlordService(
        job_repo=JobRepository(),  # Uses generic 'jobs' table for all job types
        worker_repo=WorkerRepository(),  # Uses generic 'workers' table
        analysis_service=get_analysis_service(),
    )

def get_voice_service() -> "VoiceFilterService":
    """
    Returns a configured instance of VoiceFilterService with its dependencies.
    """
    from .voice_filter import VoiceFilterService

    video_repo = VideoRepository()
    job_repo = JobRepository()
    fs_cache = FilesystemCache()

    return VoiceFilterService(
        video_repo=video_repo,
        job_repo=job_repo,
        fs_cache=fs_cache
    )

def get_dates_service() -> "DatesService":
    from .dates import DatesService

    video_repo = VideoRepository()
    job_repo = JobRepository()
    fs_cache = FilesystemCache()
    return DatesService(video_repo=video_repo, job_repo=job_repo, fs_cache=fs_cache)


def get_extra_utils_service() -> "ExtraUtilsService":
    from .extra_utils import ExtraUtilsService

    video_repo = VideoRepository()
    job_repo = JobRepository()
    fs_cache = FilesystemCache()
    return ExtraUtilsService(video_repo=video_repo, job_repo=job_repo, fs_cache=fs_cache)


def get_distributed_analysis_service() -> "DistributedAnalysisService":
    """
    Returns a configured instance of DistributedAnalysisService with its dependencies.
    Uses config to get Ollama URL and model name.
    Reads runtime VRAM/profile from environment if worker set them (e.g., via --gpu flag).
    """
    from .distributed_analysis import DistributedAnalysisService

    import os
    config = load_config()
    job_repo = JobRepository()

    # Read from environment if worker set runtime values (e.g., --gpu flag)
    vram_gb = float(os.environ.get("VIDOPS_WORKER_VRAM_GB", config.analysis.default_vram_gb or 0))
    model_url = os.environ.get("VIDOPS_WORKER_MODEL_URL", config.analysis.ollama.url)
    model_name = os.environ.get("VIDOPS_WORKER_MODEL_NAME", config.analysis.ollama.model)

    return DistributedAnalysisService(
        job_repo=job_repo,
        db_host=config.database.host,
        db_name=config.database.name,
        db_user=config.database.user,
        db_password=config.database.password,
        model_url=model_url,
        model_name=model_name,
        available_vram_gb=vram_gb,
        model_profile_id=None,  # Worker accepts any profile; tasks define their own requirements
        machine_alias=config.workers.machine_alias,
    )


def get_analysis_engine(
    *,
    model_name: str,
    model_url: str,
    model_profile_id: Optional[int],
    ollama_options: Optional[dict] = None,
) -> "AnalysisEngine":
    """
    Returns a configured AnalysisEngine instance (stateful, owns DB + analyzer).
    Caller is responsible for the claim loop and shutdown.
    """
    from .analysis_engine import AnalysisEngine

    return AnalysisEngine(
        model_name=model_name,
        model_url=model_url,
        model_profile_id=model_profile_id,
        ollama_options=ollama_options,
    )
