# Worker Error Handling & Local Failure Detection

This document explains the error handling system for VidOps workers, how to distinguish worker-local failures from job-specific failures, and how to integrate this system into services.

## Table of Contents

- [Overview](#overview)
- [Exception Classes](#exception-classes)
- [How It Works](#how-it-works)
- [Health Check Utilities](#health-check-utilities)
- [Workspace Monitoring](#workspace-monitoring)
- [Integration Guide](#integration-guide)
- [Unimplemented Features](#unimplemented-features)
- [Examples](#examples)

---

## Overview

### The Problem

When a job fails, we need to determine:
- **Is the worker broken?** → Release job back to queue, shutdown worker
- **Is the job bad?** → Mark job as FAILED, continue processing other jobs

Without this distinction:
- Bad jobs get repeatedly failed by all workers
- Worker issues cause jobs to be incorrectly marked as FAILED
- Healthy workers waste time on jobs that require specific resources

### The Solution

A two-tier exception system:

1. **`WorkerLocalError`** - Worker-specific failures
   - Job is released back to PENDING (not marked FAILED)
   - Worker shuts down gracefully
   - Another healthy worker can retry the job

2. **`JobDataError`** - Job-specific failures
   - Job is marked FAILED
   - Worker continues processing other jobs

---

## Exception Classes

### Location: `vidops/exceptions.py`

```python
from vidops.exceptions import (
    WorkerLocalError,      # Base class for local failures
    DiskSpaceError,        # Disk full
    GPUUnavailableError,   # GPU not available
    MountUnavailableError, # Storage mount missing
    LocalPermissionError,  # Permission denied on local resources
    JobDataError,          # Job is malformed/invalid
)
```

### When to Use Each

| Exception | When to Raise | What Happens |
|-----------|---------------|--------------|
| `DiskSpaceError` | Local disk is full | Job released, worker shutdown |
| `GPUUnavailableError` | GPU not available on this worker | Job released, worker shutdown |
| `MountUnavailableError` | Required mount point missing | Job released, worker shutdown |
| `LocalPermissionError` | Can't write to tmp/, workspace | Job released, worker shutdown |
| `JobDataError` | Source file doesn't exist, invalid config | Job marked FAILED, worker continues |

---

## How It Works

### 1. Pre-flight Health Checks

**Location:** `vidops/workers/general.py` - `_preflight_checks()`

Before claiming any jobs, workers verify:
- Workspace directory exists
- `tmp/` directory is writable
- Sufficient disk space (30GB minimum)

If checks fail, worker refuses to start:
```
============================================================
  ✗ INSUFFICIENT SPACE IN WORKING DRIVE!
============================================================
  Insufficient disk space on /home/user/vidops: 15.23GB free, 30.00GB required
  Workspace: /home/user/vidops
============================================================
```

**Performance:** Pre-flight checks are extremely fast (~40 microseconds for disk check).

### 2. Job Processing Exception Handling

**Location:** `vidops/workers/general.py` - `_process_single_job()`

```python
try:
    service.process_job(job)

except WorkerLocalError as exc:
    # Worker is broken - release job and shutdown
    logger.error("Worker-local failure: %s", exc)
    job_repo.release(job.job_id)  # Back to PENDING
    shutdown_requested = True      # Shutdown worker

except Exception as exc:
    # Job is broken - mark failed and continue
    logger.error("Job failure: %s", exc)
    job_repo.update_status(job.job_id, JobStatus.FAILED)
    # Worker continues processing other jobs
```

### 3. Workspace Size Monitoring

**Location:** `vidops/workers/general.py` - `_check_workspace_size()`

Monitors disk usage during idle time:
- Checks every 150 heartbeats (~5 minutes with 2s heartbeat)
- Fast `tmp/` check (10ms)
- Full workspace check (400ms)
- Worker shuts down if limits exceeded

**Configuration:** `config.yaml`
```yaml
workspace:
  max_workspace_size_gb: 100.0      # Full workspace limit
  max_tmp_size_gb: 50.0             # tmp/ specific limit
  size_check_interval: 150          # Check frequency
  monitor_tmp_separately: true
```

---

## Health Check Utilities

### Location: `vidops/utils/local_health.py`

### Available Functions

#### `check_disk_space(path, min_gb=30.0)`

Verifies sufficient free disk space.

```python
from vidops.utils.local_health import check_disk_space
from pathlib import Path

# Raises DiskSpaceError if insufficient
check_disk_space(Path("/workspace"), min_gb=50.0)
```

**Performance:** ~40 microseconds (instant)
**Method:** `os.statvfs()` - reads filesystem metadata

#### `check_write_permission(path)`

Verifies write access to a directory.

```python
from vidops.utils.local_health import check_write_permission

# Raises LocalPermissionError if can't write
check_write_permission(Path("/workspace/tmp"))
```

#### `check_mount_available(path)`

Verifies mount point is accessible.

```python
from vidops.utils.local_health import check_mount_available

# Raises MountUnavailableError if missing
check_mount_available(Path("/mnt/storage"))
```

#### `check_gpu_available(device=None)`

Verifies GPU availability.

```python
from vidops.utils.local_health import check_gpu_available

# Raises GPUUnavailableError if unavailable
check_gpu_available("cuda:0")
```

#### `wrap_os_error(error, path)`

Converts `OSError` to appropriate exception.

```python
from vidops.utils.local_health import wrap_os_error
import errno

try:
    # Some operation that might fail
    path.write_text("data")
except OSError as e:
    raise wrap_os_error(e, path) from e
```

Automatically detects:
- `ENOSPC` → `DiskSpaceError`
- `EACCES` on local paths → `LocalPermissionError`
- `ENOENT` on mounts → `MountUnavailableError`

---

## Workspace Monitoring

### Disk Space vs Workspace Size

Two different measurements:

| Metric | Method | Speed | What It Measures |
|--------|--------|-------|------------------|
| **Disk space** | `os.statvfs()` | 40 μs | Free space on filesystem |
| **Workspace size** | `du -sb` | 400 ms | Used space in directory |

**When to use each:**
- `check_disk_space()` - Before writing files (instant)
- Workspace monitoring - Periodic background check (slow)

### Configuration Options

**Disable monitoring entirely:**
```yaml
workspace:
  max_workspace_size_gb: 0  # Disables all monitoring
```

**Monitor only tmp/:**
```yaml
workspace:
  monitor_tmp_separately: true
  max_tmp_size_gb: 30.0
  max_workspace_size_gb: 0  # Disable full workspace check
```

**Check more frequently:**
```yaml
workspace:
  size_check_interval: 300  # Every 10 minutes (with 2s heartbeat)
```

**Environment variable overrides:**
```bash
export VIDOPS_MAX_WORKSPACE_SIZE_GB=150
export VIDOPS_MAX_TMP_SIZE_GB=75
export VIDOPS_WORKSPACE_CHECK_INTERVAL=200
```

---

## Integration Guide

### For Service Developers

Services should raise appropriate exceptions when detecting failures.

#### Example 1: Check Disk Space Before Large Operations

```python
from vidops.exceptions import DiskSpaceError
from vidops.utils.local_health import check_disk_space

def process_job(self, job: Job):
    workspace_root = self._workspace_root()

    # Check before downloading/staging large files
    check_disk_space(workspace_root, min_gb=10.0)

    # Proceed with normal processing
    audio_path = self._stage_media(job.media_path, workspace_root)
    # ...
```

**What happens:**
- If disk space < 10GB: Raises `DiskSpaceError`
- Worker catches `DiskSpaceError` (subclass of `WorkerLocalError`)
- Job is released to PENDING
- Worker shuts down
- Another worker with more space can retry

#### Example 2: Wrap File Operations

```python
from vidops.utils.local_health import wrap_os_error
import errno

def _stage_media(self, media_rel: str, workspace_root: Path) -> Path:
    try:
        local_path = workspace_root / "pull" / Path(media_rel).name

        # Try to copy from central storage
        shutil.copy2(central_path, local_path)

        return local_path

    except OSError as e:
        if e.errno == errno.ENOSPC:
            # Disk full - worker issue
            raise DiskSpaceError(f"No space left on device: {workspace_root}") from e
        elif e.errno == errno.EACCES and "tmp" in str(local_path):
            # Permission denied on local tmp - worker issue
            raise LocalPermissionError(f"Permission denied: {local_path}") from e
        elif e.errno == errno.ENOENT:
            # File doesn't exist - job issue
            raise JobDataError(f"Source file not found: {media_rel}") from e
        else:
            raise
```

#### Example 3: GPU Availability Check

```python
from vidops.utils.local_health import check_gpu_available

def process_job(self, job: Job):
    device = job.config.get("device", "cuda:0")

    # Check GPU before starting processing
    if device.startswith("cuda"):
        check_gpu_available(device)

    # Proceed with GPU-based processing
    # ...
```

#### Example 4: Mount Checks

```python
from vidops.utils.local_health import check_mount_available

def _workspace_root(self) -> Path:
    central_storage = Path(config.paths.central_storage_root)

    # Verify mount is available
    check_mount_available(central_storage)

    return central_storage
```

### Common Patterns

#### Pattern 1: Pre-flight Checks in Service

```python
def process_job(self, job: Job):
    """Process a job with local health checks."""
    workspace_root = self._workspace_root()

    # Pre-flight checks
    check_disk_space(workspace_root, min_gb=30.0)
    check_write_permission(workspace_root / "tmp")

    # If we get here, worker is healthy - proceed
    # ...
```

#### Pattern 2: Distinguish Local vs Central Storage Issues

```python
def _stage_file(self, file_path: str, workspace_root: Path) -> Path:
    central_path = Path(config.paths.central_storage_root) / file_path
    local_path = workspace_root / "pull" / Path(file_path).name

    # Check if file exists in central storage
    if not central_path.exists():
        # Job issue - file doesn't exist at all
        raise JobDataError(f"File not found in central storage: {file_path}")

    # Try to copy to local workspace
    try:
        shutil.copy2(central_path, local_path)
    except OSError as e:
        # Local issue - disk full, permissions, etc.
        raise wrap_os_error(e, local_path) from e

    return local_path
```

#### Pattern 3: Resource-Specific Checks

```python
def process_transcription_job(self, job: Job):
    """Transcription requires GPU and lots of disk space."""
    workspace_root = self._workspace_root()

    # Transcription-specific checks
    check_gpu_available("cuda:0")           # Need GPU
    check_disk_space(workspace_root, min_gb=50.0)  # Need 50GB for temp files

    # Proceed with transcription
    # ...
```

---

## Unimplemented Features

### 1. Automatic Cleanup Before Termination

**Status:** Not implemented

**Concept:** Before shutting down due to workspace size limits, worker could:
- Clean up old temporary files
- Remove orphaned chunk directories
- Delete stale reference builder artifacts

**Implementation sketch:**

```python
def _cleanup_old_files(self, tmp_dir: Path, age_hours: int = 24) -> float:
    """
    Clean up files older than age_hours.
    Returns GB freed.
    """
    import time

    cutoff_time = time.time() - (age_hours * 3600)
    freed_gb = 0.0

    for item in tmp_dir.iterdir():
        if item.stat().st_mtime < cutoff_time:
            size = self._get_directory_size_gb(item) if item.is_dir() else item.stat().st_size / (1024**3)
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            freed_gb += size

    return freed_gb

def _check_workspace_size(self) -> bool:
    # ... existing size checks ...

    if workspace_size_gb > threshold:
        # Try cleanup before terminating
        logger.warning("Workspace size exceeds threshold, attempting cleanup...")
        freed_gb = self._cleanup_old_files(self.tmp_dir)
        logger.info(f"Cleanup freed {freed_gb:.2f}GB")

        # Recheck after cleanup
        workspace_size_gb = self._get_directory_size_gb(self.workspace_root)
        if workspace_size_gb > threshold:
            logger.error("Still over limit after cleanup. Terminating.")
            return False
        else:
            logger.info("Workspace size now within limits.")
            return True
```

**Configuration:**
```yaml
workspace:
  cleanup_before_shutdown: true
  cleanup_age_hours: 24
  min_free_after_cleanup_gb: 40.0
```

### 2. Circuit Breaker for Consecutive Failures

**Status:** Not implemented

**Concept:** Track consecutive failures and stop claiming jobs after N failures.

**Implementation sketch:**

```python
def __init__(self):
    # ... existing init ...
    self.consecutive_failures = 0
    self.failure_threshold = config.workers.failure_threshold or 5

def _process_single_job(self):
    # ... existing job processing ...

    try:
        service.process_job(job)
        self.consecutive_failures = 0  # Reset on success

    except WorkerLocalError as exc:
        # Worker-local failure
        self.consecutive_failures += 1
        logger.error(f"Local failure ({self.consecutive_failures}/{self.failure_threshold}): {exc}")

        if self.consecutive_failures >= self.failure_threshold:
            logger.error("Circuit breaker triggered - too many consecutive failures")
            self._shutdown_requested = True

        job_repo.release(job.job_id)

    except Exception as exc:
        # Job failure
        self.consecutive_failures += 1
        logger.error(f"Job failure ({self.consecutive_failures}/{self.failure_threshold}): {exc}")

        if self.consecutive_failures >= self.failure_threshold:
            logger.warning("Circuit breaker triggered - stopping job claims")
            self._shutdown_requested = True

        job_repo.update_status(job.job_id, JobStatus.FAILED)
```

**Configuration:**
```yaml
workers:
  failure_threshold: 5  # Stop after N consecutive failures
```

### 3. Pattern Detection for Bulk Failures

**Status:** Not implemented

**Concept:** Detect when the same error repeats and optionally bulk-fail similar jobs.

**Implementation sketch:**

```python
from collections import defaultdict

def __init__(self):
    # ... existing init ...
    self.error_patterns = defaultdict(list)
    self.pattern_threshold = 5

def _process_single_job(self):
    try:
        service.process_job(job)

    except Exception as exc:
        error_type = type(exc).__name__
        self.error_patterns[error_type].append(job.job_id)

        if len(self.error_patterns[error_type]) >= self.pattern_threshold:
            logger.warning(
                f"Detected pattern: {error_type} occurred {len(self.error_patterns[error_type])} times"
            )

            # Optionally bulk-fail similar pending jobs
            if config.workers.bulk_fail_on_pattern:
                similar_jobs = self._find_similar_pending_jobs(job)
                for similar_job in similar_jobs:
                    self.job_repo.update_status(
                        similar_job.job_id,
                        JobStatus.FAILED,
                        error_message=f"Bulk failed due to systematic {error_type}"
                    )
```

**Configuration:**
```yaml
workers:
  pattern_detection: true
  pattern_threshold: 5
  bulk_fail_on_pattern: false  # Conservative default
```

### 4. Health Check Logging/Metrics

**Status:** Not implemented

**Concept:** Track and expose health check results as metrics.

**Implementation sketch:**

```python
from vidops.monitoring.metrics import Gauge

workspace_disk_free_gb = Gauge(
    'vidops_workspace_disk_free_gb',
    'Free disk space in workspace (GB)',
    ['worker_id']
)

tmp_directory_size_gb = Gauge(
    'vidops_tmp_directory_size_gb',
    'Size of tmp/ directory (GB)',
    ['worker_id']
)

def _check_workspace_size(self):
    # ... existing checks ...

    # Export metrics
    workspace_disk_free_gb.labels(worker_id=self.worker_id).set(free_gb)
    tmp_directory_size_gb.labels(worker_id=self.worker_id).set(tmp_size_gb)
```

---

## Examples

### Example Service Integration

Here's a complete example of integrating error handling into a service:

```python
# vidops/services/my_service.py

import logging
import shutil
from pathlib import Path

from vidops.exceptions import (
    DiskSpaceError,
    GPUUnavailableError,
    JobDataError,
)
from vidops.utils.local_health import (
    check_disk_space,
    check_gpu_available,
    check_write_permission,
)
from vidops.models import Job, JobStatus

logger = logging.getLogger(__name__)


class MyService:
    """Example service with proper error handling."""

    def process_job(self, job: Job) -> None:
        """
        Process a job with local health checks.

        Raises:
            WorkerLocalError: If worker has local issues (disk, GPU, etc.)
            JobDataError: If job data is invalid
        """
        workspace_root = self._workspace_root()

        # Pre-flight checks for this job type
        logger.info(f"Running pre-flight checks for job {job.job_id}")
        self._preflight_checks(workspace_root, job)

        # Proceed with processing
        logger.info(f"Processing job {job.job_id}")

        try:
            # Stage input files
            input_file = self._stage_input(job.media_path, workspace_root)

            # Process
            output_file = self._do_processing(input_file, job)

            # Persist results
            self._persist_output(output_file, job)

            # Mark complete
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED)
            logger.info(f"Job {job.job_id} completed successfully")

        except (DiskSpaceError, GPUUnavailableError) as exc:
            # Worker-local errors - propagate up to worker
            logger.error(f"Worker-local failure: {exc}")
            raise

        except JobDataError as exc:
            # Job-specific errors - mark failed and continue
            logger.error(f"Job data error: {exc}")
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, str(exc))

        except Exception as exc:
            # Unexpected errors - mark failed and continue
            logger.error(f"Unexpected error: {exc}", exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, str(exc))

    def _preflight_checks(self, workspace_root: Path, job: Job) -> None:
        """
        Run pre-flight checks specific to this job type.

        Raises:
            DiskSpaceError: Insufficient disk space
            GPUUnavailableError: GPU not available
            LocalPermissionError: Can't write to workspace
        """
        # Need at least 20GB for this job type
        check_disk_space(workspace_root, min_gb=20.0)

        # Need write access to tmp/
        check_write_permission(workspace_root / "tmp")

        # If GPU job, verify GPU available
        if job.config.get("use_gpu", False):
            device = job.config.get("device", "cuda:0")
            check_gpu_available(device)

    def _stage_input(self, media_path: str, workspace_root: Path) -> Path:
        """
        Stage input file from central storage to local workspace.

        Raises:
            JobDataError: File doesn't exist in central storage
            DiskSpaceError: Not enough space to copy file
        """
        from vidops.config import load_config
        config = load_config()

        central_path = Path(config.paths.central_storage_root) / media_path
        local_path = workspace_root / "pull" / Path(media_path).name

        # Check if file exists in central storage
        if not central_path.exists():
            raise JobDataError(f"File not found in central storage: {media_path}")

        # Try to copy to local workspace
        try:
            shutil.copy2(central_path, local_path)
        except OSError as e:
            import errno
            if e.errno == errno.ENOSPC:
                raise DiskSpaceError(f"No space left on device: {workspace_root}") from e
            raise

        return local_path

    def _do_processing(self, input_file: Path, job: Job) -> Path:
        """
        Perform the actual processing.

        Raises:
            JobDataError: Invalid input file format
        """
        output_file = input_file.parent / f"{input_file.stem}_processed.mp4"

        # Check file is valid
        if not self._validate_input(input_file):
            raise JobDataError(f"Invalid input file format: {input_file}")

        # Do processing...
        # ...

        return output_file

    def _workspace_root(self) -> Path:
        """Get workspace root from environment."""
        import os
        return Path(os.environ.get("VIDOPS_PROJECT_ROOT", "."))
```

### Testing Error Handling

```python
# test_error_handling.py

from pathlib import Path
from vidops.exceptions import DiskSpaceError, JobDataError
from vidops.utils.local_health import check_disk_space

def test_disk_space_check():
    """Test that disk space check works."""
    workspace = Path("/home/billie/bq_netservices/vidops")

    try:
        check_disk_space(workspace, min_gb=30.0)
        print("✓ Sufficient disk space")
    except DiskSpaceError as e:
        print(f"✗ Insufficient disk space: {e}")

def test_job_vs_worker_error():
    """Demo of different error types."""
    from vidops.services.my_service import MyService

    service = MyService()

    # Simulate worker error
    try:
        check_disk_space(Path("/"), min_gb=999999999.0)
    except DiskSpaceError as e:
        print(f"Worker error (would release job): {e}")

    # Simulate job error
    try:
        if not Path("nonexistent_file.mp4").exists():
            raise JobDataError("Source file not found")
    except JobDataError as e:
        print(f"Job error (would mark failed): {e}")
```

---

## Summary

### Quick Reference

**For service developers:**
1. Import exceptions and health checks from `vidops.exceptions` and `vidops.utils.local_health`
2. Add pre-flight checks before resource-intensive operations
3. Raise `WorkerLocalError` subclasses for worker issues
4. Raise `JobDataError` for job issues
5. Let `GenericWorker` handle the exception and decide what to do

**For operators:**
1. Configure workspace limits in `config.yaml`
2. Monitor worker logs for health check failures
3. Check Prometheus metrics (if implemented)
4. Adjust thresholds based on job requirements

**Key files:**
- `vidops/exceptions.py` - Exception classes
- `vidops/utils/local_health.py` - Health check utilities
- `vidops/workers/general.py` - Worker exception handling
- `config.yaml` - Configuration

---

**Status:** Implemented (cleanup features pending)
