from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Any

from psycopg2.extras import Json

# Import AnalysisDatabase from the same module (analysis_task_repository.py provides it)
from vidops.config import load_config

class AnalysisDatabase:
    """Wrapper for vidops database connection with cursor interface."""
    def __init__(self, host: str = None, port: int = None, dbname: str = None,
                 user: str = None, password: str = None) -> None:
        config = load_config()
        self.host = host or config.database.host
        self.port = port or config.database.port
        self.dbname = dbname or config.database.name
        self.user = user or config.database.user
        self.password = password or config.database.password
        self.conn = None
        self.cursor = None

    def connect(self) -> None:
        """Connect to database."""
        import psycopg2
        self.conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            database=self.dbname,
            user=self.user,
            password=self.password,
        )
        self.cursor = self.conn.cursor()

    def disconnect(self) -> None:
        """Disconnect from database."""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()


class AnalysisResultsRepository:
    """
    Repository for reading/writing aggregated job results in analysis_results.
    """

    def __init__(self, db: AnalysisDatabase) -> None:
        self.db = db

    def upsert_results(
        self,
        job_id: str,
        ytid: str,
        config_id: str,
        results_by_pass: Dict[str, Any],
        total_tasks: int,
        completed_tasks: int,
        failed_tasks: int,
        status: str,
    ) -> None:
        """
        Insert or update the aggregated results row for a job.

        Mirrors the schema described in schema.sql:

            analysis_results (
              id                  BIGSERIAL PRIMARY KEY,
              ytid                TEXT NOT NULL,
              config_id           TEXT NOT NULL,
              job_id              TEXT NOT NULL,
              results_by_pass     JSONB NOT NULL,
              total_tasks         INTEGER NOT NULL,
              completed_tasks     INTEGER NOT NULL,
              failed_tasks        INTEGER DEFAULT 0,
              status              TEXT NOT NULL DEFAULT 'processing',
              created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              completed_at        TIMESTAMPTZ,
              UNIQUE(job_id),
              UNIQUE(ytid, config_id)
            )
        """
        now = datetime.now(timezone.utc)
        completed_at = now if status == "completed" else None

        sql = """
            INSERT INTO analysis_results (
                job_id,
                ytid,
                config_id,
                results_by_pass,
                total_tasks,
                completed_tasks,
                failed_tasks,
                status,
                completed_at
            )
            VALUES (
                %(job_id)s,
                %(ytid)s,
                %(config_id)s,
                %(results_by_pass)s,
                %(total_tasks)s,
                %(completed_tasks)s,
                %(failed_tasks)s,
                %(status)s,
                %(completed_at)s
            )
            ON CONFLICT (job_id) DO UPDATE SET
                results_by_pass   = EXCLUDED.results_by_pass,
                total_tasks       = EXCLUDED.total_tasks,
                completed_tasks   = EXCLUDED.completed_tasks,
                failed_tasks      = EXCLUDED.failed_tasks,
                status            = EXCLUDED.status,
                completed_at      = EXCLUDED.completed_at
        """

        cur = self.db.cursor
        cur.execute(
            sql,
            {
                "job_id": job_id,
                "ytid": ytid,
                "config_id": config_id,
                "results_by_pass": Json(results_by_pass),
                "total_tasks": int(total_tasks),
                "completed_tasks": int(completed_tasks),
                "failed_tasks": int(failed_tasks),
                "status": status,
                "completed_at": completed_at,
            },
        )
        if self.db.conn:
            self.db.conn.commit()
