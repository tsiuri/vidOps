# vidops/dal/hc_clips.py

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from db import get_connection


def _stable_json(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return str(obj)


def _hash_fingerprint(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        if p is None:
            h.update(b"<null>")
        elif isinstance(p, (bytes, bytearray)):
            h.update(p)
        else:
            h.update(str(p).encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()


class HCClipRepository:
    """Repository for clip profiles + versioned clips.

    Clips are append-only versions under a logical id.
    Rendering is explicit and updates the *current version* row only.
    """

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def create_profile(self, project_id: str, name: str, profile_json: Dict[str, Any], version: Optional[int] = None) -> str:
        """Create a clip profile version.

        If version is None, it auto-increments based on existing versions.
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                if version is None:
                    cur.execute(
                        """
                        SELECT COALESCE(MAX(version), 0) + 1
                        FROM hc_clip_profiles
                        WHERE project_id = %s AND name = %s
                        """,
                        (project_id, name),
                    )
                    version = int(cur.fetchone()[0])

                cur.execute(
                    """
                    INSERT INTO hc_clip_profiles (project_id, name, version, profile_json)
                    VALUES (%s, %s, %s, %s::jsonb)
                    ON CONFLICT (project_id, name, version) DO UPDATE SET
                      profile_json = EXCLUDED.profile_json,
                      updated_at = NOW()
                    RETURNING profile_id
                    """,
                    (project_id, name, int(version), psycopg2.extras.Json(profile_json)),
                )
                return str(cur.fetchone()[0])

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM hc_clip_profiles WHERE profile_id = %s", (profile_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def list_profiles(self, project_id: str, name: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM hc_clip_profiles WHERE project_id = %s"
        params: List[Any] = [project_id]
        if name:
            sql += " AND name = %s"
            params.append(name)
        sql += " ORDER BY name ASC, version DESC LIMIT %s"
        params.append(int(limit))
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Logical ids + versions
    # ------------------------------------------------------------------

    def _ensure_clip_logical(self, project_id: str, logical_key: Optional[str]) -> str:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_clip_logicals (project_id, logical_key)
                    VALUES (%s, %s)
                    ON CONFLICT (project_id, logical_key) DO UPDATE SET project_id = EXCLUDED.project_id
                    RETURNING clip_logical_id
                    """,
                    (project_id, logical_key),
                )
                return str(cur.fetchone()[0])

    def _latest_version_row(self, clip_logical_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_clips
                    WHERE clip_logical_id = %s
                    ORDER BY clip_version DESC
                    LIMIT 1
                    """,
                    (clip_logical_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def get_clip(self, clip_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM hc_clips WHERE clip_id = %s", (clip_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def list_clips(
        self,
        project_id: str,
        *,
        status: Optional[str] = None,
        validity_status: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        # List latest version per logical id.
        sql = """
            SELECT DISTINCT ON (c.clip_logical_id) c.*
            FROM hc_clips c
            WHERE c.project_id = %s
        """
        params: List[Any] = [project_id]
        if status:
            sql += " AND c.status = %s"
            params.append(status)
        if validity_status:
            sql += " AND c.validity_status = %s"
            params.append(validity_status)
        sql += " ORDER BY c.clip_logical_id, c.clip_version DESC, c.created_at DESC LIMIT %s"
        params.append(int(limit))
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]



    def list_clips_for_hit_id(self, hit_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """List latest clip versions that depend on a specific hit version (hc_clips.hit_id).

        Phase 6 observability helper (read-only).
        """
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (clip_logical_id) *
                    FROM hc_clips
                    WHERE hit_id = %s
                    ORDER BY clip_logical_id, clip_version DESC, created_at DESC
                    LIMIT %s
                    """,
                    (hit_id, int(limit)),
                )
                return [dict(r) for r in cur.fetchall()]
    def list_clip_versions(self, clip_logical_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_clips
                    WHERE clip_logical_id = %s
                    ORDER BY clip_version DESC
                    LIMIT %s
                    """,
                    (clip_logical_id, int(limit)),
                )
                return [dict(r) for r in cur.fetchall()]

    def create_clip(
        self,
        *,
        project_id: str,
        run_id: Optional[str],
        hit_id: Optional[str],
        hit_snapshot: Dict[str, Any],
        profile_id: Optional[str],
        profile_name: Optional[str],
        profile_version: Optional[int],
        ytid: Optional[str],
        start_sec: float,
        end_sec: float,
        label: Optional[str],
        status: str = "planned",
        provenance: Optional[Dict[str, Any]] = None,
        logical_key: Optional[str] = None,
    ) -> str:
        """Create a clip version (append-only) under a logical id.

        If the latest version has the same *input_fingerprint*, return that
        latest clip_id (idempotent projection).
        """
        provenance = provenance or {}

        # Stable logical identity: unless explicitly supplied, default to
        # hit_logical_id + profile identity (this matches spec intent).
        if not logical_key:
            hit_logical_id = (hit_snapshot or {}).get("hit_logical_id")
            logical_key = f"{hit_logical_id}:{profile_name}:{profile_version}"

        clip_logical_id = self._ensure_clip_logical(project_id, logical_key)

        # Deterministic input fingerprint for the clip version.
        clip_input_fp = _hash_fingerprint(
            "hc_clip_input",
            project_id,
            logical_key,
            ytid,
            f"{float(start_sec):.3f}",
            f"{float(end_sec):.3f}",
            _stable_json(hit_snapshot or {}),
            str(profile_id or ""),
            str(profile_name or ""),
            str(profile_version or ""),
            _stable_json(provenance or {}),
        )

        latest = self._latest_version_row(clip_logical_id)
        if latest and latest.get("input_fingerprint") == clip_input_fp and latest.get("status") != "deleted":
            return str(latest["clip_id"])

        next_ver = int(latest["clip_version"]) + 1 if latest else 1

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_clips (
                        project_id, run_id, hit_id,
                        clip_logical_id, clip_version,
                        hit_snapshot,
                        profile_id, profile_name, profile_version,
                        ytid, start_sec, end_sec, label,
                        status,
                        provenance,
                        validity_status,
                        input_fingerprint
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s,
                        %s::jsonb,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s,
                        %s::jsonb,
                        'valid',
                        %s
                    )
                    RETURNING clip_id
                    """,
                    (
                        project_id,
                        run_id,
                        hit_id,
                        clip_logical_id,
                        next_ver,
                        psycopg2.extras.Json(hit_snapshot or {}),
                        profile_id,
                        profile_name,
                        int(profile_version) if profile_version is not None else None,
                        ytid,
                        float(start_sec),
                        float(end_sec),
                        label,
                        status,
                        psycopg2.extras.Json(provenance or {}),
                        clip_input_fp,
                    ),
                )
                return str(cur.fetchone()[0])

    def create_rebuild_version(
        self,
        *,
        source_clip_id: str,
        run_id: Optional[str],
        actor: str,
        reason: Optional[str],
        render_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a new clip version under the same logical id.

        This does **not** render automatically; it only creates a planned
        version with provenance linking back to the source clip.
        """
        src = self.get_clip(source_clip_id)
        if not src:
            raise ValueError(f"clip not found: {source_clip_id}")

        clip_logical_id = str(src["clip_logical_id"])
        latest = self._latest_version_row(clip_logical_id)
        next_ver = int(latest["clip_version"]) + 1 if latest else int(src.get("clip_version") or 1) + 1

        prov = dict(src.get("provenance") or {})
        prov.update(
            {
                "rebuild": {
                    "source_clip_id": str(source_clip_id),
                    "actor": actor,
                    "reason": reason,
                    "render_params": render_params or {},
                }
            }
        )

        # Preserve the snapshot + profile identity; rendering params are stored
        # separately (used to build the render fingerprint).
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_clips (
                        project_id, run_id, hit_id,
                        clip_logical_id, clip_version,
                        hit_snapshot,
                        profile_id, profile_name, profile_version,
                        ytid, start_sec, end_sec, label,
                        status,
                        asset_path, asset_meta,
                        provenance,
                        validity_status,
                        input_fingerprint
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s,
                        %s::jsonb,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        'planned',
                        NULL, '{}'::jsonb,
                        %s::jsonb,
                        'valid',
                        %s
                    )
                    RETURNING clip_id
                    """,
                    (
                        str(src["project_id"]),
                        run_id,
                        src.get("hit_id"),
                        clip_logical_id,
                        next_ver,
                        psycopg2.extras.Json(src.get("hit_snapshot") or {}),
                        src.get("profile_id"),
                        src.get("profile_name"),
                        src.get("profile_version"),
                        src.get("ytid"),
                        float(src.get("start_sec") or 0.0),
                        float(src.get("end_sec") or 0.0),
                        src.get("label"),
                        psycopg2.extras.Json(prov),
                        _hash_fingerprint("hc_clip_input", "rebuild", source_clip_id, _stable_json({
                            "hit_id": src.get("hit_id"),
                            "hit_snapshot": src.get("hit_snapshot") or {},
                            "profile_id": src.get("profile_id"),
                            "profile_name": src.get("profile_name"),
                            "profile_version": src.get("profile_version"),
                            "ytid": src.get("ytid"),
                            "start_sec": float(src.get("start_sec") or 0.0),
                            "end_sec": float(src.get("end_sec") or 0.0),
                            "label": src.get("label"),
                            "render_params": render_params or {},
                        })),
                    ),
                )
                return str(cur.fetchone()[0])

    # ------------------------------------------------------------------
    # Lifecycle transitions for rendering
    # ------------------------------------------------------------------

    def transition_status(
        self,
        *,
        clip_id: str,
        from_status: str,
        to_status: str,
        actor: str,
        reason: Optional[str] = None,
        event_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Atomic status transition with an audit event."""
        from dal.hc_events import HCArtifactEventRepository

        event_meta = event_meta or {}

        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if to_status == "rendering":
                    cur.execute(
                        """
                        UPDATE hc_clips
                        SET status = %s,
                            render_started_at = COALESCE(render_started_at, NOW()),
                            render_completed_at = NULL,
                            render_error = NULL,
                            updated_at = NOW()
                        WHERE clip_id = %s AND status = %s
                        RETURNING project_id
                        """,
                        (to_status, clip_id, from_status),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE hc_clips
                        SET status = %s, updated_at = NOW()
                        WHERE clip_id = %s AND status = %s
                        RETURNING project_id
                        """,
                        (to_status, clip_id, from_status),
                    )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"clip status transition failed ({from_status} -> {to_status}) for {clip_id}")
                project_id = str(row["project_id"])

        try:
            HCArtifactEventRepository().add_event(
                project_id=project_id,
                entity_type="clip",
                entity_id=str(clip_id),
                event_type="lifecycle",
                from_state=from_status,
                to_state=to_status,
                actor=actor,
                reason=reason,
                event_meta=event_meta,
            )
        except Exception:
            # Audit trail must never block state transitions.
            pass

    def mark_render_result(
        self,
        *,
        clip_id: str,
        status: str,
        asset_path: Optional[str],
        asset_meta: Optional[Dict[str, Any]],
        provenance_patch: Optional[Dict[str, Any]],
        actor: str,
        error: Optional[str] = None,
    ) -> None:
        from dal.hc_events import HCArtifactEventRepository

        asset_meta = asset_meta or {}
        provenance_patch = provenance_patch or {}

        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT project_id, provenance, status FROM hc_clips WHERE clip_id = %s", (clip_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"clip not found: {clip_id}")
                project_id = str(row["project_id"])
                prev_status = str(row["status"])
                prov = dict(row.get("provenance") or {})
                prov.update(provenance_patch)

                cur.execute(
                    """
                    UPDATE hc_clips
                    SET status = %s,
                        asset_path = %s,
                        asset_meta = %s::jsonb,
                        provenance = %s::jsonb,
                        render_error = %s,
                        render_completed_at = COALESCE(render_completed_at, NOW()),
                        updated_at = NOW()
                    WHERE clip_id = %s
                    """,
                    (
                        status,
                        asset_path,
                        psycopg2.extras.Json(asset_meta),
                        psycopg2.extras.Json(prov),
                        error,
                        clip_id,
                    ),
                )

        try:
            HCArtifactEventRepository().add_event(
                project_id=project_id,
                entity_type="clip",
                entity_id=str(clip_id),
                event_type="render_result" if status in ("ready", "failed") else "lifecycle",
                from_state=prev_status,
                to_state=status,
                actor=actor,
                reason=error or None,
                event_meta={"asset_path": asset_path, "asset_meta": asset_meta},
            )
        except Exception:
            pass

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
        source: Dict[str, Any],
    ) -> int:
        """Invalidate clips produced by a node_key.

        - soft: mark invalid but keep usable metadata
        - strict: mark invalid and also move to stale-ish state by setting status=deleted
        - manual: mark invalid only (no other action)
        """
        from dal.hc_events import HCArtifactEventRepository

        validity = "invalid"
        new_status = None
        if mode == "strict":
            new_status = "deleted"

        with get_connection() as conn:
            with conn.cursor() as cur:
                if new_status:
                    cur.execute(
                        """
                        UPDATE hc_clips
                        SET validity_status = %s,
                            invalidated_at = NOW(),
                            invalidation_reason = %s,
                            invalidation_source = %s::jsonb,
                            status = %s,
                            updated_at = NOW()
                        WHERE project_id = %s
                          AND (provenance->>'node_key') = %s
                          AND validity_status <> 'invalid'
                        """,
                        (validity, reason, psycopg2.extras.Json(source), new_status, project_id, node_key),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE hc_clips
                        SET validity_status = %s,
                            invalidated_at = NOW(),
                            invalidation_reason = %s,
                            invalidation_source = %s::jsonb,
                            updated_at = NOW()
                        WHERE project_id = %s
                          AND (provenance->>'node_key') = %s
                          AND validity_status <> 'invalid'
                        """,
                        (validity, reason, psycopg2.extras.Json(source), project_id, node_key),
                    )
                count = cur.rowcount

        # Best-effort audit event (one event that summarizes the bulk change)
        try:
            HCArtifactEventRepository().add_event(
                project_id=project_id,
                entity_type="clip",
                entity_id=node_key,
                event_type="invalidation",
                from_state="valid",
                to_state="invalid",
                actor=actor,
                reason=reason,
                event_meta={"mode": mode, "count": count, "source": source},
            )
        except Exception:
            pass

        return int(count)

    def count_invalid_for_node(self, project_id: str, node_key: str) -> int:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM hc_clips
                    WHERE project_id = %s
                      AND validity_status = 'invalid'
                      AND (provenance->>'node_key') = %s
                    """,
                    (project_id, node_key),
                )
                return int(cur.fetchone()[0])
