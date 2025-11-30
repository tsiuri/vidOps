# vidops/services/subtitle.py

import logging
from typing import List, Optional
from datetime import timedelta

from vidops.dal import VideoRepository, JobRepository, TranscriptRepository, FilesystemCache
from vidops.models import Video, Job, JobStatus, Transcript

logger = logging.getLogger(__name__)

class SubtitleService:
    """
    Orchestrates the subtitle download and processing process, managing job creation,
    dispatch, and result handling.
    """

    def __init__(
        self,
        video_repo: VideoRepository,
        job_repo: JobRepository,
        transcript_repo: TranscriptRepository,
        fs_cache: FilesystemCache
    ):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.transcript_repo = transcript_repo
        self.fs_cache = fs_cache

    def enqueue_subtitle_download_job(
        self,
        ytid: str,
        lang: str = "en",
        format: str = "vtt", # e.g., vtt, srt
        priority: int = 0
    ) -> Job:
        """
        Enqueues a job to download subtitles for a given video.

        Args:
            ytid: YouTube ID of the video.
            lang: Language of the subtitles to download.
            format: Format of the subtitles (e.g., 'vtt', 'srt').
            priority: Job priority.

        Returns:
            The created Job object.

        Raises:
            ValueError: If the video is not found.
        """
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")
        
        # Create the job configuration
        config = {
            "ytid": ytid,
            "lang": lang,
            "format": format,
            "media_path_hint": video.url # Hint for where to find the media
        }

        # Create the job in the database
        job = Job(
            job_type="subtitle_download",
            ytid=ytid,
            media_path=video.url, # Original media source for context
            config=config,
            priority=priority,
            status=JobStatus.PENDING
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Processes a single subtitle download job claimed by a worker.
        """
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid.")
            return

        try:
            # 1. Update job status to RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting subtitle download.")

            # 2. Execute subtitle download (placeholder)
            logger.info(f"Downloading {job.config.get('lang')} subtitles for {job.ytid} in {job.config.get('format')} format...")
            
            # --- Placeholder for actual subtitle download logic (e.g., using yt-dlp) ---
            # For now, simulate success
            downloaded_subtitle_content = f"WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n[Simulated subtitles for {job.ytid}]"
            downloaded_subtitle_path = f"/tmp/subs/{job.ytid}.{job.config['lang']}.{job.config['format']}"
            
            # Simulate saving subtitle file
            # Path(downloaded_subtitle_path).parent.mkdir(parents=True, exist_ok=True)
            # Path(downloaded_subtitle_path).write_text(downloaded_subtitle_content, encoding='utf-8')
            
            logger.debug(f"Simulated subtitle output: {downloaded_subtitle_path}")
            # --- End Placeholder ---

            # 3. Store subtitle metadata in database
            transcript = Transcript(
                ytid=job.ytid,
                kind=f"yt_auto_{job.config['format']}", # e.g., 'yt_auto_vtt'
                lang=job.config['lang'],
                path=downloaded_subtitle_path, # Path in local cache/central storage
                word_count=len(downloaded_subtitle_content.split()), # Simplified
                segment_count=1 # Simplified
            )
            self.transcript_repo.upsert(transcript)

            # 4. Update job status to COMPLETED
            job_result = {
                "output_subtitle_path": downloaded_subtitle_path,
                "lang": job.config['lang']
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info(f"Successfully downloaded subtitles for {job.ytid} to {downloaded_subtitle_path}.")

        except Exception as e:
            error_msg = f"Subtitle download failed for job {job.job_id} ({job.ytid}): {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)
