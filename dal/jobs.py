# vidops/dal/jobs.py

from typing import Optional
from datetime import datetime, timedelta, UTC
from psycopg2.extras import Json

from db import get_connection
from models import Job, JobStatus, Worker

class JobRepository:
    """
    Data Access Layer for generic job queues (Overlord System v2).
    Handles all database operations for the 'jobs' table.
    """

    def __init__(self, table_name: str = "jobs"):
        # Using generic 'jobs' table for all job types (Overlord System v2)
        if not table_name.isidentifier():
             raise ValueError("Invalid table name for JobRepository")
        self.table_name = table_name

    def get(self, job_id: str) -> Optional[Job]:
        """
        Retrieves a single job by its ID.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table_name} WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                return Job.from_row(row) if row else None

    def create(self, job: Job) -> Job:
        """
        Creates a new job in the generic jobs table.
        """
        job_dict = job.to_dict()
        # Adapt dictionaries to JSON for psycopg2
        job_dict['config'] = Json(job_dict['config'])
        job_dict['result'] = Json(job_dict['result'])

        with get_connection() as conn:
            with conn.cursor() as cur:
                # Basic INSERT, no ON CONFLICT, as job_id should be unique
                cur.execute(
                    f"""
                    INSERT INTO {self.table_name} (
                        job_id, job_type, status, priority, ytid,
                        media_path, config, created_at, updated_at
                    )
                    VALUES (
                        %(job_id)s, %(job_type)s, %(status)s, %(priority)s, %(ytid)s,
                        %(media_path)s, %(config)s, %(created_at)s, %(updated_at)s
                    )
                    RETURNING *;
                    """,
                    job_dict
                )
                created_row = cur.fetchone()
                return Job.from_row(created_row)

    def claim_next(
        self,
        worker: Worker,
        lease_duration: timedelta = timedelta(hours=1),
        job_types: Optional[list[str]] = None
    ) -> Optional[Job]:
        """
        Atomically claims the next available job from the queue.
        This uses 'SELECT FOR UPDATE SKIP LOCKED' to prevent race conditions
        between multiple workers.

        Respects job dependencies: only claims jobs where the depends_on job
        (if present) has been completed.

        Args:
            worker: The worker attempting to claim the job.
            lease_duration: How long the job should be "leased" before it's
                            considered stale.
            job_types: Optional list of job_type values to restrict the claim to.

        Returns:
            A Job object if one was successfully claimed, otherwise None.
        """
        # The job is considered stale if it was claimed but not updated
        # for the duration of the lease.
        stale_threshold = datetime.now(UTC) - lease_duration

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET
                        status = %s,
                        claimed_by = %s,
                        claimed_at = NOW(),
                        updated_at = NOW()
                    WHERE job_id = (
                        SELECT j.job_id
                        FROM {self.table_name} j
                        LEFT JOIN {self.table_name} dep ON j.config->>'depends_on' = dep.job_id
                        WHERE
                            (
                                j.status = %s OR
                                (j.status = %s AND j.updated_at < %s)
                            )
                            { "AND j.job_type = ANY(%s)" if job_types else "" }
                            -- Either no dependency, or dependency is completed
                            AND (
                                j.config->>'depends_on' IS NULL
                                OR dep.status = %s
                            )
                        ORDER BY j.priority DESC, j.created_at ASC
                        FOR UPDATE OF j SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *;
                    """,
                    tuple(
                        [
                            JobStatus.CLAIMED.value,
                            worker.worker_id,
                            JobStatus.PENDING.value,
                            JobStatus.CLAIMED.value,
                            stale_threshold,
                        ]
                        + ([job_types] if job_types else [])
                        + [
                            JobStatus.COMPLETED.value,
                        ]
                    ),
                )
                claimed_row = cur.fetchone()
                return Job.from_row(claimed_row) if claimed_row else None

    def update_status(self, job_id: str, status: JobStatus, error_message: Optional[str] = None, result: Optional[dict] = None) -> Optional[Job]:
        """
        Updates the status of a job.

        Args:
            job_id: The ID of the job to update.
            status: The new status for the job.
            error_message: An optional error message if the job failed.
            result: Optional result data to store (will be converted to JSONB).

        Returns:
            The updated Job object, or None if the job was not found.
        """
        # Convert result dict to Json if provided
        result_json = Json(result) if result is not None else None

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET
                        status = %s,
                        error_message = %s,
                        result = COALESCE(%s, result),
                        updated_at = NOW()
                        -- Set started_at timestamp if moving to RUNNING
                        , started_at = CASE WHEN %s = %s AND started_at IS NULL THEN NOW() ELSE started_at END
                        -- Set completed_at timestamp if it's a final state
                        , completed_at = CASE WHEN %s IN (%s, %s, %s) THEN NOW() ELSE completed_at END
                    WHERE job_id = %s
                    RETURNING *;
                    """,
                    (
                        status.value,
                        error_message,
                        result_json,
                        status.value,
                        JobStatus.RUNNING.value,
                        status.value,
                        JobStatus.COMPLETED.value,
                        JobStatus.FAILED.value,
                        JobStatus.CANCELLED.value,
                        job_id
                    )
                )
                row = cur.fetchone()
                return Job.from_row(row) if row else None

    def release(self, job_id: str) -> Optional[Job]:
        """
        Releases a claimed job back to the 'pending' state.
        This is useful if a worker fails gracefully before starting processing.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET
                        status = %s,
                        priority = priority - 1,
                        claimed_by = NULL,
                        claimed_at = NULL,
                        started_at = NULL,
                        completed_at = NULL,
                        updated_at = NOW()
                    WHERE job_id = %s
                    RETURNING *;
                    """,
                    (JobStatus.PENDING.value, job_id)
                )
                row = cur.fetchone()
                return Job.from_row(row) if row else None

    def update_config(self, job_id: str, config: dict) -> Optional[Job]:
        """
        Update a job's config JSON.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET config = %s, updated_at = NOW()
                    WHERE job_id = %s
                    RETURNING *;
                    """,
                    (Json(config), job_id),
                )
                row = cur.fetchone()
                return Job.from_row(row) if row else None

    # =========================================================================
    # Overlord Helper Methods
    # =========================================================================

    def find_completed_without_followup(self, job_type: str, followup_key: str = 'analysis_enqueued') -> list[Job]:
        """
        Find completed jobs of a specific type that haven't had follow-up processing.

        Used by Overlord to detect jobs ready for chaining (e.g., transcription -> analysis).

        Args:
            job_type: The type of job to search for (e.g., 'transcription')
            followup_key: Key in result JSONB to check (e.g., 'analysis_enqueued')

        Returns:
            List of Job objects
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {self.table_name}
                    WHERE
                        job_type = %s
                        AND status = %s
                        AND NOT (result ? %s)
                    ORDER BY completed_at ASC
                    LIMIT 100;
                    """,
                    (job_type, JobStatus.COMPLETED.value, followup_key)
                )
                rows = cur.fetchall()
                return [Job.from_row(row) for row in rows]

    def find_stale_jobs(self, stale_threshold: timedelta) -> list[Job]:
        """
        Find jobs that have been claimed but not updated within the threshold.

        These jobs likely belong to dead/crashed workers and should be released.

        Args:
            stale_threshold: Time since last update to consider stale

        Returns:
            List of stale Job objects
        """
        stale_since = datetime.now(UTC) - stale_threshold

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {self.table_name}
                    WHERE
                        status IN (%s, %s)
                        AND updated_at < %s
                    ORDER BY updated_at ASC
                    LIMIT 100;
                    """,
                    (JobStatus.CLAIMED.value, JobStatus.RUNNING.value, stale_since)
                )
                rows = cur.fetchall()
                return [Job.from_row(row) for row in rows]

    def release_stale_jobs(self, stale_threshold: timedelta) -> int:
        """
        Bulk release stale jobs back to pending status.

        Args:
            stale_threshold: Time since last update to consider stale

        Returns:
            Number of jobs released
        """
        stale_since = datetime.now(UTC) - stale_threshold

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET
                        status = %s,
                        claimed_by = NULL,
                        claimed_at = NULL,
                        updated_at = NOW()
                    WHERE
                        status IN (%s, %s)
                        AND updated_at < %s
                    RETURNING job_id;
                    """,
                    (
                        JobStatus.PENDING.value,
                        JobStatus.CLAIMED.value,
                        JobStatus.RUNNING.value,
                        stale_since
                    )
                )
                released = cur.fetchall()
                return len(released)

    def update_result(self, job_id: str, result_updates: dict) -> Optional[Job]:
        """
        Update specific keys in a job's result JSONB.

        Args:
            job_id: Job ID to update
            result_updates: Dictionary of key-value pairs to merge into result

        Returns:
            Updated Job object, or None if job not found
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET
                        result = result || %s::jsonb,
                        updated_at = NOW()
                    WHERE job_id = %s
                    RETURNING *;
                    """,
                    (Json(result_updates), job_id)
                )
                row = cur.fetchone()
                return Job.from_row(row) if row else None
