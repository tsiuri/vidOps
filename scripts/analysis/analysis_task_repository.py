from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from psycopg2.extras import Json

from .analysis_task import AnalysisTask, TaskStatus
from .db_storage import AnalysisDatabase


def _row_to_dict(row, cur) -> Dict[str, Any]:
    """Convert psycopg2 tuple row to dict using cursor.description."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    cols = [desc[0] for desc in cur.description]
    return dict(zip(cols, row))


class AnalysisTaskRepository:
    """
    Repository for working with the analysis_tasks table.

    This is intentionally low-level and focused on the core operations
    needed for the distributed analysis worker model.
    """

    def __init__(self, db: AnalysisDatabase) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create_task(self, task: AnalysisTask) -> AnalysisTask:
        """
        Insert a new task row and return the fully-populated task.
        """
        data = task.to_dict()
        now = datetime.now(timezone.utc)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)

        sql = """
            INSERT INTO analysis_tasks (
                job_id,
                ytid,
                chunk_id,
                pass_id,
                chunk_text,
                chunk_metadata,
                required_capabilities,
                status,
                result_json,
                claimed_by,
                claimed_at,
                lease_expires_at,
                started_at,
                completed_at,
                error_message,
                created_at,
                updated_at
            )
            VALUES (
                %(job_id)s,
                %(ytid)s,
                %(chunk_id)s,
                %(pass_id)s,
                %(chunk_text)s,
                %(chunk_metadata)s,
                %(required_capabilities)s,
                %(status)s,
                %(result_json)s,
                %(claimed_by)s,
                %(claimed_at)s,
                %(lease_expires_at)s,
                %(started_at)s,
                %(completed_at)s,
                %(error_message)s,
                %(created_at)s,
                %(updated_at)s
            )
            RETURNING *
        """

        params = {
            "job_id": data["job_id"],
            "ytid": data["ytid"],
            "chunk_id": data["chunk_id"],
            "pass_id": data["pass_id"],
            "chunk_text": data["chunk_text"],
            "chunk_metadata": Json(data.get("chunk_metadata") or {}),
            "required_capabilities": data.get("required_capabilities") or [],
            "status": data["status"],
            "result_json": Json(data["result_json"]) if data.get("result_json") is not None else None,
            "claimed_by": data.get("claimed_by"),
            "claimed_at": data.get("claimed_at"),
            "lease_expires_at": data.get("lease_expires_at"),
            "started_at": data.get("started_at"),
            "completed_at": data.get("completed_at"),
            "error_message": data.get("error_message"),
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
        }

        cur = self.db.cursor
        cur.execute(sql, params)
        row = _row_to_dict(cur.fetchone(), cur)
        if self.db.conn:
            self.db.conn.commit()
        return AnalysisTask.from_row(row)

    # ------------------------------------------------------------------
    # Claiming
    # ------------------------------------------------------------------

    def claim_next(
        self,
        worker_id: str,
        worker_capabilities: List[str],
        lease_duration: timedelta,
    ) -> Optional[AnalysisTask]:
        """
        Atomically claim the next available task compatible with the worker's capabilities.

        Uses SELECT ... FOR UPDATE SKIP LOCKED to avoid contention between workers.
        """
        now = datetime.now(timezone.utc)
        lease_expires = now + lease_duration

        # Note: required_capabilities is ARRAY(TEXT). We use <@ to mean "subset of".
        # If required_capabilities is NULL or empty, any worker can take the task.
        select_sql = """
            SELECT *
            FROM analysis_tasks
            WHERE status IN ('pending', 'failed')
              AND (claimed_by IS NULL OR lease_expires_at < %(now)s)
              AND (
                    required_capabilities IS NULL
                 OR required_capabilities = '{}'
                 OR required_capabilities <@ %(caps)s
              )
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """

        cur = self.db.cursor
        cur.execute(select_sql, {"now": now, "caps": worker_capabilities})
        row = _row_to_dict(cur.fetchone(), cur)
        if not row:
            if self.db.conn:
                self.db.conn.commit()
            return None

        task_id = row["task_id"]

        update_sql = """
            UPDATE analysis_tasks
            SET status = 'claimed',
                claimed_by = %(worker_id)s,
                claimed_at = %(now)s,
                lease_expires_at = %(lease_expires)s,
                updated_at = %(now)s
            WHERE task_id = %(task_id)s
            RETURNING *
        """

        cur.execute(
            update_sql,
            {
                "worker_id": worker_id,
                "now": now,
                "lease_expires": lease_expires,
                "task_id": task_id,
            },
        )
        updated = _row_to_dict(cur.fetchone(), cur)
        if self.db.conn:
            self.db.conn.commit()

        return AnalysisTask.from_row(updated)

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------

    def mark_completed(self, task_id: int, result_json: Dict[str, Any]) -> bool:
        """
        Mark a task as completed and store its result_json.
        """
        now = datetime.now(timezone.utc)
        sql = """
            UPDATE analysis_tasks
            SET status = 'completed',
                result_json = %(result_json)s,
                completed_at = %(now)s,
                updated_at = %(now)s
            WHERE task_id = %(task_id)s
        """
        cur = self.db.cursor
        cur.execute(
            sql,
            {
                "result_json": Json(result_json),
                "now": now,
                "task_id": task_id,
            },
        )
        if self.db.conn:
            self.db.conn.commit()
        return cur.rowcount == 1

    def mark_failed(self, task_id: int, error_message: str) -> bool:
        """
        Mark a task as failed and store an error message.
        """
        now = datetime.now(timezone.utc)
        sql = """
            UPDATE analysis_tasks
            SET status = 'failed',
                error_message = %(error_message)s,
                updated_at = %(now)s
            WHERE task_id = %(task_id)s
        """
        cur = self.db.cursor
        cur.execute(
            sql,
            {
                "error_message": error_message,
                "now": now,
                "task_id": task_id,
            },
        )
        if self.db.conn:
            self.db.conn.commit()
        return cur.rowcount == 1

    # ------------------------------------------------------------------
    # Job-level helpers
    # ------------------------------------------------------------------

    def get_job_tasks(self, job_id: str) -> List[AnalysisTask]:
        """
        Return all tasks for a given job_id.
        """
        sql = "SELECT * FROM analysis_tasks WHERE job_id = %s ORDER BY chunk_id, pass_id"
        cur = self.db.cursor
        cur.execute(sql, (job_id,))
        rows = cur.fetchall() or []
        return [AnalysisTask.from_row(_row_to_dict(r, cur)) for r in rows]

    def get_job_progress(self, job_id: str) -> Dict[str, int]:
        """
        Return a dictionary summarizing task counts per status for the job.
        """
        sql = """
            SELECT status, COUNT(*) AS count
            FROM analysis_tasks
            WHERE job_id = %s
            GROUP BY status
        """
        cur = self.db.cursor
        cur.execute(sql, (job_id,))
        rows = cur.fetchall() or []

        progress: Dict[str, int] = {
            "total": 0,
            "pending": 0,
            "claimed": 0,
            "completed": 0,
            "failed": 0,
        }

        for row in rows:
            row_dict = _row_to_dict(row, cur)
            status = row_dict["status"]
            count = int(row_dict["count"])
            progress["total"] += count
            if status in progress:
                progress[status] = count

        return progress

    def is_job_complete(self, job_id: str) -> bool:
        """
        Return True when all tasks for a job are in a terminal state
        (completed or failed) and there are no pending/claimed tasks.
        """
        progress = self.get_job_progress(job_id)
        total = progress.get("total", 0)
        if total == 0:
            return False  # No tasks – not a meaningful "complete" state

        pending = progress.get("pending", 0)
        claimed = progress.get("claimed", 0)
        return pending == 0 and claimed == 0
