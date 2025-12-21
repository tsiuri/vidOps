# vidops/dal/analysis_results_read.py

from __future__ import annotations

from typing import Any, Dict, List, Optional

import psycopg2.extras

from db import get_connection


class AnalysisResultsReadRepository:
    """Read-only helpers for pulling aggregated `analysis_results` rows.

    The distributed analysis pipeline may periodically upsert aggregated, per-video
    results into `analysis_results.results_by_pass`. Hits & Clips can consume these
    aggregates without needing to parse the raw `analysis_tasks` queue.
    """

    def list_completed(
        self,
        job_id: str,
        ytids: Optional[List[str]] = None,
        config_ids: Optional[List[str]] = None,
        limit: int = 2000,
    ) -> List[Dict[str, Any]]:
        where = ["job_id = %s", "status = 'completed'"]
        params: List[Any] = [job_id]
        if ytids:
            where.append("ytid = ANY(%s)")
            params.append(ytids)
        if config_ids:
            where.append("config_id = ANY(%s)")
            params.append(config_ids)
        params.append(int(limit))

        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT id, job_id, ytid, config_id, results_by_pass, total_tasks,
                           completed_tasks, failed_tasks, status, created_at, completed_at
                    FROM analysis_results
                    WHERE {' AND '.join(where)}
                    ORDER BY completed_at DESC NULLS LAST, created_at DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                return [dict(r) for r in cur.fetchall()]


    def get_result(self, result_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single analysis_results row by id."""
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, job_id, ytid, config_id, results_by_pass, total_tasks,
                           completed_tasks, failed_tasks, status, created_at, completed_at
                    FROM analysis_results
                    WHERE id = %s
                    """,
                    (int(result_id),),
                )
                row = cur.fetchone()
                return dict(row) if row else None
