"""
Prometheus metrics instrumentation for all vidops worker types.

This module provides metrics collection for:
- Task processing (counts, latency, success/failure rates)
- Worker health (uptime, queue depth, resource usage)
- Job processing (generic job metrics for all worker types)
- LLM integration (inference latency, model availability)
- Database operations (query latency, connection pool status)
"""

from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    CollectorRegistry,
)
from typing import Optional

# Create a dedicated registry for this worker
WORKER_REGISTRY = CollectorRegistry()

# =====================================================================
# Task Processing Metrics
# =====================================================================

tasks_claimed_total = Counter(
    name="analysis_worker_tasks_claimed_total",
    documentation="Total number of tasks claimed by this worker",
    labelnames=["worker_id", "pass_id"],
    registry=WORKER_REGISTRY,
)

tasks_completed_total = Counter(
    name="analysis_worker_tasks_completed_total",
    documentation="Total number of tasks completed successfully",
    labelnames=["worker_id", "pass_id", "status"],
    registry=WORKER_REGISTRY,
)

tasks_failed_total = Counter(
    name="analysis_worker_tasks_failed_total",
    documentation="Total number of tasks that failed",
    labelnames=["worker_id", "pass_id", "error_type"],
    registry=WORKER_REGISTRY,
)

task_processing_duration_seconds = Histogram(
    name="analysis_worker_task_processing_duration_seconds",
    documentation="Time taken to process a task (seconds)",
    labelnames=["worker_id", "pass_id"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=WORKER_REGISTRY,
)

task_processing_duration_summary = Summary(
    name="analysis_worker_task_processing_duration_summary",
    documentation="Summary of task processing duration (for detailed percentiles)",
    labelnames=["worker_id", "pass_id"],
    registry=WORKER_REGISTRY,
)

# =====================================================================
# Queue/Backlog Metrics
# =====================================================================

pending_tasks_gauge = Gauge(
    name="analysis_worker_pending_tasks",
    documentation="Number of pending tasks in the queue",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

claimed_but_not_completed_gauge = Gauge(
    name="analysis_worker_claimed_not_completed",
    documentation="Number of tasks claimed but not yet completed",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

job_progress_gauge = Gauge(
    name="analysis_worker_job_progress",
    documentation="Job progress: 0=started, 100=complete",
    labelnames=["worker_id", "job_id"],
    registry=WORKER_REGISTRY,
)

# =====================================================================
# Worker Health Metrics
# =====================================================================

worker_uptime_seconds = Gauge(
    name="analysis_worker_uptime_seconds",
    documentation="Time the worker has been running (seconds)",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

worker_current_task_gauge = Gauge(
    name="analysis_worker_current_task",
    documentation="Currently processing task ID (0 if idle)",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

worker_memory_usage_bytes = Gauge(
    name="analysis_worker_memory_usage_bytes",
    documentation="Current memory usage of the worker (bytes)",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

worker_cpu_usage_percent = Gauge(
    name="analysis_worker_cpu_usage_percent",
    documentation="Current CPU usage percentage",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

# =====================================================================
# LLM/Inference Metrics
# =====================================================================

llm_inference_duration_seconds = Histogram(
    name="analysis_worker_llm_inference_duration_seconds",
    documentation="Time taken for LLM inference (seconds)",
    labelnames=["worker_id", "model_name", "pass_id"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=WORKER_REGISTRY,
)

llm_inference_total = Counter(
    name="analysis_worker_llm_inference_total",
    documentation="Total number of LLM inferences performed",
    labelnames=["worker_id", "model_name", "pass_id", "status"],
    registry=WORKER_REGISTRY,
)

llm_model_available = Gauge(
    name="analysis_worker_llm_model_available",
    documentation="Whether the LLM model is available (1=yes, 0=no)",
    labelnames=["worker_id", "model_name"],
    registry=WORKER_REGISTRY,
)

llm_tokens_processed_total = Counter(
    name="analysis_worker_llm_tokens_processed_total",
    documentation="Total tokens sent to/processed by LLM",
    labelnames=["worker_id", "model_name", "direction"],  # direction: input or output
    registry=WORKER_REGISTRY,
)

# =====================================================================
# Database Metrics
# =====================================================================

db_query_duration_seconds = Histogram(
    name="analysis_worker_db_query_duration_seconds",
    documentation="Database query execution time (seconds)",
    labelnames=["worker_id", "query_type"],  # query_type: claim, update, select, etc
    buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0),
    registry=WORKER_REGISTRY,
)

db_connection_pool_size = Gauge(
    name="analysis_worker_db_connection_pool_size",
    documentation="Number of connections in the database pool",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

db_connection_errors_total = Counter(
    name="analysis_worker_db_connection_errors_total",
    documentation="Total number of database connection errors",
    labelnames=["worker_id", "error_type"],
    registry=WORKER_REGISTRY,
)

# =====================================================================
# Job Aggregation Metrics
# =====================================================================

jobs_aggregated_total = Counter(
    name="analysis_worker_jobs_aggregated_total",
    documentation="Total number of jobs that completed and were aggregated",
    labelnames=["worker_id", "status"],  # status: success or failed
    registry=WORKER_REGISTRY,
)

job_aggregation_duration_seconds = Histogram(
    name="analysis_worker_job_aggregation_duration_seconds",
    documentation="Time taken to aggregate a completed job (seconds)",
    labelnames=["worker_id"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0),
    registry=WORKER_REGISTRY,
)

# =====================================================================
# Error Tracking Metrics
# =====================================================================

errors_total = Counter(
    name="analysis_worker_errors_total",
    documentation="Total number of errors encountered",
    labelnames=["worker_id", "error_category"],  # error_category: llm, database, task_execution, etc
    registry=WORKER_REGISTRY,
)

last_error_timestamp = Gauge(
    name="analysis_worker_last_error_timestamp",
    documentation="Unix timestamp of the last error",
    labelnames=["worker_id", "error_category"],
    registry=WORKER_REGISTRY,
)

# =====================================================================
# Capability/Config Metrics
# =====================================================================

worker_capabilities_gauge = Gauge(
    name="analysis_worker_capabilities_count",
    documentation="Number of capabilities this worker declares",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

# =====================================================================
# Lease Management Metrics
# =====================================================================

task_lease_expired_total = Counter(
    name="analysis_worker_task_lease_expired_total",
    documentation="Total number of tasks with expired leases (reclaimed by another worker)",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)

average_lease_duration_minutes = Gauge(
    name="analysis_worker_average_lease_duration_minutes",
    documentation="Configured lease duration in minutes",
    labelnames=["worker_id"],
    registry=WORKER_REGISTRY,
)


# =====================================================================
# Generic Job Metrics (for all job types: diarization, transcription, etc)
# =====================================================================

jobs_claimed_total = Counter(
    name="vidops_jobs_claimed_total",
    documentation="Total number of jobs claimed by workers",
    labelnames=["worker_id", "job_type"],
    registry=WORKER_REGISTRY,
)

jobs_completed_total = Counter(
    name="vidops_jobs_completed_total",
    documentation="Total number of jobs completed",
    labelnames=["worker_id", "job_type", "status"],  # status: success or failed
    registry=WORKER_REGISTRY,
)

job_processing_duration_seconds = Histogram(
    name="vidops_job_processing_duration_seconds",
    documentation="Time taken to process a job (seconds)",
    labelnames=["worker_id", "job_type"],
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),  # Longer buckets for general jobs
    registry=WORKER_REGISTRY,
)

job_processing_duration_summary = Summary(
    name="vidops_job_processing_duration_summary",
    documentation="Summary of job processing duration (for detailed percentiles)",
    labelnames=["worker_id", "job_type"],
    registry=WORKER_REGISTRY,
)

generic_worker_uptime_seconds = Gauge(
    name="vidops_worker_uptime_seconds",
    documentation="Time the worker has been running (seconds)",
    labelnames=["worker_id", "worker_type"],
    registry=WORKER_REGISTRY,
)

generic_worker_memory_usage_bytes = Gauge(
    name="vidops_worker_memory_usage_bytes",
    documentation="Current memory usage of the worker (bytes)",
    labelnames=["worker_id", "worker_type"],
    registry=WORKER_REGISTRY,
)

generic_worker_cpu_usage_percent = Gauge(
    name="vidops_worker_cpu_usage_percent",
    documentation="Current CPU usage percentage",
    labelnames=["worker_id", "worker_type"],
    registry=WORKER_REGISTRY,
)

pending_jobs_gauge = Gauge(
    name="vidops_pending_jobs",
    documentation="Number of pending jobs in the queue",
    labelnames=["worker_type"],
    registry=WORKER_REGISTRY,
)

worker_current_job_gauge = Gauge(
    name="vidops_worker_current_job",
    documentation="Currently processing job ID (0 if idle)",
    labelnames=["worker_id", "worker_type"],
    registry=WORKER_REGISTRY,
)

generic_errors_total = Counter(
    name="vidops_errors_total",
    documentation="Total number of errors encountered",
    labelnames=["worker_id", "worker_type", "error_category"],
    registry=WORKER_REGISTRY,
)

# =====================================================================
# Helper Functions for Common Patterns
# =====================================================================

def record_task_execution(
    worker_id: str,
    pass_id: str,
    duration_seconds: float,
    status: str,
    error_type: Optional[str] = None,
) -> None:
    """
    Record a task execution with all relevant metrics.

    Args:
        worker_id: Unique worker identifier
        pass_id: Analysis pass identifier
        duration_seconds: Time taken to process task
        status: Result status (completed or failed)
        error_type: Type of error if failed (e.g., "timeout", "llm_error", "db_error")
    """
    task_processing_duration_seconds.labels(
        worker_id=worker_id,
        pass_id=pass_id,
    ).observe(duration_seconds)

    task_processing_duration_summary.labels(
        worker_id=worker_id,
        pass_id=pass_id,
    ).observe(duration_seconds)

    if status == "completed":
        tasks_completed_total.labels(
            worker_id=worker_id,
            pass_id=pass_id,
            status="success",
        ).inc()
    elif status == "failed":
        tasks_failed_total.labels(
            worker_id=worker_id,
            pass_id=pass_id,
            error_type=error_type or "unknown",
        ).inc()


def record_llm_inference(
    worker_id: str,
    model_name: str,
    pass_id: str,
    duration_seconds: float,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """
    Record LLM inference execution.

    Args:
        worker_id: Unique worker identifier
        model_name: Name of the model used
        pass_id: Analysis pass identifier
        duration_seconds: Inference time
        status: Result status (success or failed)
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
    """
    llm_inference_duration_seconds.labels(
        worker_id=worker_id,
        model_name=model_name,
        pass_id=pass_id,
    ).observe(duration_seconds)

    llm_inference_total.labels(
        worker_id=worker_id,
        model_name=model_name,
        pass_id=pass_id,
        status=status,
    ).inc()

    if input_tokens > 0:
        llm_tokens_processed_total.labels(
            worker_id=worker_id,
            model_name=model_name,
            direction="input",
        ).inc(input_tokens)

    if output_tokens > 0:
        llm_tokens_processed_total.labels(
            worker_id=worker_id,
            model_name=model_name,
            direction="output",
        ).inc(output_tokens)


def record_db_query(
    worker_id: str,
    query_type: str,
    duration_seconds: float,
) -> None:
    """
    Record database query execution.

    Args:
        worker_id: Unique worker identifier
        query_type: Type of query (claim, update, select, etc)
        duration_seconds: Query execution time
    """
    db_query_duration_seconds.labels(
        worker_id=worker_id,
        query_type=query_type,
    ).observe(duration_seconds)


def record_error(
    worker_id: str,
    error_category: str,
    timestamp: Optional[float] = None,
) -> None:
    """
    Record an error occurrence.

    Args:
        worker_id: Unique worker identifier
        error_category: Type of error (llm, database, task_execution, etc)
        timestamp: Unix timestamp (defaults to now)
    """
    import time

    errors_total.labels(
        worker_id=worker_id,
        error_category=error_category,
    ).inc()

    last_error_timestamp.labels(
        worker_id=worker_id,
        error_category=error_category,
    ).set(timestamp or time.time())


def record_generic_job_execution(
    worker_id: str,
    job_type: str,
    duration_seconds: float,
    status: str,
    error_category: Optional[str] = None,
) -> None:
    """
    Record a generic job execution for any worker type.

    Args:
        worker_id: Unique worker identifier
        job_type: Type of job (diarization, transcription, etc)
        duration_seconds: Time taken to process job
        status: Result status (success or failed)
        error_category: Type of error if failed
    """
    job_processing_duration_seconds.labels(
        worker_id=worker_id,
        job_type=job_type,
    ).observe(duration_seconds)

    job_processing_duration_summary.labels(
        worker_id=worker_id,
        job_type=job_type,
    ).observe(duration_seconds)

    jobs_completed_total.labels(
        worker_id=worker_id,
        job_type=job_type,
        status=status,
    ).inc()

    if status == "failed" and error_category:
        generic_errors_total.labels(
            worker_id=worker_id,
            worker_type=job_type,
            error_category=error_category,
        ).inc()
