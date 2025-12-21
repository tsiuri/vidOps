# vidops/dal/hc_suggestions.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from db import get_connection


class HCSuggestionRepository:
    """Repository for hc_suggestions/hc_suggestion_spans.

    Suggestions are *not* hits. They can later be promoted into a hit by explicit action.
    """

    def create_suggestion(
        self,
        project_id: str,
        suggestion_type: str,
        ytid: Optional[str] = None,
        spans: Optional[List[Tuple[float, float, Optional[str]]]] = None,
        label: Optional[str] = None,
        confidence: Optional[float] = None,
        rationale: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        status: str = "open",
        run_id: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = payload or {}
        spans = spans or []
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_suggestions (
                        project_id, run_id, node_id,
                        ytid, suggestion_type,
                        label, confidence, rationale,
                        payload, status
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s::jsonb, %s
                    )
                    RETURNING suggestion_id
                    """,
                    (
                        project_id,
                        run_id,
                        node_id,
                        ytid,
                        suggestion_type,
                        label,
                        confidence,
                        rationale,
                        psycopg2.extras.Json(payload),
                        status,
                    ),
                )
                suggestion_id = str(cur.fetchone()[0])

                if spans:
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        INSERT INTO hc_suggestion_spans (suggestion_id, start_sec, end_sec, speaker_name)
                        VALUES %s
                        """,
                        [(suggestion_id, float(s), float(e), speaker) for (s, e, speaker) in spans],
                    )

        return {"suggestion_id": suggestion_id}

    def create_suggestion_if_absent(
        self,
        *,
        project_id: str,
        source_fingerprint: str,
        suggestion_type: str,
        ytid: Optional[str] = None,
        spans: Optional[List[Tuple[float, float, Optional[str]]]] = None,
        label: Optional[str] = None,
        confidence: Optional[float] = None,
        rationale: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        status: str = "open",
        run_id: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a suggestion unless one with the same `source_fingerprint` already exists.

        The fingerprint is stored under payload.source_fingerprint.
        """

        payload = payload or {}
        payload.setdefault("source_fingerprint", source_fingerprint)

        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT suggestion_id
                    FROM hc_suggestions
                    WHERE project_id = %s
                      AND payload->>'source_fingerprint' = %s
                    LIMIT 1
                    """,
                    (project_id, source_fingerprint),
                )
                existing = cur.fetchone()
                if existing:
                    return {"suggestion_id": str(existing["suggestion_id"]), "created": False}

        created = self.create_suggestion(
            project_id=project_id,
            run_id=run_id,
            node_id=node_id,
            ytid=ytid,
            suggestion_type=suggestion_type,
            spans=spans,
            label=label,
            confidence=confidence,
            rationale=rationale,
            payload=payload,
            status=status,
        )
        created["created"] = True
        return created

    def list_suggestions(
        self,
        project_id: str,
        status: Optional[str] = None,
        suggestion_type: Optional[str] = None,
        validity_status: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        where = ["project_id = %s"]
        params: List[Any] = [project_id]
        if status:
            where.append("status = %s")
            params.append(status)
        if suggestion_type:
            where.append("suggestion_type = %s")
            params.append(suggestion_type)
        if validity_status:
            where.append("validity_status = %s")
            params.append(validity_status)
        params.append(limit)

        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT *
                    FROM hc_suggestions
                    WHERE {' AND '.join(where)}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                return [dict(r) for r in cur.fetchall()]

    def get_suggestion(self, suggestion_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM hc_suggestions WHERE suggestion_id = %s", (suggestion_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_suggestion_spans(self, suggestion_id: str) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT * FROM hc_suggestion_spans WHERE suggestion_id = %s ORDER BY start_sec ASC""",
                    (suggestion_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def list_promoted_unconverted(self, project_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        """List suggestions explicitly marked promoted but not yet linked to a hit."""
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_suggestions
                    WHERE project_id = %s
                      AND status = 'promoted'
                      AND promoted_hit_id IS NULL
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    (project_id, int(limit)),
                )
                return [dict(r) for r in cur.fetchall()]

    def transition_status(
        self,
        suggestion_id: str,
        to_status: str,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> None:
        """Enforce suggestion lifecycle transitions (explicit, audited).

        Allowed transitions:
        - open -> promoted | dismissed
        - dismissed -> open
        - promoted -> (terminal)
        """
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT project_id, status FROM hc_suggestions WHERE suggestion_id = %s", (suggestion_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError("suggestion not found")
                project_id = str(row["project_id"])
                from_status = str(row["status"])

                allowed = {
                    "open": {"promoted", "dismissed"},
                    "dismissed": {"open"},
                    "promoted": set(),
                }
                if to_status != from_status and to_status not in allowed.get(from_status, set()):
                    raise ValueError(f"invalid suggestion status transition: {from_status} -> {to_status}")

                if to_status != from_status:
                    cur.execute(
                        """
                        UPDATE hc_suggestions
                        SET status = %s
                        WHERE suggestion_id = %s
                        """,
                        (to_status, suggestion_id),
                    )

                    # audit
                    from dal.hc_events import HCArtifactEventRepository

                    HCArtifactEventRepository().add_event(
                        project_id=project_id,
                        entity_type="suggestion",
                        entity_id=str(suggestion_id),
                        event_type="lifecycle",
                        from_state=from_status,
                        to_state=to_status,
                        actor=actor,
                        reason=reason,
                        event_meta={},
                    )

    def set_status(self, suggestion_id: str, status: str) -> None:
        """Backward-compatible wrapper (no actor/reason)."""
        self.transition_status(suggestion_id, status, actor="api", reason="")

    def invalidate_by_node_id(
        self,
        *,
        project_id: str,
        node_id: str,
        mode: str,
        actor: str,
        reason: str,
        source: dict,
    ) -> int:
        """Invalidate suggestions produced by a node.

        For Phase 3:
        - strict: mark invalid
        - soft: mark invalid (suggestions have no 'stale' state)
        """
        mode = mode or "soft"
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE hc_suggestions
                    SET validity_status = 'invalid',
                        invalidated_at = NOW(),
                        invalidation_reason = %s,
                        invalidation_source = %s::jsonb
                    WHERE project_id = %s
                      AND node_id = %s
                      AND validity_status <> 'invalid'
                    """,
                    (reason, psycopg2.extras.Json(source), project_id, node_id),
                )
                count = cur.rowcount or 0

        # audit event (one per invalidation action)
        if count:
            from dal.hc_events import HCArtifactEventRepository

            HCArtifactEventRepository().add_event(
                project_id=project_id,
                entity_type="node_execution",
                entity_id=str(node_id),
                event_type="invalidation",
                from_state=None,
                to_state=None,
                actor=actor,
                reason=reason,
                event_meta={"target": "hc_suggestions", "count": count, "mode": mode, "source": source},
            )
        return count


    def count_invalid_for_node(self, project_id: str, node_key: str) -> int:
        """Count invalid suggestions for a given node_key within a project.

        Suggestions are not versioned; validity is tracked per-row.
        Used only for explicit DAG run validation (no auto-rebuild).
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM hc_suggestions s
                    JOIN hc_dag_nodes n ON n.node_id = s.node_id
                    WHERE s.project_id = %s
                      AND n.node_key = %s
                      AND s.validity_status = 'invalid'
                    """,
                    (project_id, node_key),
                )
                return int(cur.fetchone()[0])

    def set_promoted_hit(self, suggestion_id: str, hit_id: str) -> None:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE hc_suggestions
                    SET promoted_hit_id = %s
                    WHERE suggestion_id = %s
                    """,
                    (hit_id, suggestion_id),
                )
