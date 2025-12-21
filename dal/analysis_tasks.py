# vidops/dal/analysis_tasks.py

from __future__ import annotations

from typing import Any, Dict, List, Optional

import psycopg2.extras

from db import get_connection


class AnalysisTaskResultsRepository:
    """Read-only helpers for pulling completed analysis task results.

    The distributed analysis system stores chunk-level results in `analysis_tasks.result_json`.
    This repository exists so the Hits & Clips project DAG can *consume* those results
    (without participating in task claiming/execution).
    """

    def list_completed(
        self,
        job_id: str,
        pass_ids: Optional[List[str]] = None,
        ytids: Optional[List[str]] = None,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        where = ["job_id = %s", "status = 'completed'"]
        params: List[Any] = [job_id]

        if pass_ids:
            where.append("pass_id = ANY(%s)")
            params.append(pass_ids)
        if ytids:
            where.append("ytid = ANY(%s)")
            params.append(ytids)

        params.append(int(limit))

        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT task_id, job_id, ytid, chunk_id, pass_id, chunk_metadata, result_json, completed_at
                    FROM analysis_tasks
                    WHERE {' AND '.join(where)}
                    ORDER BY ytid, chunk_id, pass_id
                    LIMIT %s
                    """,
                    tuple(params),
                )
                return [dict(r) for r in cur.fetchall()]


    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single analysis_tasks row by task_id."""
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT task_id, job_id, ytid, chunk_id, pass_id, chunk_metadata, result_json,
                           status, created_at, claimed_at, completed_at, error_message
                    FROM analysis_tasks
                    WHERE task_id = %s
                    """,
                    (int(task_id),),
                )
                row = cur.fetchone()
                return dict(row) if row else None
