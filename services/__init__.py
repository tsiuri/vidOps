# vidops/services/__init__.py

"""
vidops.services

This package handles the business logic and orchestrates interactions
between the CLI, DAL, and workers.
"""

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
from dal import VideoRepository, JobRepository, TranscriptRepository, WordRepository, FilesystemCache, WorkerRepository
from configuration import load_config

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
]

# Helper function for service instantiation
def get_download_service() -> DownloadService:
    """
    Returns a configured instance of DownloadService with its dependencies.
    """
    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    fs_cache = FilesystemCache()

    return DownloadService(
        video_repo=video_repo,
        job_repo=job_repo,
        fs_cache=fs_cache
    )

def get_transcription_service() -> TranscriptionService:
    """
    Returns a configured instance of TranscriptionService with its dependencies.
    """
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

def get_clipping_service() -> ClippingService:
    """
    Returns a configured instance of ClippingService with its dependencies.
    """
    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    fs_cache = FilesystemCache()

    return ClippingService(
        video_repo=video_repo,
        job_repo=job_repo,
        fs_cache=fs_cache
    )

def get_analysis_service() -> AnalysisService:
    """
    Returns a configured instance of AnalysisService with its dependencies.
    """
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

def get_diarization_service() -> DiarizationService:
    """
    Returns a configured instance of DiarizationService with its dependencies.
    """
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

def get_stitching_service() -> StitchingService:
    """
    Returns a configured instance of StitchingService with its dependencies.
    """
    video_repo = VideoRepository()
    job_repo = JobRepository()  # Uses generic 'jobs' table
    fs_cache = FilesystemCache()

    return StitchingService(
        video_repo=video_repo,
        job_repo=job_repo,
        fs_cache=fs_cache
    )

def get_subtitle_service() -> SubtitleService:
    """
    Returns a configured instance of SubtitleService with its dependencies.
    """
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

def get_overlord_service() -> OverlordService:
    """
    Returns a configured instance of OverlordService with its dependencies.
    """
    return OverlordService(
        job_repo=JobRepository(),  # Uses generic 'jobs' table for all job types
        worker_repo=WorkerRepository(),  # Uses generic 'workers' table
        analysis_service=get_analysis_service(),
    )

def get_voice_service() -> VoiceFilterService:
    """
    Returns a configured instance of VoiceFilterService with its dependencies.
    """
    video_repo = VideoRepository()
    job_repo = JobRepository()
    fs_cache = FilesystemCache()

    return VoiceFilterService(
        video_repo=video_repo,
        job_repo=job_repo,
        fs_cache=fs_cache
    )

def get_dates_service() -> DatesService:
    video_repo = VideoRepository()
    job_repo = JobRepository()
    fs_cache = FilesystemCache()
    return DatesService(video_repo=video_repo, job_repo=job_repo, fs_cache=fs_cache)


def get_extra_utils_service() -> ExtraUtilsService:
    video_repo = VideoRepository()
    job_repo = JobRepository()
    fs_cache = FilesystemCache()
    return ExtraUtilsService(video_repo=video_repo, job_repo=job_repo, fs_cache=fs_cache)


def get_distributed_analysis_service() -> DistributedAnalysisService:
    """
    Returns a configured instance of DistributedAnalysisService with its dependencies.
    Uses config to get Ollama URL and model name.
    """
    config = load_config()
    job_repo = JobRepository()

    return DistributedAnalysisService(
        job_repo=job_repo,
        db_host=config.database.host,
        db_name=config.database.name,
        db_user=config.database.user,
        db_password=config.database.password,
        model_url=config.analysis.ollama.url,
        model_name=config.analysis.ollama.model,
        capabilities=list(config.analysis.default_capabilities or []),
        machine_alias=config.workers.machine_alias,
    )
