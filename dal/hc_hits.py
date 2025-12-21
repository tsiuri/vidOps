# vidops/dal/hc_hits.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from db import get_connection


class HCHitRepository:
    """Repository for hc_hit_logicals/hc_hits/hc_hit_spans.

    Hits are append-only versions under a logical id.

    Phase 3 added invalidation accounting on hc_hits (observational):
      - validity_status (valid/invalid)
      - invalidated_at, invalidation_reason, invalidation_source

    This repository enforces explicit lifecycle transitions on *a specific hit version row*.
    Versions remain immutable as separate rows; status changes are audited.
    """

    # ------------------------------------------------------------------
    # Logical ids
    # ------------------------------------------------------------------

    def _ensure_logical(self, project_id: str, logical_key: Optional[str] = None) -> str:
        """Create or get a logical hit container.

        If `logical_key` is provided, it becomes a stable key within the project.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                if logical_key:
                    cur.execute(
                        """
                        INSERT INTO hc_hit_logicals (project_id, logical_key)
                        VALUES (%s, %s)
                        ON CONFLICT (project_id, logical_key) DO UPDATE SET logical_key = EXCLUDED.logical_key
                        RETURNING hit_logical_id
                        """,
                        (project_id, logical_key),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO hc_hit_logicals (project_id)
                        VALUES (%s)
                        RETURNING hit_logical_id
                        """,
                        (project_id,),
                    )
                return str(cur.fetchone()[0])

    def get_latest_hit_by_logical_key(self, project_id: str, logical_key: str) -> Optional[Dict[str, Any]]:
        """Return the latest hit row for a given project-scoped logical_key, if present."""
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT h.*
                    FROM hc_hit_logicals l
                    JOIN hc_hits h ON h.hit_logical_id = l.hit_logical_id
                    WHERE l.project_id = %s
                      AND l.logical_key = %s
                    ORDER BY h.hit_version DESC
                    LIMIT 1
                    """,
                    (project_id, logical_key),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    # ------------------------------------------------------------------
    # Create versions
    # ------------------------------------------------------------------

    def create_hit_version(
        self,
        project_id: str,
        hit_type: str,
        ytid: Optional[str],
        spans: List[Tuple[float, float, Optional[str]]],
        label: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: Optional[float] = None,
        rationale: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        logical_key: Optional[str] = None,
        status: str = "active",
        pinned: bool = False,
    ) -> Dict[str, Any]:
        """Create a new hit logical+version.

        Returns dict with hit_id, hit_logical_id, hit_version.
        """
        if not spans:
            raise ValueError("Hit must have at least one span")
        tags = tags or []
        provenance = provenance or {}
        payload = payload or {}
        hit_logical_id = self._ensure_logical(project_id, logical_key=logical_key)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(MAX(hit_version), 0) + 1
                    FROM hc_hits
                    WHERE hit_logical_id = %s
                    """,
                    (hit_logical_id,),
                )
                next_version = int(cur.fetchone()[0])

                cur.execute(
                    """
                    INSERT INTO hc_hits (
                        project_id, hit_logical_id, hit_version,
                        ytid, hit_type, status, pinned,
                        label, tags, confidence, rationale,
                        provenance, payload
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb
                    )
                    RETURNING hit_id
                    """,
                    (
                        project_id,
                        hit_logical_id,
                        next_version,
                        ytid,
                        hit_type,
                        status,
                        pinned,
                        label,
                        tags,
                        confidence,
                        rationale,
                        psycopg2.extras.Json(provenance),
                        psycopg2.extras.Json(payload),
                    ),
                )
                hit_id = str(cur.fetchone()[0])

                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO hc_hit_spans (hit_id, start_sec, end_sec, speaker_name)
                    VALUES %s
                    """,
                    [(hit_id, float(s), float(e), speaker) for (s, e, speaker) in spans],
                )

        return {"hit_id": hit_id, "hit_logical_id": hit_logical_id, "hit_version": next_version}

    def create_hit_version_if_absent(
        self,
        *,
        project_id: str,
        logical_key: str,
        source_fingerprint: str,
        hit_type: str,
        ytid: Optional[str],
        spans: List[Tuple[float, float, Optional[str]]],
        label: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: Optional[float] = None,
        rationale: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        status: str = "active",
        pinned: bool = False,
    ) -> Dict[str, Any]:
        """Create a hit version under `logical_key` unless the latest already matches `source_fingerprint`.

        This provides idempotency for generator nodes while still preserving non-determinism.
        """
        latest = self.get_latest_hit_by_logical_key(project_id, logical_key)
        if latest:
            fp = None
            try:
                prov = latest.get("provenance") or {}
                if isinstance(prov, dict):
                    fp = prov.get("source_fingerprint")
            except Exception:
                fp = None
            if fp == source_fingerprint:
                return {
                    "hit_id": str(latest["hit_id"]),
                    "hit_logical_id": str(latest["hit_logical_id"]),
                    "hit_version": int(latest["hit_version"]),
                    "created": False,
                }

        provenance = dict(provenance or {})
        provenance.setdefault("source_fingerprint", source_fingerprint)

        created = self.create_hit_version(
            project_id=project_id,
            hit_type=hit_type,
            ytid=ytid,
            spans=spans,
            label=label,
            tags=tags,
            confidence=confidence,
            rationale=rationale,
            provenance=provenance,
            payload=payload,
            logical_key=logical_key,
            status=status,
            pinned=pinned,
        )
        created["created"] = True
        created["source_fingerprint"] = source_fingerprint
        return created

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_hit(self, hit_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM hc_hits WHERE hit_id = %s", (hit_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def get_hit_spans(self, hit_id: str) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT * FROM hc_hit_spans WHERE hit_id = %s ORDER BY start_sec ASC""",
                    (hit_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def get_hit_logical_id(self, hit_id: str) -> Optional[str]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT hit_logical_id FROM hc_hits WHERE hit_id = %s", (hit_id,))
                row = cur.fetchone()
                return str(row[0]) if row else None

    def list_hit_versions(self, hit_logical_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_hits
                    WHERE hit_logical_id = %s
                    ORDER BY hit_version DESC, created_at DESC
                    LIMIT %s
                    """,
                    (hit_logical_id, int(limit)),
                )
                return [dict(r) for r in cur.fetchall()]

    def list_hits(
        self,
        project_id: str,
        status: Optional[str] = None,
        hit_type: Optional[str] = None,
        pinned: Optional[bool] = None,
        validity_status: Optional[str] = None,
        latest_only: bool = False,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """List hits for a project.

        - validity_status filter (valid/invalid)
        - latest_only: if True, returns only the latest version per hit_logical_id.
        """
        where = ["project_id = %s"]
        params: List[Any] = [project_id]
        if status:
            where.append("status = %s")
            params.append(status)
        if hit_type:
            where.append("hit_type = %s")
            params.append(hit_type)
        if pinned is not None:
            where.append("pinned = %s")
            params.append(pinned)
        if validity_status:
            where.append("validity_status = %s")
            params.append(validity_status)

        base_where = " AND ".join(where)

        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if latest_only:
                    cur.execute(
                        f"""
                        SELECT DISTINCT ON (hit_logical_id) *
                        FROM hc_hits
                        WHERE {base_where}
                        ORDER BY hit_logical_id, hit_version DESC, created_at DESC
                        LIMIT %s
                        """,
                        tuple(params + [int(limit)]),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT *
                        FROM hc_hits
                        WHERE {base_where}
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        tuple(params + [int(limit)]),
                    )
                return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Lifecycle transitions (explicit)
    # ------------------------------------------------------------------

    def transition_hit_status(
        self,
        hit_id: str,
        to_status: str,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> None:
        """Enforce hit lifecycle transitions (explicit, audited).

        Allowed transitions:
        - active -> stale | rejected | archived
        - stale -> active | rejected | archived
        - rejected -> active | archived
        - archived -> (terminal)

        Notes:
        - This updates the *selected hit version row* in place. Versions remain immutable as separate rows.
        - Pinned hits can still be explicitly rejected/archived via API; pinning only blocks invalidation updates.
        """
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT project_id, status FROM hc_hits WHERE hit_id = %s", (hit_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError("hit not found")
                project_id = str(row["project_id"])
                from_status = str(row["status"])

                allowed = {
                    "active": {"stale", "rejected", "archived"},
                    "stale": {"active", "rejected", "archived"},
                    "rejected": {"active", "archived"},
                    "archived": set(),
                }
                if to_status != from_status and to_status not in allowed.get(from_status, set()):
                    raise ValueError(f"invalid hit status transition: {from_status} -> {to_status}")

                if to_status != from_status:
                    cur.execute(
                        """
                        UPDATE hc_hits
                        SET status = %s, updated_at = NOW()
                        WHERE hit_id = %s
                        """,
                        (to_status, hit_id),
                    )

                    from dal.hc_events import HCArtifactEventRepository

                    HCArtifactEventRepository().add_event(
                        project_id=project_id,
                        entity_type="hit",
                        entity_id=str(hit_id),
                        event_type="lifecycle",
                        from_state=from_status,
                        to_state=to_status,
                        actor=actor,
                        reason=reason,
                        event_meta={},
                    )

    def set_hit_status(self, hit_id: str, status: str) -> None:
        """Backward-compatible wrapper (no actor/reason)."""
        self.transition_hit_status(hit_id, status, actor="api", reason="")

    def set_hit_pinned(self, hit_id: str, pinned: bool) -> None:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE hc_hits SET pinned = %s, updated_at = NOW() WHERE hit_id = %s""",
                    (pinned, hit_id),
                )

    # ------------------------------------------------------------------
    # Invalidation (Phase 3 semantics)
    # ------------------------------------------------------------------

    def invalidate_by_node_key(
        self,
        *,
        project_id: str,
        node_key: str,
        mode: str,
        actor: str,
        reason: str,
        source: dict,
    ) -> int:
        """Invalidate hits produced by a node (non-destructive).

        strict:
          - validity_status => invalid
          - status => stale (unless pinned)
        soft:
          - status => stale (unless pinned)

        Only affects rows with provenance.node_key == node_key.
        """
        mode = mode or "soft"
        with get_connection() as conn:
            with conn.cursor() as cur:
                if mode == "strict":
                    cur.execute(
                        """
                        UPDATE hc_hits
                        SET validity_status = 'invalid',
                            status = CASE WHEN status = 'active' THEN 'stale' ELSE status END,
                            invalidated_at = NOW(),
                            invalidation_reason = %s,
                            invalidation_source = %s::jsonb,
                            updated_at = NOW()
                        WHERE project_id = %s
                          AND COALESCE((provenance->>'node_key'), '') = %s
                          AND pinned = FALSE
                          AND validity_status <> 'invalid'
                        """,
                        (reason, psycopg2.extras.Json(source), project_id, node_key),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE hc_hits
                        SET status = CASE WHEN status = 'active' THEN 'stale' ELSE status END,
                            invalidated_at = NOW(),
                            invalidation_reason = %s,
                            invalidation_source = %s::jsonb,
                            updated_at = NOW()
                        WHERE project_id = %s
                          AND COALESCE((provenance->>'node_key'), '') = %s
                          AND pinned = FALSE
                        """,
                        (reason, psycopg2.extras.Json(source), project_id, node_key),
                    )
                count = cur.rowcount or 0

        if count:
            from dal.hc_events import HCArtifactEventRepository

            HCArtifactEventRepository().add_event(
                project_id=project_id,
                entity_type="node_execution",
                entity_id=str(source.get("node_id") or "00000000-0000-0000-0000-000000000000"),
                event_type="invalidation",
                from_state=None,
                to_state=None,
                actor=actor,
                reason=reason,
                event_meta={"target": "hc_hits", "count": count, "mode": mode, "node_key": node_key, "source": source},
            )
        return count

    def count_invalid_for_node(self, project_id: str, node_key: str) -> int:
        """Count invalid hits for a given node_key within a project.

        Version-aware: counts only currently-latest versions per hit_logical_id that are invalid.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT DISTINCT ON (hit_logical_id) hit_logical_id, validity_status
                        FROM hc_hits
                        WHERE project_id = %s
                          AND COALESCE((provenance->>'node_key'), '') = %s
                        ORDER BY hit_logical_id, hit_version DESC
                    ) latest
                    WHERE validity_status = 'invalid'
                    """,
                    (project_id, node_key),
                )
                return int(cur.fetchone()[0])

    # ------------------------------------------------------------------
    # Phase 6 observability helpers (read-only)
    # ------------------------------------------------------------------

    def list_clips_depending_on_hit(self, hit_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Return clips that directly reference this hit version (hc_clips.hit_id)."""
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_clips
                    WHERE hit_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (hit_id, int(limit)),
                )
                return [dict(r) for r in cur.fetchall()]
