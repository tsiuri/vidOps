"""
Hot target scheduler for batching and rate-limiting detail passes.

Simple scheduler abstraction with:
- Priority-based execution
- Rate limiting (delays between runs)
- Sequential or batch execution modes
- No external dependencies
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum


class ExecutionMode(Enum):
    """Execution mode for scheduler."""
    SEQUENTIAL = "sequential"  # Run one at a time
    BATCH = "batch"  # Run all at once (current behavior)
    PRIORITY = "priority"  # Run in priority order


@dataclass
class ScheduledTask:
    """A scheduled hot target task."""
    target: Dict[str, Any]
    priority: int = 0  # Higher priority runs first
    delay_before: float = 0  # Seconds to wait before running
    max_retries: int = 0  # Not implemented yet, for future
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Result of executing a task."""
    target_name: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    execution_time: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class HotTargetScheduler:
    """
    Scheduler for hot target detail passes.

    Configuration:
    {
        "mode": "sequential",  // "sequential", "batch", or "priority"
        "rate_limit": {
            "delay_between_targets": 0.5,  // seconds between each target
            "max_concurrent": 1,  // for future parallel execution
        },
        "priority": {
            "high": ["conflict_detection", "political_topics"],
            "normal": ["drama_detection"],
            "low": ["family_mentions"]
        }
    }
    """

    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.BATCH,
        delay_between: float = 0,
        log_mode: str = "quiet"
    ):
        """
        Initialize scheduler.

        Args:
            mode: Execution mode (sequential, batch, priority)
            delay_between: Seconds to wait between targets (for sequential/priority)
            log_mode: Logging level (quiet, progress, verbose)
        """
        self.mode = mode
        self.delay_between = delay_between
        self.log_mode = log_mode
        self.priority_map: Dict[str, int] = {}

    def set_priority_map(self, priority_config: Dict[str, List[str]]):
        """
        Set priority levels for targets.

        Args:
            priority_config: Dict mapping priority level to list of target names
                Example: {
                    "high": ["conflict_detection"],
                    "normal": ["drama_detection"],
                    "low": ["family_mentions"]
                }
        """
        # Convert to numeric priorities
        priority_values = {
            "critical": 100,
            "high": 50,
            "normal": 0,
            "low": -50
        }

        for level, targets in priority_config.items():
            priority_value = priority_values.get(level, 0)
            for target_name in targets:
                self.priority_map[target_name] = priority_value

    def schedule_targets(
        self,
        triggered_targets: List[Dict[str, Any]]
    ) -> List[ScheduledTask]:
        """
        Create scheduled tasks from triggered targets.

        Args:
            triggered_targets: List of targets that were triggered

        Returns:
            List of ScheduledTask objects ready for execution
        """
        tasks = []

        for target in triggered_targets:
            name = target.get("name") or target.get("category") or "unknown"

            # Get priority (from target config or priority map)
            priority = target.get("priority", self.priority_map.get(name, 0))

            # Get delay (from target config or default)
            delay = target.get("delay_before", 0)

            task = ScheduledTask(
                target=target,
                priority=priority,
                delay_before=delay,
                metadata={
                    "trigger_result": target.get("_trigger_result", {})
                }
            )
            tasks.append(task)

        # Sort by priority (highest first) if in priority mode
        if self.mode == ExecutionMode.PRIORITY:
            tasks.sort(key=lambda t: t.priority, reverse=True)

        return tasks

    def execute_tasks(
        self,
        tasks: List[ScheduledTask],
        executor: Callable[[Dict[str, Any]], Any],
        **executor_kwargs
    ) -> List[TaskResult]:
        """
        Execute scheduled tasks using provided executor function.

        Args:
            tasks: List of ScheduledTask to execute
            executor: Function that executes a single target
                Should accept (target_dict, **kwargs) and return results
            **executor_kwargs: Additional arguments to pass to executor

        Returns:
            List of TaskResult objects
        """
        results = []

        if not tasks:
            return results

        if self.log_mode in ("progress", "verbose"):
            mode_str = self.mode.value
            print(f"[scheduler] Executing {len(tasks)} target(s) in {mode_str} mode")

        for i, task in enumerate(tasks):
            target_name = task.target.get("name") or task.target.get("category") or "unknown"

            # Apply task-specific delay
            if task.delay_before > 0:
                if self.log_mode == "verbose":
                    print(f"[scheduler] Waiting {task.delay_before}s before {target_name}")
                time.sleep(task.delay_before)

            # Execute
            start_time = time.time()
            success = False
            result_data = None
            error_msg = None

            try:
                if self.log_mode in ("progress", "verbose"):
                    print(f"[scheduler] Executing {target_name} ({i+1}/{len(tasks)})")

                result_data = executor(task.target, **executor_kwargs)
                success = True

            except Exception as exc:
                success = False
                error_msg = str(exc)
                if self.log_mode in ("progress", "verbose"):
                    print(f"[scheduler] Error executing {target_name}: {exc}")

            execution_time = time.time() - start_time

            results.append(TaskResult(
                target_name=target_name,
                success=success,
                result=result_data,
                error=error_msg,
                execution_time=execution_time,
                metadata=task.metadata
            ))

            # Apply delay between targets (except for last one)
            if self.delay_between > 0 and i < len(tasks) - 1:
                if self.log_mode == "verbose":
                    print(f"[scheduler] Rate limit delay: {self.delay_between}s")
                time.sleep(self.delay_between)

        if self.log_mode in ("progress", "verbose"):
            successful = sum(1 for r in results if r.success)
            print(f"[scheduler] Completed {successful}/{len(results)} target(s) successfully")

        return results

    @classmethod
    def from_config(cls, config: Dict[str, Any], log_mode: str = "quiet") -> HotTargetScheduler:
        """
        Create scheduler from configuration dict.

        Args:
            config: Scheduler configuration
                {
                    "mode": "sequential",
                    "rate_limit": {
                        "delay_between_targets": 0.5
                    },
                    "priority": {
                        "high": ["conflict_detection"],
                        "normal": ["drama_detection"]
                    }
                }
            log_mode: Logging level

        Returns:
            Configured HotTargetScheduler instance
        """
        # Parse mode
        mode_str = config.get("mode", "batch")
        try:
            mode = ExecutionMode(mode_str)
        except ValueError:
            mode = ExecutionMode.BATCH

        # Parse rate limit config
        rate_config = config.get("rate_limit", {})
        delay_between = rate_config.get("delay_between_targets", 0)

        scheduler = cls(
            mode=mode,
            delay_between=delay_between,
            log_mode=log_mode
        )

        # Set priority map if provided
        priority_config = config.get("priority", {})
        if priority_config:
            scheduler.set_priority_map(priority_config)

        return scheduler
