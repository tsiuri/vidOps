# vidops/services/clipping.py

import logging
import math
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from vidops.dal import VideoRepository, JobRepository, FilesystemCache
from vidops.models import Job, JobStatus

logger = logging.getLogger(__name__)

class ClippingService:
    """
    Orchestrates the video clipping process, managing job creation,
    dispatch, and result handling.
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

    def enqueue_clip_job(
        self,
        ytid: str,
        start_sec: float,
        end_sec: float,
        label: str,
        priority: int = 0,
        media_relative_path: Optional[str] = None,
        transcript_source: Optional[str] = None
    ) -> Job:
        """
        Enqueues a single video clipping job.

        Args:
            ytid: YouTube ID of the video to clip.
            start_sec: Start time of the clip in seconds.
            end_sec: End time of the clip in seconds.
            label: A label for the clip (e.g., search term).
            priority: Job priority.

        Returns:
            The created Job object.

        Raises:
            ValueError: If the video is not found.
        """
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")

        media_rel = media_relative_path or self._resolve_media_asset_path(ytid)
        if not media_rel:
            raise ValueError(f"No stored media asset for video '{ytid}'. Download it first.")

        # Create the job configuration
        config = {
            "ytid": ytid,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "label": label,
            "media_asset_path": media_rel,
            "transcript_source": transcript_source,
            "output_relative_path": self._build_clip_relative_path(ytid, label, start_sec, end_sec)
        }

        # Create the job in the database
        job = Job(
            job_type="clipping",
            ytid=ytid,
            media_path=media_rel,
            config=config,
            priority=priority,
            status=JobStatus.PENDING
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Processes a single clipping job claimed by a worker.
        """
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid.")
            return

        try:
            media_relative = job.config.get("media_asset_path") or job.media_path
            if not media_relative:
                media_relative = self._resolve_media_asset_path(job.ytid)
            if not media_relative:
                raise FileNotFoundError(f"No media asset registered for {job.ytid}")

            central_source = self.fs_cache.get_central_path(media_relative)
            if not central_source.exists():
                raise FileNotFoundError(f"Media asset missing from storage: {central_source}")

            local_source = self.fs_cache.pull_to_cache(media_relative)

            start_sec = float(job.config.get("start_sec", 0))
            end_sec = float(job.config.get("end_sec", start_sec))
            if end_sec <= start_sec:
                raise ValueError("End time must be greater than start time.")

            # 2. Update job status to RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting clipping.")

            # 3. Execute clipping with ffmpeg (copy mode)
            local_temp = self.fs_cache.prepare_local_path(f"clips/tmp/{job.job_id}.mp4")
            self._render_clip(Path(local_source), local_temp, start_sec, end_sec)

            relative_output = job.config.get("output_relative_path") or self._build_clip_relative_path(
                job.ytid, job.config.get("label", "clip"), start_sec, end_sec
            )
            self.fs_cache.persist_local_artifact(
                local_temp,
                relative_output,
                video_repo=self.video_repo,
                ytid=job.ytid,
                kind="clip"
            )

            # 4. Update job status to COMPLETED
            job_result = {
                "clip_path": relative_output,
                "label": job.config.get("label"),
                "duration": round(end_sec - start_sec, 3)
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Successfully clipped %s to %s", job.ytid, relative_output)

            # Cleanup temp
            if local_temp.exists():
                local_temp.unlink()

        except Exception as e:
            error_msg = f"Clipping failed for job {job.job_id} ({job.ytid}): {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_media_asset_path(self, ytid: str) -> Optional[str]:
        asset = self.video_repo.get_primary_asset(ytid, "media")
        return asset.path if asset else None

    def _build_clip_relative_path(self, ytid: str, label: str, start_sec: float, end_sec: float) -> str:
        safe_label = "".join(ch if ch.isalnum() else "-" for ch in label.lower()).strip("-") or "clip"
        start_tag = f"{math.floor(start_sec*1000):07d}"
        end_tag = f"{math.floor(end_sec*1000):07d}"
        filename = f"{ytid}_{safe_label}_{start_tag}-{end_tag}.mp4"
        return str(Path("clips") / ytid / filename)

    def _render_clip(self, source_path: Path, output_path: Path, start_sec: float, end_sec: float) -> None:
        duration = max(0.1, end_sec - start_sec)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-ss", f"{start_sec:.3f}",
            "-i", str(source_path),
            "-t", f"{duration:.3f}",
            "-c", "copy",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            shutil.copy2(source_path, output_path)
        except subprocess.CalledProcessError as exc:
            logger.warning("ffmpeg clipping failed (%s). Falling back to file copy.", exc)
            shutil.copy2(source_path, output_path)
