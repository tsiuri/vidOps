"""GPU task scheduler facade using the shared queue.

Current scope: enqueue tasks (in-memory or Postgres) and process them immediately
in this process. This scaffolds a future multi-worker queue.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .gpu_queue import GPUSchedulerQueue, PRIORITY_MAP


class GPUScheduler:
    """Minimal scheduler that enqueues tasks and processes them inline."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, log_mode: str = "quiet") -> None:
        self.config = config or {}
        self.log_mode = log_mode
        self.queue = GPUSchedulerQueue(self.config)
        rate_cfg = (self.config.get("rate_limit") or {})
        self.delay_between = rate_cfg.get("delay_between_targets", 0)

    def run_tasks(self, tasks: List[Dict[str, Any]], executor: Callable[[Dict[str, Any]], Any]) -> List[Any]:
        """
        Enqueue tasks with priorities and process them inline.
        Tasks are dicts and must be self-contained for execution.
        """
        if not tasks:
            return []

        # Enqueue
        for task in tasks:
            pr_label = task.get("priority_label") or "normal"
            self.queue.enqueue(task, priority_label=pr_label)

        total_tasks = len(tasks)
        processed = 0
        results: List[Any] = []
        while True:
            item = self.queue.dequeue()
            if not item:
                break
            if isinstance(item, tuple):  # Postgres dequeue returns (task_id, payload)
                task_id, payload = item
            else:
                task_id, payload = item.task_id, item.payload  # type: ignore[attr-defined]
            start = time.time()
            try:
                res = executor(payload)
                success = True
                error = None
            except Exception as exc:  # pragma: no cover - executor may raise
                res = None
                success = False
                error = str(exc)
            duration = time.time() - start

            results.append(
                {"task_id": task_id, "success": success, "result": res, "error": error, "duration": duration, "payload": payload}
            )

            if self.log_mode in ("progress", "verbose"):
                ttype = payload.get("task_type", "task")
                cid = None
                try:
                    cid = payload.get("chunk", {}).get("chunk_id")
                except Exception:
                    cid = None
                msg = f"[{ttype}]"
                if cid is not None:
                    msg += f" chunk {cid}"
                msg += f" {'ok' if success else 'err'} ({duration:.2f}s)"
                processed += 1
                if total_tasks:
                    msg += f" [{processed}/{total_tasks}]"
                print(msg, flush=True)
            # Mark completion if backend supports it
            if hasattr(self.queue.backend, "complete"):
                try:
                    status = "done" if success else "failed"
                    self.queue.backend.complete(task_id, status=status)
                except Exception:
                    pass
            if self.delay_between > 0:
                time.sleep(self.delay_between)
        return results
