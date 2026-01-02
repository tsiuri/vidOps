# vidops/services/stitching.py

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional

from dal import VideoRepository, JobRepository, FilesystemCache
from models import Job, JobStatus
from services.stitching_native import VideoStitcher

logger = logging.getLogger(__name__)

class StitchingService:
    """
    Native stitching (no workspace.sh) while preserving legacy outputs/paths.
    Inputs are materialized under media/clips/, stitched via ffmpeg, and outputs
    are pushed to central storage with asset registration.
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
        priority: int = 50
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
            "sort_method": "date_timestamp",
        }

        first_input_id = input_ytids_or_clip_paths[0]

        job = Job(
            job_type="stitching",
            ytid=first_input_id,  # Placeholder; can be None or more complex logic
            config=config,
            priority=priority,
            status=JobStatus.PENDING
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Processes a single stitching job claimed by a worker.
        """
        sources = job.config.get("input_sources") or []
        output_filename = job.config.get("output_filename")
        if not sources or not output_filename:
            self.job_repo.update_status(
                job.job_id,
                JobStatus.FAILED,
                error_message="Stitching job config is missing input sources or output filename.",
            )
            return

        try:
            project_root = self._project_root()
            stage_dir, staged_files = self._stage_inputs(
                project_root,
                sources,
                output_filename,
                job.job_id,
            )
            if not staged_files:
                raise FileNotFoundError("No valid inputs could be materialized for stitching.")

            output_relative = job.config.get("output_relative_path") or str(
                Path("stitch") / output_filename
            )
            output_local = self._local_output_path(project_root, output_filename)
            method = job.config.get("stitch_method", "batch")
            sort_method = job.config.get("sort_method", "date_timestamp")

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting stitching.")
            stitcher = VideoStitcher()
            if method == "batch":
                stitcher.batch_stitch(stage_dir, output_local, sort_method=sort_method)
            elif method == "concat":
                stitcher.concat_stitch(stage_dir, output_local, sort_method=sort_method, filter_complex=False)
            elif method == "concat_filter":
                stitcher.concat_stitch(stage_dir, output_local, sort_method=sort_method, filter_complex=True)
            elif method == "cfr":
                stitcher.cfr_stitch(stage_dir, output_local, sort_method=sort_method)
            else:
                raise ValueError(f"Unknown stitch method: {method}")

            if not output_local.exists() or output_local.stat().st_size == 0:
                job_result = {
                    "stitched_path": None,
                    "error": "Stitch produced no output",
                }
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message="Stitch produced no output",
                    result=job_result,
                )
                return

            stored_path = self.fs_cache.persist_local_artifact(
                output_local,
                output_relative,
                video_repo=self.video_repo,
                ytid=job.ytid or "stitch",
                kind="stitched",
            )

            job_result = {
                "stitched_path": str(Path(output_relative)),
                "stored_path": str(Path(stored_path)),
                "input_count": len(staged_files),
                "stage_dir": str(stage_dir.relative_to(project_root)),
                "method": method,
                "sort_method": sort_method,
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Successfully stitched %s -> %s", job.job_id, output_relative)

        except Exception as e:
            error_msg = f"Stitching failed for job {job.job_id}: {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _project_root(self) -> Path:
        return Path(os.environ.get("VIDOPS_PROJECT_ROOT") or Path(__file__).resolve().parents[1])

    def _stage_inputs(
        self,
        project_root: Path,
        sources: List[str],
        output_filename: str,
        job_id: str,
    ) -> tuple[Path, List[Path]]:
        run_name = Path(output_filename).stem or "stitch"
        stage_dir = project_root / "media" / "clips" / f"{self._sanitize(run_name)}_{job_id[:8]}"
        stage_dir.mkdir(parents=True, exist_ok=True)

        staged: List[Path] = []
        for idx, source in enumerate(sources):
            staged_file = self._stage_single_source(source, stage_dir, idx)
            if staged_file:
                staged.append(staged_file)
        return stage_dir, staged

    def _stage_single_source(self, source: str, stage_dir: Path, idx: int) -> Optional[Path]:
        try:
            if "/" in source:
                rel_path = source.lstrip("/")
                local_source = self.fs_cache.pull_to_cache(rel_path)
            else:
                video = self.video_repo.get(source)
                if not video:
                    logger.warning("Video %s missing for stitching input.", source)
                    return None
                local_source = self.fs_cache.get_media_path(video, pull_to_local=True)
                if not local_source:
                    logger.warning("Media not found for %s", source)
                    return None
            target_name = f"{idx:03d}_{self._sanitize(Path(local_source).name)}"
            target = stage_dir / target_name
            if local_source.resolve() != target.resolve():
                shutil.copy2(local_source, target)
            return target
        except Exception as exc:
            logger.warning("Failed to stage %s: %s", source, exc)
            return None

    def _sanitize(self, name: str) -> str:
        safe = []
        for ch in name:
            safe.append(ch if ord(ch) < 128 else "-")
        cleaned = "".join(safe)
        while "--" in cleaned:
            cleaned = cleaned.replace("--", "-")
        return cleaned.strip("-") or "clip"

    def _local_output_path(self, project_root: Path, output_filename: str) -> Path:
        # Legacy outputs live under generated/stitch/
        target_dir = project_root / "generated" / "stitch"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / output_filename
