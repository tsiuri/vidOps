# vidops/dal/hc_finalization.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from db import get_connection


class HCFinalizationRepository:
    """Repository for project finalization artifacts.

    Phase 5: provides an explicit, user-driven way to mark a project finalized
    and persist exactly which hit/clip versions are frozen.

    This is intentionally non-destructive and does not perform cleanup/GC.
    """

    def create_finalization(
        self,
        *,
        project_id: str,
        finalized_by: Optional[str] = None,
        reason: Optional[str] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        artifacts: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        snapshot = snapshot or {}
        artifacts = artifacts or []
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_project_finalizations (project_id, finalized_by, reason, snapshot)
                    VALUES (%s, %s, %s, %s::jsonb)
                    RETURNING finalization_id
                    """,
                    (project_id, finalized_by, reason, psycopg2.extras.Json(snapshot)),
                )
                finalization_id = str(cur.fetchone()[0])
                if artifacts:
                    rows: List[Tuple[Any, ...]] = []
                    for a in artifacts:
                        rows.append(
                            (
                                finalization_id,
                                a.get("artifact_kind"),
                                a.get("artifact_id"),
                                a.get("artifact_logical_id"),
                                a.get("artifact_version"),
                            )
                        )
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        INSERT INTO hc_finalized_artifacts (finalization_id, artifact_kind, artifact_id, artifact_logical_id, artifact_version)
                        VALUES %s
                        """,
                        rows,
                        template="(%s,%s,%s,%s,%s)",
                    )
                return finalization_id

    def get_latest_finalization(self, project_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_project_finalizations
                    WHERE project_id = %s
                    ORDER BY finalized_at DESC
                    LIMIT 1
                    """,
                    (project_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def list_finalized_artifacts(self, finalization_id: str) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_finalized_artifacts
                    WHERE finalization_id = %s
                    ORDER BY artifact_kind, created_at ASC
                    """,
                    (finalization_id,),
                )
                return [dict(r) for r in cur.fetchall()]
