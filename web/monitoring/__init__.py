"""
Monitoring and observability for the distributed analysis worker.

This package provides:
- Prometheus metrics collection (vidops/monitoring/metrics.py)
- HTTP metrics exporter (vidops/monitoring/exporter.py)
- Grafana dashboard templates (vidops/monitoring/dashboards/)
"""

from .metrics import (
    WORKER_REGISTRY,
    tasks_claimed_total,
    tasks_completed_total,
    tasks_failed_total,
    task_processing_duration_seconds,
    pending_tasks_gauge,
    worker_uptime_seconds,
    worker_memory_usage_bytes,
    worker_cpu_usage_percent,
    llm_inference_duration_seconds,
    llm_model_available,
    db_query_duration_seconds,
    jobs_aggregated_total,
    errors_total,
    record_task_execution,
    record_llm_inference,
    record_db_query,
    record_error,
)

__all__ = [
    "WORKER_REGISTRY",
    "tasks_claimed_total",
    "tasks_completed_total",
    "tasks_failed_total",
    "task_processing_duration_seconds",
    "pending_tasks_gauge",
    "worker_uptime_seconds",
    "worker_memory_usage_bytes",
    "worker_cpu_usage_percent",
    "llm_inference_duration_seconds",
    "llm_model_available",
    "db_query_duration_seconds",
    "jobs_aggregated_total",
    "errors_total",
    "record_task_execution",
    "record_llm_inference",
    "record_db_query",
    "record_error",
]
