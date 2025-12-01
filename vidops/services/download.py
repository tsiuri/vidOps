# vidops/services/download.py

import logging
import yt_dlp
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import date

from vidops.dal import VideoRepository, JobRepository, FilesystemCache
from vidops.models import Video, Job, JobStatus
from vidops.config import load_config

logger = logging.getLogger(__name__)

class DownloadService:
    """
    Orchestrates video downloads using yt-dlp.
    Downloads videos and registers them in the database.
    """

    def __init__(
        self,
        video_repo: VideoRepository,
        job_repo: JobRepository,
        fs_cache: FilesystemCache
    ):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.fs_cache = fs_cache
        self.config = load_config()

    def enqueue_download(self, url: str, priority: int = 0) -> Job:
        """
        Enqueues a video download job.

        Args:
            url: YouTube video URL
            priority: Job priority

        Returns:
            Created Job object
        """
        # Extract ytid from URL
        ytid = self._extract_ytid(url)

        # Create download job
        job = Job(
            job_type="download",
            ytid=ytid,
            media_path=url,
            config={"url": url},
            priority=priority,
            status=JobStatus.PENDING
        )

        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Processes a download job by downloading the video with yt-dlp.

        Args:
            job: The download job to process
        """
        url = job.config.get('url') or job.media_path

        if not url:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "No URL provided")
            return

        try:
            # Update status to RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING)

            logger.info(f"Downloading video from {url}")

            # Download into local cache first (avoids relying on mounted storage)
            download_dir = self.fs_cache.ensure_local_dir("downloads/raw")
            logger.info("Download staging dir: %s", download_dir)

            # yt-dlp options
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': str(download_dir / '%(id)s__%(upload_date)s - %(title)s.%(ext)s'),
                'writeinfojson': True,
                'quiet': False,
                'no_warnings': False,
            }

            # Download and extract info
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                logger.info("yt-dlp finished for %s (id=%s)", url, info.get("id") if info else "unknown")

                if not info:
                    raise RuntimeError("yt-dlp returned no video info")

                # Parse upload_date (YYYYMMDD format)
                upload_date_str = info.get('upload_date')
                upload_date = None
                if upload_date_str and len(upload_date_str) == 8:
                    upload_date = date(
                        int(upload_date_str[0:4]),
                        int(upload_date_str[4:6]),
                        int(upload_date_str[6:8])
                    )

                # Create Video object
                video = Video(
                    ytid=info['id'],
                    url=info.get('webpage_url') or url,
                    title=info.get('title'),
                    upload_date=upload_date,
                    duration_sec=info.get('duration'),
                    channel=info.get('uploader') or info.get('channel'),
                    channel_id=info.get('channel_id') or info.get('uploader_id'),
                    extractor_key=info.get('extractor_key'),
                    tags=info.get('tags') or [],
                    categories=info.get('categories') or []
                )

                # Upsert video to database
                self.video_repo.upsert(video)

                # Persist downloaded file into central storage (or broker)
                downloaded_file = Path(ydl.prepare_filename(info))
                relative_path = str(Path("raw") / downloaded_file.name)
                logger.info("Uploading media to storage/broker: %s -> %s", downloaded_file, relative_path)
                stored_path = self.fs_cache.persist_local_artifact(
                    local_path=downloaded_file,
                    relative_path=relative_path,
                    video_repo=self.video_repo,
                    ytid=info['id'],
                    kind='media'
                )
                logger.info("Media persisted at %s", stored_path)

                # Update job status to COMPLETED with result data
                result = {
                    "ytid": info['id'],
                    "relative_path": relative_path,
                    "title": info.get('title'),
                    "duration_sec": info.get('duration'),
                    "filesize_bytes": downloaded_file.stat().st_size if downloaded_file.exists() else None
                }

                self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=result)
                logger.info(f"Successfully downloaded {info['id']}: {info.get('title')}")

        except Exception as e:
            error_msg = f"Download failed for job {job.job_id}: {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    def _extract_ytid(self, url: str) -> str:
        """
        Extracts YouTube video ID from URL.

        Args:
            url: YouTube URL

        Returns:
            YouTube video ID
        """
        # Handle various YouTube URL formats
        if "v=" in url:
            return url.split('v=')[-1].split('&')[0]
        elif "youtu.be/" in url:
            return url.split('youtu.be/')[-1].split('?')[0]
        else:
            # Assume it's already a video ID
            return url
