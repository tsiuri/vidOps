# vidops/services/overlord.py

import logging
import time
from datetime import timedelta, datetime, UTC
from typing import Optional

from vidops.config import load_config
from vidops.dal import JobRepository, WorkerRepository
from vidops.models import JobStatus, WorkerStatus, Job
# Avoid circular import - use TYPE_CHECKING
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from vidops.services.analysis import AnalysisService

logger = logging.getLogger(__name__)

class OverlordService:
    """
    The Overlord service acts as a supervisor, monitoring the generic job queue,
    orchestrating job chaining, and performing system housekeeping.

    Responsibilities:
    - Detect completed transcription jobs and enqueue follow-up analysis
    - Detect stale jobs (claimed but not updated) and release them
    - Detect stale workers (no heartbeat) and mark them as stale
    - General system health monitoring
    """

    def __init__(
        self,
        job_repo: JobRepository,
        worker_repo: WorkerRepository,
        analysis_service: "AnalysisService",
    ):
        self.config = load_config()
        self.job_repo = job_repo
        self.worker_repo = worker_repo
        self.analysis_service = analysis_service
        self.running = False
        self.cycle_interval_sec = 10  # How often the overlord wakes up

        # Stale thresholds from config or defaults
        self.job_stale_threshold = timedelta(hours=2)  # Jobs not updated in 2 hours
        self.worker_stale_threshold = timedelta(
            minutes=self.config.workers.heartbeat_interval * 3
        )  # 3x heartbeat interval

    def _process_completed_transcriptions(self):
        """
        Checks for completed transcription jobs and enqueues follow-up analysis jobs.

        Uses the generic jobs table with job_type='transcription'.
        Checks result.analysis_enqueued to avoid duplicates.
        """
        logger.debug("Overlord: Checking for completed transcription jobs...")

        try:
            # Find completed transcription jobs without follow-up
            completed_jobs = self.job_repo.find_completed_without_followup(
                job_type='transcription',
                followup_key='analysis_enqueued'
            )

            if not completed_jobs:
                logger.debug("Overlord: No transcription jobs needing analysis.")
                return

            logger.info(f"Overlord: Found {len(completed_jobs)} transcription jobs needing analysis.")

            for job in completed_jobs:
                try:
                    logger.info(
                        f"Overlord: Transcription job {job.job_id} for {job.ytid} completed. "
                        f"Enqueuing analysis..."
                    )

                    # Determine transcript kind from job config
                    model = job.config.get('model', 'medium')
                    transcript_kind = f"words_whisper_{model}"

                    # Enqueue analysis job via AnalysisService
                    analysis_job = self.analysis_service.enqueue_analysis_job(
                        ytid=job.ytid,
                        transcript_kind=transcript_kind,
                        analysis_model="llama3",  # Default model
                        priority=max(0, job.priority - 1)  # Slightly lower priority
                    )

                    # Mark transcription job as having analysis enqueued
                    self.job_repo.update_result(
                        job_id=job.job_id,
                        result_updates={
                            'analysis_enqueued': True,
                            'analysis_job_id': analysis_job.job_id
                        }
                    )

                    logger.info(
                        f"Overlord: Enqueued analysis job {analysis_job.job_id} "
                        f"for transcription {job.job_id}."
                    )

                except Exception as e:
                    logger.error(
                        f"Overlord: Failed to enqueue analysis for {job.job_id}: {e}",
                        exc_info=True
                    )

        except Exception as e:
            logger.error(f"Overlord: Error in _process_completed_transcriptions: {e}", exc_info=True)

    def _recover_stale_jobs(self):
        """
        Detect and recover stale jobs (claimed/running but not updated).

        Stale jobs likely belong to dead/crashed workers. Release them back
        to pending so other workers can claim them.
        """
        logger.debug("Overlord: Checking for stale jobs...")

        try:
            # Find stale jobs
            stale_jobs = self.job_repo.find_stale_jobs(self.job_stale_threshold)

            if not stale_jobs:
                logger.debug("Overlord: No stale jobs found.")
                return

            logger.warning(f"Overlord: Found {len(stale_jobs)} stale jobs. Releasing...")

            # Get list of stale worker IDs before releasing jobs
            stale_worker_ids = set(
                job.claimed_by for job in stale_jobs if job.claimed_by
            )

            # Bulk release stale jobs
            num_released = self.job_repo.release_stale_jobs(self.job_stale_threshold)

            logger.warning(
                f"Overlord: Released {num_released} stale jobs back to pending."
            )

            # Mark workers as stale
            if stale_worker_ids:
                for worker_id in stale_worker_ids:
                    try:
                        self.worker_repo.update_status(
                            worker_id=worker_id,
                            status=WorkerStatus.STALE
                        )
                        logger.warning(f"Overlord: Marked worker {worker_id} as STALE.")
                    except Exception as e:
                        logger.error(
                            f"Overlord: Failed to mark worker {worker_id} as stale: {e}"
                        )

        except Exception as e:
            logger.error(f"Overlord: Error in _recover_stale_jobs: {e}", exc_info=True)

    def _perform_housekeeping(self):
        """
        Performs general system housekeeping.

        - Purges stale workers (no heartbeat)
        - Additional maintenance tasks as needed
        """
        logger.debug("Overlord: Performing housekeeping...")

        try:
            # Purge stale workers based on heartbeat
            num_stale = self.worker_repo.purge_stale(self.worker_stale_threshold)

            if num_stale > 0:
                logger.warning(f"Overlord: Purged {num_stale} stale workers (no heartbeat).")

        except Exception as e:
            logger.error(f"Overlord: Error in _perform_housekeeping: {e}", exc_info=True)

    def run(self):
        """Main loop for the Overlord service."""
        logger.info("Starting Overlord service...")
        logger.info(f"  Job stale threshold: {self.job_stale_threshold}")
        logger.info(f"  Worker stale threshold: {self.worker_stale_threshold}")
        logger.info(f"  Cycle interval: {self.cycle_interval_sec}s")

        self.running = True

        try:
            while self.running:
                # Job chaining: transcription -> analysis
                self._process_completed_transcriptions()

                # Stale job recovery
                self._recover_stale_jobs()

                # Worker housekeeping
                self._perform_housekeeping()

                # Sleep until next cycle
                time.sleep(self.cycle_interval_sec)

        except KeyboardInterrupt:
            logger.info("Overlord service interrupted by user.")
        except Exception as e:
            logger.critical(
                f"Overlord service encountered a critical error: {e}",
                exc_info=True
            )
        finally:
            self.running = False
            logger.info("Overlord service shutting down.")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    print("--- Starting Overlord Service (for testing) ---")

    try:
        from vidops.services import get_overlord_service
        overlord = get_overlord_service()
        overlord.run()
    except KeyboardInterrupt:
        print("\n--- Overlord Service Stopped by User ---")
    except Exception as e:
        print(f"\n--- Overlord Service Stopped with Error: {e} ---")
