# vidops/services/housekeeping.py

import logging
from datetime import datetime, timedelta
from typing import List
from dal import VideoRepository, JobRepository
from configuration import load_config

logger = logging.getLogger(__name__)


class HousekeepingService:
    """
    Handles maintenance tasks when workers are idle.
    Enqueues backlog jobs (transcription, diarization, etc.) at very low priority.
    """

    def __init__(self, video_repo: VideoRepository, job_repo: JobRepository):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.config = load_config()

    def run_housekeeping(self) -> int:
        """
        Execute housekeeping tasks.

        Returns:
            Number of jobs enqueued
        """
        if not self.config.housekeeping.enabled:
            logger.debug("Housekeeping is disabled")
            return 0

        jobs_enqueued = 0

        # Process enabled tasks
        if "transcribe_default" in self.config.housekeeping.tasks:
            count = self._enqueue_pending_transcriptions()
            jobs_enqueued += count

        logger.info(f"Housekeeping cycle complete: enqueued {jobs_enqueued} jobs")
        return jobs_enqueued

    def _enqueue_pending_transcriptions(self) -> int:
        """
        Find videos needing default transcription and enqueue them.

        Handles two cases:
        1. Videos with NO transcript (priority: config.priority)
        2. Videos with lesser model transcript (priority: config.priority - 5, for upgrades)

        Returns:
            Number of jobs enqueued
        """
        from services.transcription import TranscriptionService

        config = self.config.housekeeping
        model = self.config.transcription.model  # large-v3-turbo by default
        min_age_seconds = config.min_video_age_seconds
        max_jobs = config.max_per_cycle
        priority = config.priority
        upgrade_priority = max(1, priority - config.upgrade_priority_offset)  # Lower priority for upgrades (at least 1)

        # Calculate min age cutoff
        now = datetime.utcnow()
        min_age_cutoff = now - timedelta(seconds=min_age_seconds)

        logger.info(
            f"Looking for pending transcriptions (model={model}, min_age={min_age_seconds}s, max={max_jobs})"
        )

        try:
            transcription_service = TranscriptionService(
                self.video_repo, self.job_repo
            )
            jobs_enqueued = 0

            # 1. First-priority: videos with NO transcript at all
            no_transcript_videos = self._find_videos_needing_transcription(
                model, min_age_cutoff, max_jobs // 2  # Use half of budget for no-transcript
            )

            for video in no_transcript_videos:
                try:
                    job = transcription_service.enqueue_video(
                        ytid=video["ytid"],
                        model=model,
                        language=self.config.transcription.language,
                        priority=priority,
                        force=False,
                        force_job=False,
                    )
                    logger.info(
                        f"Housekeeping: Enqueued transcription for {video['ytid']} (no prior transcript) "
                        f"(job={job.job_id}, priority={priority})"
                    )
                    jobs_enqueued += 1
                except Exception as exc:
                    if "unique" in str(exc).lower():
                        logger.debug(
                            f"Housekeeping: Job already exists for {video['ytid']}, skipping"
                        )
                    else:
                        logger.warning(
                            f"Housekeeping: Failed to enqueue transcription for {video['ytid']}: {exc}"
                        )

            # 2. Second-priority: videos with lesser model transcript (upgrades)
            remaining_budget = max_jobs - jobs_enqueued
            if remaining_budget > 0:
                upgrade_videos = self._find_videos_with_lesser_transcript(
                    model, min_age_cutoff, remaining_budget
                )

                for video in upgrade_videos:
                    try:
                        job = transcription_service.enqueue_video(
                            ytid=video["ytid"],
                            model=model,
                            language=self.config.transcription.language,
                            priority=upgrade_priority,
                            force=False,
                            force_job=False,
                        )
                        logger.info(
                            f"Housekeeping: Enqueued transcription upgrade for {video['ytid']} "
                            f"(from {video['current_model']}) (job={job.job_id}, priority={upgrade_priority})"
                        )
                        jobs_enqueued += 1
                    except Exception as exc:
                        if "unique" in str(exc).lower():
                            logger.debug(
                                f"Housekeeping: Job already exists for {video['ytid']}, skipping"
                            )
                        else:
                            logger.warning(
                                f"Housekeeping: Failed to enqueue transcription upgrade for {video['ytid']}: {exc}"
                            )

            return jobs_enqueued

        except Exception as exc:
            logger.error(f"Housekeeping transcription error: {exc}", exc_info=True)
            return 0

    def _find_videos_needing_transcription(
        self, model: str, min_age_cutoff: datetime, limit: int
    ) -> List[dict]:
        """
        Find videos that don't have a transcript for the given model
        and don't have a pending/running transcription job.

        Args:
            model: Whisper model (e.g., 'large-v3-turbo')
            min_age_cutoff: Only include videos created before this time
            limit: Maximum videos to return

        Returns:
            List of videos needing transcription
        """
        from db import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Find videos without transcripts for this model, excluding young videos
                # and excluding videos with pending/running transcription jobs
                query = """
                    SELECT DISTINCT v.ytid
                    FROM videos v
                    WHERE v.created_at < %s
                    AND v.ytid NOT IN (
                        -- Exclude videos with pending/running transcription jobs
                        SELECT ytid FROM jobs
                        WHERE job_type = 'transcription'
                        AND status IN ('PENDING', 'CLAIMED', 'RUNNING')
                        AND config->>'model' = %s
                        AND ytid IS NOT NULL
                    )
                    AND v.ytid NOT IN (
                        -- Exclude videos that already have this transcript
                        SELECT ytid FROM transcripts
                        WHERE kind = %s
                    )
                    ORDER BY v.created_at ASC
                    LIMIT %s
                """
                transcript_kind = f"words_whisper_{model}"
                cur.execute(query, (min_age_cutoff, model, transcript_kind, limit))
                results = cur.fetchall()
                return [{"ytid": row[0]} for row in results]

    def _find_videos_with_lesser_transcript(
        self, target_model: str, min_age_cutoff: datetime, limit: int
    ) -> List[dict]:
        """
        Find videos with a lesser Whisper model transcript that should be upgraded.

        Args:
            target_model: Target model to upgrade to (e.g., 'large-v3-turbo')
            min_age_cutoff: Only include videos created before this time
            limit: Maximum videos to return

        Returns:
            List of videos with current_model, ytid
        """
        from db import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Find videos with lesser model transcripts (small, base, medium)
                # but NOT the target model, and no pending transcription jobs
                query = """
                    SELECT DISTINCT v.ytid, t.kind
                    FROM videos v
                    JOIN transcripts t ON v.ytid = t.ytid
                    WHERE v.created_at < %s
                    AND t.kind IN ('words_whisper_small', 'words_whisper_base', 'words_whisper_medium')
                    AND v.ytid NOT IN (
                        -- Exclude videos with pending/running transcription jobs
                        SELECT ytid FROM jobs
                        WHERE job_type = 'transcription'
                        AND status IN ('PENDING', 'CLAIMED', 'RUNNING')
                        AND config->>'model' = %s
                        AND ytid IS NOT NULL
                    )
                    AND v.ytid NOT IN (
                        -- Exclude videos that already have the target transcript
                        SELECT ytid FROM transcripts
                        WHERE kind = %s
                    )
                    ORDER BY v.created_at ASC
                    LIMIT %s
                """
                target_kind = f"words_whisper_{target_model}"
                cur.execute(query, (min_age_cutoff, target_model, target_kind, limit))
                results = cur.fetchall()
                return [
                    {"ytid": row[0], "current_model": row[1].replace("words_whisper_", "")}
                    for row in results
                ]
