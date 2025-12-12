"""Shared GPU task queue (in-memory with optional Postgres-backed adapter).

This is an initial scaffold; not yet wired into the pipeline.
"""

from __future__ import annotations

import heapq
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # optional; Postgres backend is best-effort
    psycopg2 = None  # type: ignore


# Priority numeric mapping (higher = earlier)
PRIORITY_MAP = {
    "critical": 100,
    "high": 50,
    "normal": 0,
    "low": -50,
}


@dataclass(order=True)
class Task:
    sort_index: int = field(init=False, repr=False)
    priority: int
    created_at: float
    task_id: str
    payload: Dict[str, Any] = field(compare=False)

    def __post_init__(self) -> None:
        # heapq sorts ascending; negate to make higher priority pop first
        self.sort_index = -self.priority


class InMemoryGPUQueue:
    """Simple in-memory priority queue."""

    def __init__(self) -> None:
        self._heap: List[Task] = []

    def enqueue(self, payload: Dict[str, Any], priority_label: str = "normal") -> str:
        task_id = payload.get("task_id") or str(uuid.uuid4())
        pr = PRIORITY_MAP.get(priority_label, 0)
        t = Task(priority=pr, created_at=time.time(), task_id=task_id, payload=payload)
        heapq.heappush(self._heap, t)
        return task_id

    def dequeue(self) -> Optional[Task]:
        if not self._heap:
            return None
        return heapq.heappop(self._heap)

    def __len__(self) -> int:
        return len(self._heap)

    def complete(self, task_id: str, status: str = "done") -> None:
        """No-op for in-memory queue; tasks are removed on dequeue."""
        return

    def reset(self) -> None:
        """Clear the in-memory queue."""
        self._heap.clear()


class PostgresGPUQueue:
    """Postgres-backed queue using SKIP LOCKED leasing (schema not auto-created)."""

    def __init__(self, dsn: str, table: str = "gpu_task_queue") -> None:
        if psycopg2 is None:
            raise RuntimeError("psycopg2 not installed")
        self.dsn = dsn
        self.table = table

    def _conn(self):
        return psycopg2.connect(self.dsn)

    def enqueue(self, payload: Dict[str, Any], priority_label: str = "normal", lease_seconds: int = 300) -> str:
        task_id = payload.get("task_id") or str(uuid.uuid4())
        pr = PRIORITY_MAP.get(priority_label, 0)
        now = int(time.time())
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self.table}
                    (task_id, payload, priority, status, created_at, lease_expires_at, attempts)
                VALUES (%s, %s, %s, 'pending', to_timestamp(%s), NULL, 0)
                """,
                (task_id, json.dumps(payload), pr, now),
            )
        return task_id

    def dequeue(self, lease_seconds: int = 300) -> Optional[Tuple[str, Dict[str, Any]]]:
        with self._conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE {self.table} AS q
                SET status = 'leased',
                    lease_expires_at = now() + interval '%s seconds',
                    attempts = attempts + 1
                WHERE q.task_id = (
                    SELECT task_id
                    FROM {self.table}
                    WHERE status IN ('pending','leased')
                      AND (lease_expires_at IS NULL OR lease_expires_at < now())
                    ORDER BY priority DESC, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING q.task_id, q.payload
                """,
                (lease_seconds,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    pass
            return row["task_id"], payload

    def complete(self, task_id: str, status: str = "done") -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {self.table} SET status=%s, lease_expires_at=NULL WHERE task_id=%s",
                (status, task_id),
            )


class GPUSchedulerQueue:
    """
    Facade over in-memory or Postgres-backed queue.

    This is a stub for future integration; currently not wired into the pipeline.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        backend = cfg.get("queue", {}).get("backend", "memory")
        if backend == "postgres":
            dsn = cfg.get("queue", {}).get("dsn")
            table = cfg.get("queue", {}).get("table", "gpu_task_queue")
            self.backend = PostgresGPUQueue(dsn=dsn, table=table)
        else:
            self.backend = InMemoryGPUQueue()

    def enqueue(self, payload: Dict[str, Any], priority_label: str = "normal") -> str:
        return self.backend.enqueue(payload, priority_label=priority_label)

    def dequeue(self) -> Optional[Any]:
        return self.backend.dequeue()
