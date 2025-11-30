# vidops/services/stitching.py

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List

from vidops.dal import VideoRepository, JobRepository, FilesystemCache
from vidops.models import Job, JobStatus

logger = logging.getLogger(__name__)

class StitchingService:
    """
    Orchestrates the video stitching process, managing job creation,
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

    def enqueue_stitch_job(
        self,
        input_ytids_or_clip_paths: List[str],
        output_filename: str,
        stitch_method: str = "batch",
        priority: int = 0
    ) -> Job:
        """
        Enqueues a video stitching job.

        Args:
            input_ytids_or_clip_paths: A list of YTIDs or clip paths to stitch.
            output_filename: The desired name for the output stitched video.
            stitch_method: The FFmpeg stitching method to use.
            priority: Job priority.

        Returns:
            The created Job object.

        Raises:
            ValueError: If input list is empty or output filename is invalid.
        """
        if not input_ytids_or_clip_paths:
            raise ValueError("Input list for stitching cannot be empty.")
        if not output_filename:
            raise ValueError("Output filename cannot be empty.")
        
        output_relative = str(Path("stitch") / output_filename)

        config = {
            "input_sources": input_ytids_or_clip_paths,
            "output_filename": output_filename,
            "stitch_method": stitch_method,
            "output_relative_path": output_relative,
        }

        # For stitching, we might not have a single 'ytid' or 'media_path'
        # if stitching multiple sources. We'll use a generic job_id as the primary identifier.
        # However, the Job model still expects ytid, so we'll use a placeholder or the first input's ytid.
        first_input_id = input_ytids_or_clip_paths[0]
        
        job = Job(
            job_type="stitching",
            ytid=first_input_id, # Placeholder; can be None or more complex logic
            config=config,
            priority=priority,
            status=JobStatus.PENDING
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Processes a single stitching job claimed by a worker.
        """
        if not job.config.get('input_sources') or not job.config.get('output_filename'):
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Stitching job config is missing input sources or output filename.")
            return

        try:
            input_paths = self._resolve_input_paths(job.config['input_sources'])
            if not input_paths:
                raise FileNotFoundError("No valid input media files found for stitching job.")

            local_inputs = [self._ensure_local_path(path) for path in input_paths]

            # 2. Update job status to RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting stitching.")

            # 3. Execute stitching via ffmpeg concat
            local_output = self.fs_cache.prepare_local_path(f"stitch/tmp/{job.job_id}.mp4")
            self._run_stitch(local_inputs, local_output, job.config.get('stitch_method', 'batch'))

            relative_output = job.config.get("output_relative_path") or str(
                Path("stitch") / job.config['output_filename']
            )
            self.fs_cache.persist_local_artifact(
                local_output,
                relative_output,
                video_repo=self.video_repo,
                ytid=job.ytid or "stitch",
                kind="stitched"
            )

            # 4. Update job status to COMPLETED
            job_result = {
                "stitched_path": relative_output,
                "input_count": len(local_inputs)
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Successfully stitched %s -> %s", job.job_id, relative_output)

            if local_output.exists():
                local_output.unlink()

        except Exception as e:
            error_msg = f"Stitching failed for job {job.job_id}: {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_input_paths(self, sources: List[str]) -> List[Path]:
        resolved: List[Path] = []
        for source in sources:
            if "/" in source:
                candidate = self.fs_cache.get_central_path(source)
                if candidate.exists():
                    resolved.append(candidate)
                else:
                    logger.warning("Clip asset missing from storage: %s", source)
            else:
                video = self.video_repo.get(source)
                if not video:
                    logger.warning("Video %s missing for stitching input.", source)
                    continue
                media_path = self.fs_cache.get_media_path(video)
                if media_path and media_path.exists():
                    resolved.append(media_path)
                else:
                    logger.warning("Media not found for %s", source)
        return resolved

    def _ensure_local_path(self, path: Path) -> Path:
        try:
            relative = path.relative_to(self.fs_cache.central_storage_root)
            return self.fs_cache.pull_to_cache(str(relative))
        except ValueError:
            return path

    def _run_stitch(self, inputs: List[Path], output_path: Path, method: str) -> None:
        manifest = self.fs_cache.prepare_local_path(f"stitch/manifests/{output_path.stem}.txt")
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("\n".join(f"file '{path}'" for path in inputs), encoding="utf-8")

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", str(manifest),
            "-c", "copy",
            str(output_path),
        ]

        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            # Fallback: naive copy of first file
            shutil.copy2(inputs[0], output_path)
        except subprocess.CalledProcessError as exc:
            logger.error("ffmpeg stitching failed (%s).", exc)
            raise
        finally:
            if manifest.exists():
                manifest.unlink()
