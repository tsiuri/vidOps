# vidops/dal/hc_dag.py

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from db import get_connection


class HCDagRepository:
    """Repository for hc_dag_nodes, hc_dag_edges, hc_node_executions.

    Phase 3/4 semantics rely on the execution records storing fingerprints for
    invalidation-aware runs. This repo is intentionally thin: it does not
    interpret the DAG; it only persists + reads.
    """

    # ------------------------------------------------------------------
    # Nodes / edges
    # ------------------------------------------------------------------

    def list_nodes(self, project_id: str) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_dag_nodes
                    WHERE project_id = %s
                    ORDER BY created_at ASC
                    """,
                    (project_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def list_edges(self, project_id: str) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_dag_edges
                    WHERE project_id = %s
                    ORDER BY created_at ASC
                    """,
                    (project_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def upsert_node(
        self,
        *,
        project_id: str,
        node_key: str,
        node_type: str,
        display_name: Optional[str] = None,
        config_json: Optional[Dict[str, Any]] = None,
        invalidation_mode: Optional[str] = None,
        zero_results_mode: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> str:
        """Insert or update a DAG node by (project_id, node_key)."""
        config_json = config_json or {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_dag_nodes (
                        project_id, node_key, node_type, display_name,
                        config_json,
                        invalidation_mode,
                        zero_results_mode,
                        enabled
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s::jsonb,
                        COALESCE(%s, 'soft'),
                        COALESCE(%s, 'ok'),
                        COALESCE(%s, TRUE)
                    )
                    ON CONFLICT (project_id, node_key) DO UPDATE SET
                        node_type = EXCLUDED.node_type,
                        display_name = EXCLUDED.display_name,
                        config_json = EXCLUDED.config_json,
                        invalidation_mode = COALESCE(EXCLUDED.invalidation_mode, hc_dag_nodes.invalidation_mode),
                        zero_results_mode = COALESCE(EXCLUDED.zero_results_mode, hc_dag_nodes.zero_results_mode),
                        enabled = COALESCE(EXCLUDED.enabled, hc_dag_nodes.enabled),
                        updated_at = NOW()
                    RETURNING node_id
                    """,
                    (
                        project_id,
                        node_key,
                        node_type,
                        display_name or node_key,
                        psycopg2.extras.Json(config_json),
                        invalidation_mode,
                        zero_results_mode,
                        enabled,
                    ),
                )
                return str(cur.fetchone()[0])

    def get_nodes_edges(self, project_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return self.list_nodes(project_id), self.list_edges(project_id)

    # ------------------------------------------------------------------
    # Executions
    # ------------------------------------------------------------------

    def get_latest_node_execution(self, node_id: str) -> Optional[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM hc_node_executions
                    WHERE node_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (node_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def list_node_executions(self, run_id: str) -> List[Dict[str, Any]]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT ne.*, n.node_key, n.node_type
                    FROM hc_node_executions ne
                    JOIN hc_dag_nodes n ON n.node_id = ne.node_id
                    WHERE ne.run_id = %s
                    ORDER BY ne.created_at ASC
                    """,
                    (run_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def create_node_execution(
        self,
        run_id: str,
        node_id: str,
        *,
        input_summary: Optional[Dict[str, Any]] = None,
        config_fingerprint: Optional[str] = None,
        upstream_fingerprint: Optional[str] = None,
        input_fingerprint: Optional[str] = None,
    ) -> str:
        input_summary = input_summary or {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hc_node_executions (
                        run_id, node_id,
                        input_summary,
                        config_fingerprint, upstream_fingerprint, input_fingerprint
                    ) VALUES (
                        %s, %s,
                        %s::jsonb,
                        %s, %s, %s
                    )
                    RETURNING exec_id
                    """,
                    (
                        run_id,
                        node_id,
                        psycopg2.extras.Json(input_summary),
                        config_fingerprint,
                        upstream_fingerprint,
                        input_fingerprint,
                    ),
                )
                return str(cur.fetchone()[0])

    def update_node_execution(
        self,
        exec_id: str,
        status: str,
        *,
        output_summary: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        output_fingerprint: Optional[str] = None,
    ) -> None:
        output_summary = output_summary or {}

        # status-dependent timestamps
        started_sql = "started_at = COALESCE(started_at, NOW())," if status == "running" else ""
        completed_sql = "completed_at = COALESCE(completed_at, NOW())," if status in ("completed", "failed", "blocked", "skipped") else ""

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE hc_node_executions
                    SET status = %s,
                        {started_sql}
                        {completed_sql}
                        output_summary = %s::jsonb,
                        error_message = %s,
                        output_fingerprint = COALESCE(%s, output_fingerprint)
                    WHERE exec_id = %s
                    """,
                    (
                        status,
                        psycopg2.extras.Json(output_summary),
                        error_message,
                        output_fingerprint,
                        exec_id,
                    ),
                )
