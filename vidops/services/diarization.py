# vidops/services/diarization.py

import logging
from typing import List, Optional
from datetime import timedelta

from vidops.dal import VideoRepository, JobRepository, TranscriptRepository, FilesystemCache
from vidops.models import Video, Job, JobStatus, Transcript

logger = logging.getLogger(__name__)

class DiarizationService:
    """
    Orchestrates the diarization process, managing job creation,
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

    def enqueue_diarization_job(
        self,
        ytid: str,
        transcript_kind: str,
        diarization_model: str, # e.g., 'resemblyzer'
        priority: int = 0
    ) -> Job:
        """
        Enqueues a single diarization job.

        Args:
            ytid: YouTube ID of the video to diarize.
            transcript_kind: The kind of transcript to use for diarization (e.g., 'words_whisper_medium').
            diarization_model: The model/method to use for diarization.
            priority: Job priority.

        Returns:
            The created Job object.

        Raises:
            ValueError: If the video or transcript is not found.
        """
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")
        
        transcript = self.transcript_repo.get(ytid, transcript_kind)
        if not transcript:
            raise ValueError(f"Transcript of kind '{transcript_kind}' not found for video '{ytid}'.")
        
        # Create the job configuration
        config = {
            "ytid": ytid,
            "transcript_kind": transcript_kind,
            "diarization_model": diarization_model,
            "media_path_hint": video.url # Hint for where to find the media
        }

        # Create the job in the database
        job = Job(
            job_type="diarization",
            ytid=ytid,
            media_path=video.url, # Original media source for audio extraction
            config=config,
            priority=priority,
            status=JobStatus.PENDING
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Processes a single diarization job claimed by a worker.
        """
        if not job.ytid or not job.media_path:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid or media_path.")
            return

        try:
            # 1. Resolve media file path
            video_obj = self.video_repo.get(job.ytid)
            if not video_obj:
                raise ValueError(f"Video object not found for ytid: {job.ytid}")

            media_local_path = self.fs_cache.get_media_path(video_obj)
            if not media_local_path or not media_local_path.exists():
                raise FileNotFoundError(f"Media file not found for ytid '{job.ytid}' at '{job.media_path}'.")

            # 2. Update job status to RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting diarization.")

            # 3. Execute diarization (placeholder)
            logger.info(f"Diarizing {job.ytid} with model {job.config.get('diarization_model')}...")
            
            # --- Placeholder for actual diarization logic (e.g., calling Resemblyzer) ---
            # For now, simulate success
            diarization_result_content = f"Diarization report for {job.ytid}: Speaker 1 (0-10s), Speaker 2 (10-20s)."
            diarization_output_path = f"/tmp/diarization/{job.ytid}_{job.config['diarization_model']}.json"
            
            # Simulate saving diarization results
            # Path(diarization_output_path).parent.mkdir(parents=True, exist_ok=True)
            # Path(diarization_output_path).write_text(diarization_result_content, encoding='utf-8')
            
            logger.debug(f"Simulated diarization output: {diarization_output_path}")
            # --- End Placeholder ---

            # 4. Update job status to COMPLETED
            job_result = {
                "output_diarization_path": diarization_output_path,
                "summary": diarization_result_content
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info(f"Successfully diarized {job.ytid} to {diarization_output_path}.")

        except Exception as e:
            error_msg = f"Diarization failed for job {job.job_id} ({job.ytid}): {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)
