# vidops/dal/hc_projects.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import psycopg2.extras

from db import get_connection


@dataclass(frozen=True)
class HCProject:
    project_id: str
    name: str
    description: Optional[str]
    status: str
    tags: Optional[str]
    input_spec: Dict[str, Any]
    config_spec: Dict[str, Any]
    created_at: str
    updated_at: str


class HCProjectRepository:
    """Repository for hc_projects and hc_project_runs."""

    def create_project(
        self,
        name: str,
        description: Optional[str] = None,
        tags: Optional[str] = None,
        input_spec: Optional[Dict[str, Any]] = None,
        config_spec: Optional[Dict[str, Any]] = None,
    ) -> str:
        input_spec = input_spec or {}
        config_spec = config_spec or {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_projects (name, description, tags, input_spec, config_spec)
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                    RETURNING project_id
                    """,
                    (name, description, tags, psycopg2.extras.Json(input_spec), psycopg2.extras.Json(config_spec)),
                )
                return str(cur.fetchone()[0])

    def list_projects(self, limit: int = 50) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_projects
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM hc_projects WHERE project_id = %s", (project_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def update_project(self, project_id: str, patch: Dict[str, Any]) -> None:
        allowed = {"name", "description", "status", "tags", "input_spec", "config_spec"}
        keys = [k for k in patch.keys() if k in allowed]
        if not keys:
            return
        set_sql = []
        values = []
        for k in keys:
            if k in ("input_spec", "config_spec"):
                set_sql.append(f"{k} = %s::jsonb")
                values.append(psycopg2.extras.Json(patch[k]))
            else:
                set_sql.append(f"{k} = %s")
                values.append(patch[k])
        set_sql.append("updated_at = NOW()")
        values.append(project_id)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE hc_projects SET {', '.join(set_sql)} WHERE project_id = %s",
                    values,
                )

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def create_run(self, project_id: str, created_by: Optional[str] = None, run_spec: Optional[Dict[str, Any]] = None) -> str:
        run_spec = run_spec or {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_project_runs (project_id, created_by, run_spec)
                    VALUES (%s, %s, %s::jsonb)
                    RETURNING run_id
                    """,
                    (project_id, created_by, psycopg2.extras.Json(run_spec)),
                )
                return str(cur.fetchone()[0])

    def list_runs(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_project_runs
                    WHERE project_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (project_id, limit),
                )
                return [dict(r) for r in cur.fetchall()]

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM hc_project_runs WHERE run_id = %s", (run_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def update_run_status(self, run_id: str, status: str, error_message: Optional[str] = None) -> None:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if status == "running":
                    cur.execute(
                        """
                        UPDATE hc_project_runs
                        SET status = %s, started_at = COALESCE(started_at, NOW()), updated_at = NOW(), error_message = NULL
                        WHERE run_id = %s
                        """,
                        (status, run_id),
                    )
                elif status in ("completed", "failed", "cancelled"):
                    cur.execute(
                        """
                        UPDATE hc_project_runs
                        SET status = %s, completed_at = COALESCE(completed_at, NOW()), updated_at = NOW(), error_message = %s
                        WHERE run_id = %s
                        """,
                        (status, error_message, run_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE hc_project_runs
                        SET status = %s, updated_at = NOW(), error_message = %s
                        WHERE run_id = %s
                        """,
                        (status, error_message, run_id),
                    )
