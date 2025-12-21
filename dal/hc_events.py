# vidops/dal/hc_events.py

from __future__ import annotations

from typing import Any, Dict, List, Optional

import psycopg2.extras

from db import get_connection


class HCArtifactEventRepository:
    """Append-only audit trail for lifecycle + invalidation events.

    This table is append-only. Phase 6 uses read-only listing helpers to
    render lineage/invalidation explanations in the Web UI.
    """

    def add_event(
        self,
        *,
        project_id: str,
        entity_type: str,
        entity_id: str,
        event_type: str,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
        actor: Optional[str] = None,
        reason: Optional[str] = None,
        event_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        event_meta = event_meta or {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_artifact_events (
                        project_id, entity_type, entity_id,
                        event_type, from_state, to_state,
                        actor, reason, event_meta
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s::jsonb
                    )
                    RETURNING event_id
                    """,
                    (
                        project_id,
                        entity_type,
                        entity_id,
                        event_type,
                        from_state,
                        to_state,
                        actor,
                        reason,
                        psycopg2.extras.Json(event_meta),
                    ),
                )
                return str(cur.fetchone()[0])

    # ------------------------------------------------------------------
    # Phase 6: observability (read-only)
    # ------------------------------------------------------------------

    def list_events_for_entity(self, entity_type: str, entity_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_artifact_events
                    WHERE entity_type = %s AND entity_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (entity_type, entity_id, int(limit)),
                )
                return [dict(r) for r in cur.fetchall()]

    def list_events_for_project(
        self,
        project_id: str,
        *,
        entity_type: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM hc_artifact_events WHERE project_id = %s"
        params: List[Any] = [project_id]
        if entity_type:
            sql += " AND entity_type = %s"
            params.append(entity_type)
        if event_type:
            sql += " AND event_type = %s"
            params.append(event_type)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(int(limit))
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
