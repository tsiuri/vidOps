# vidops/workers/exceptions.py
"""
Custom exceptions for VidOps worker system.

Distinguishes between:
- WorkerLocalError: Worker-specific failures (release job, shutdown worker)
- JobDataError: Job-specific failures (mark job failed, continue processing)
"""


class WorkerLocalError(Exception):
    """
    Indicates a failure local to this worker.

    When raised:
    - Job should be released back to PENDING (not marked FAILED)
    - Worker should shut down gracefully
    - Another healthy worker can retry the job

    Examples:
    - Disk full on local machine
    - GPU unavailable on this worker
    - Required mount missing
    - Permission denied on local tmp/
    """
    pass


class DiskSpaceError(WorkerLocalError):
    """Local disk is full or insufficient space."""
    pass


class GPUUnavailableError(WorkerLocalError):
    """GPU not available on this worker."""
    pass


class MountUnavailableError(WorkerLocalError):
    """Required storage mount is missing or inaccessible."""
    pass


class LocalPermissionError(WorkerLocalError):
    """Permission denied on local resources (tmp/, workspace, etc.)."""
    pass


class JobDataError(Exception):
    """
    Indicates a problem with the job data itself.

    When raised:
    - Job should be marked FAILED
    - Worker continues processing other jobs

    Examples:
    - Source file doesn't exist (in central storage)
    - Malformed job config
    - Invalid YTID
    - Referenced data missing from database
    """
    pass
