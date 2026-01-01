# vidops/dal/workers.py

from datetime import datetime, timedelta, UTC
from typing import Optional, List
from psycopg2.extras import Json

from db import get_connection
from models import Worker, WorkerStatus

class WorkerRepository:
    """
    Data Access Layer for the 'workers' table.
    Handles registration, status updates, and heartbeats for workers.
    """
    
    def __init__(self, table_name: str = "workers"):
        if not table_name.isidentifier():
             raise ValueError("Invalid table name for WorkerRepository")
        self.table_name = table_name

    def get(self, worker_id: str) -> Optional[Worker]:
        """
        Retrieves a single worker by its ID.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table_name} WHERE worker_id = %s", (worker_id,))
                row = cur.fetchone()
                return Worker.from_row(row) if row else None

    def register(self, worker: Worker) -> Worker:
        """
        Registers a new worker or updates an existing one based on worker_id.
        This is an idempotent operation.
        """
        worker_dict = worker.to_dict()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.table_name} (
                        worker_id, machine_alias, worker_type, status, vram_gb,
                        pid, hostname, registered_at, last_heartbeat
                    )
                    VALUES (
                        %(worker_id)s, %(machine_alias)s, %(worker_type)s, %(status)s,
                        %(vram_gb)s, %(pid)s, %(hostname)s, %(registered_at)s,
                        %(last_heartbeat)s
                    )
                    ON CONFLICT (worker_id) DO UPDATE SET
                        machine_alias = EXCLUDED.machine_alias,
                        worker_type = EXCLUDED.worker_type,
                        status = EXCLUDED.status,
                        vram_gb = EXCLUDED.vram_gb,
                        pid = EXCLUDED.pid,
                        hostname = EXCLUDED.hostname,
                        last_heartbeat = NOW()
                    RETURNING *;
                    """,
                    worker_dict
                )
                row = cur.fetchone()
                return Worker.from_row(row)

    def heartbeat(self, worker_id: str) -> None:
        """
        Updates the last_heartbeat timestamp for a worker to show it's still alive.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self.table_name} SET last_heartbeat = NOW() WHERE worker_id = %s",
                    (worker_id,)
                )

    def update_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        current_job_id: Optional[str] = None,
        worker_type: Optional[str] = None,
    ) -> Optional[Worker]:
        """
        Updates a worker's status and the job it's currently processing.
        Also implicitly updates the heartbeat.
        """
        type_fragment = ", worker_type = %s" if worker_type is not None else ""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET
                        status = %s,
                        current_job_id = %s,
                        last_heartbeat = NOW()
                        {type_fragment}
                    WHERE worker_id = %s
                    RETURNING *;
                    """,
                    tuple(
                        [status.value, current_job_id]
                        + ([worker_type] if worker_type is not None else [])
                        + [worker_id]
                    )
                )
                row = cur.fetchone()
                return Worker.from_row(row) if row else None

    def list_active(self, threshold: timedelta = timedelta(minutes=5)) -> List[Worker]:
        """
        Lists all workers that have sent a heartbeat within the threshold.
        """
        active_since = datetime.now(UTC) - threshold
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self.table_name} WHERE last_heartbeat >= %s ORDER BY last_heartbeat DESC",
                    (active_since,)
                )
                rows = cur.fetchall()
                return [Worker.from_row(row) for row in rows]
                
    def purge_stale(self, stale_threshold: timedelta = timedelta(minutes=15)) -> int:
        """
        Marks workers that have missed heartbeats as 'stale'.
        Returns the number of workers marked as stale.
        This would typically be called by a supervisor/overlord process.
        """
        stale_since = datetime.now(UTC) - stale_threshold
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self.table_name}
                    SET status = %s
                    WHERE last_heartbeat < %s AND status NOT IN (%s, %s);
                    """,
                    (
                        WorkerStatus.STALE.value,
                        stale_since,
                        WorkerStatus.STALE.value,
                        WorkerStatus.STOPPING.value # Don't mark workers that are cleanly stopping
                    )
                )
                return cur.rowcount
