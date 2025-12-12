"""Minimal VidOps jobs-table adapter for manual enqueue/claim/complete of analysis jobs.

This is a skeletal helper to bridge into the VidOps Overlord queue without
implementing full worker heartbeats. Use it to push or pull `analysis_llm`
jobs by hand while the real `vo_cli.py` worker is being wired.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any, Dict, List, Optional

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError as exc:  # optional dependency; required for this helper
    psycopg2 = None  # type: ignore
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None

# Priority numeric mapping (higher = earlier)
PRIORITY_MAP = {
    "critical": 100,
    "high": 50,
    "normal": 0,
    "low": -50,
}


def _ensure_psycopg() -> None:
    if psycopg2 is None:
        raise RuntimeError(
            "psycopg2 is required for VidOps jobs-table access "
            f"(import error: {IMPORT_ERROR})"
        )


class VidOpsJobsQueue:
    """Skeletal adapter around the VidOps `jobs` table (no heartbeat/monitoring)."""

    def __init__(self, dsn: str, job_type: str = "analysis_llm", worker_id: str = "analysis_subworker") -> None:
        _ensure_psycopg()
        self.dsn = dsn
        self.job_type = job_type
        self.worker_id = worker_id

    def _conn(self):
        return psycopg2.connect(self.dsn)

    def enqueue(
        self,
        config: Dict[str, Any],
        priority_label: str = "normal",
        ytid: Optional[str] = None,
        media_path: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> str:
        job_id = job_id or str(uuid.uuid4())
        priority = PRIORITY_MAP.get(priority_label, 0)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (job_id, job_type, status, priority, ytid, media_path, config, created_at, updated_at)
                VALUES (%s, %s, 'pending', %s, %s, %s, %s::jsonb, now(), now())
                """,
                (job_id, self.job_type, priority, ytid, media_path, json.dumps(config)),
            )
        return job_id

    def claim_one(self, lease_seconds: int = 900, include_expired: bool = True) -> Optional[Dict[str, Any]]:
        """Claim a single pending (or expired) job of this job_type."""
        if include_expired:
            status_predicate = (
                " (status = 'pending' OR (status IN ('claimed','running') "
                "AND (lease_expires_at IS NULL OR lease_expires_at < now()))) "
            )
        else:
            status_predicate = " status = 'pending' "
        with self._conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                WITH cte AS (
                    SELECT job_id
                    FROM jobs
                    WHERE job_type = %s
                      AND {status_predicate}
                    ORDER BY priority DESC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE jobs j
                SET status = 'running',
                    claimed_by = %s,
                    claimed_at = now(),
                    started_at = COALESCE(started_at, now()),
                    lease_expires_at = now() + (%s || ' seconds')::interval,
                    updated_at = now()
                FROM cte
                WHERE j.job_id = cte.job_id
                RETURNING j.job_id, j.config, j.ytid, j.media_path, j.priority, j.created_at
                """,
                (self.job_type, self.worker_id, str(lease_seconds)),
            )
            row = cur.fetchone()
            if not row:
                return None
            config = row["config"]
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except Exception:
                    pass
            return {
                "job_id": row["job_id"],
                "ytid": row["ytid"],
                "media_path": row["media_path"],
                "priority": row["priority"],
                "config": config,
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            }

    def complete(self, job_id: str, result: Optional[Dict[str, Any]] = None, status: str = "completed", error: Optional[str] = None) -> None:
        """Mark a job as completed/failed; no worker heartbeats are emitted."""
        result_json = json.dumps(result or {})
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = %s,
                    result = %s::jsonb,
                    error_message = %s,
                    completed_at = now(),
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE job_id = %s
                """,
                (status, result_json, error, job_id),
            )


def _parse_json_arg(value: str) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {exc}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Minimal VidOps jobs-table subworker helper (enqueue/claim/complete).")
    parser.add_argument("--dsn", required=True, help="Postgres DSN pointing at VidOps DB (with jobs table).")
    parser.add_argument("--job-type", default="analysis_llm", help="Job type to enqueue/claim (default: analysis_llm).")
    parser.add_argument("--worker-id", default="analysis_subworker", help="Worker identifier stored in claimed_by.")

    sub = parser.add_subparsers(dest="command", required=True)

    p_enq = sub.add_parser("enqueue", help="Enqueue a new job into VidOps jobs table.")
    p_enq.add_argument("--priority", default="normal", choices=list(PRIORITY_MAP.keys()))
    p_enq.add_argument("--config-json", type=_parse_json_arg, default={}, help="Job config JSON payload.")
    p_enq.add_argument("--config-file", type=str, help="Path to JSON file for config.")
    p_enq.add_argument("--ytid", type=str, help="Optional YouTube ID.")
    p_enq.add_argument("--media-path", type=str, help="Optional media path.")
    p_enq.add_argument("--job-id", type=str, help="Optional job_id override.")

    p_claim = sub.add_parser("claim", help="Claim one pending job of the given type.")
    p_claim.add_argument("--lease-seconds", type=int, default=900)
    p_claim.add_argument("--no-expired", action="store_true", help="Do not reclaim expired leases; only fresh pending.")

    p_complete = sub.add_parser("complete", help="Mark a job completed with optional result JSON.")
    p_complete.add_argument("job_id")
    p_complete.add_argument("--status", default="completed", choices=["completed", "failed", "cancelled"])
    p_complete.add_argument("--result-json", type=_parse_json_arg, default={}, help="Result JSON to store.")
    p_complete.add_argument("--error", type=str, help="Error message if failing.")

    args = parser.parse_args(argv)
    queue = VidOpsJobsQueue(dsn=args.dsn, job_type=args.job_type, worker_id=args.worker_id)

    if args.command == "enqueue":
        cfg = args.config_json
        if args.config_file:
            with open(args.config_file, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        job_id = queue.enqueue(
            config=cfg,
            priority_label=args.priority,
            ytid=args.ytid,
            media_path=args.media_path,
            job_id=args.job_id,
        )
        print(job_id)
        return 0

    if args.command == "claim":
        job = queue.claim_one(lease_seconds=args.lease_seconds, include_expired=not args.no_expired)
        if not job:
            print("{}")
            return 0
        print(json.dumps(job, default=str))
        return 0

    if args.command == "complete":
        queue.complete(job_id=args.job_id, result=args.result_json, status=args.status, error=args.error)
        return 0

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    sys.exit(main())
