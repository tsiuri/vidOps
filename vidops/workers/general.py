# vidops/workers/general.py

import logging
import os
import signal
import subprocess
import time
import gc
from datetime import timedelta
from pathlib import Path
from typing import Callable, Dict, Optional

from vidops.config import load_config
from vidops.dal import JobRepository, WorkerRepository
from vidops.models import JobStatus, Worker, WorkerStatus
from vidops.exceptions import WorkerLocalError, DiskSpaceError
from vidops.services import (
    get_analysis_service,
    get_clipping_service,
    get_diarization_service,
    get_download_service,
    get_stitching_service,
    get_subtitle_service,
    get_transcription_service,
    get_voice_service,
    get_dates_service,
    get_extra_utils_service,
)

logger = logging.getLogger(__name__)


class GenericWorker:
    """
    Generic worker that stays untyped until a job is claimed.
    After each job it reverts to the starter "general" state so the next loop
    can service any job type in the queue.
    """

    def __init__(self):
        self.config = load_config()
        self.worker_repo = WorkerRepository()
        self.job_repo = JobRepository()
        self.worker_id = f"{self.config.workers.machine_alias}-general-{os.getpid()}"
        self.machine_alias = self.config.workers.machine_alias
        self.current_job_id: Optional[str] = None
        self.base_worker_type = "general"
        self._shutdown_requested = False
        self.max_jobs = self.config.workers.max_jobs
        self.worker_obj = Worker(
            worker_id=self.worker_id,
            worker_type=self.base_worker_type,
            machine_alias=self.machine_alias,
            status=WorkerStatus.REGISTERING,
            pid=os.getpid(),
            hostname=os.uname().nodename,
        )
        self.service_factories: Dict[str, Callable] = {
            "download": get_download_service,
            "transcription": get_transcription_service,
            "clipping": get_clipping_service,
            "analysis": get_analysis_service,
            "diarization": get_diarization_service,
            "stitching": get_stitching_service,
            "dl_subs": get_subtitle_service,
            "convert_captions": get_subtitle_service,
            "voice": get_voice_service,
            "dates": get_dates_service,
            "extra_utils": get_extra_utils_service,
        }
        self._services: Dict[str, object] = {}
        self._descendants_killed = False

        # Workspace monitoring
        self.heartbeat_counter = 0
        self.workspace_root = Path(os.environ.get("TOOL_ROOT", Path(__file__).parent.parent.parent))
        self.tmp_dir = self.workspace_root / "tmp"

        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)

    def run(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        logger.info("Generic worker %s starting on %s", self.worker_id, self.machine_alias)

        # Pre-flight health checks
        if not self._preflight_checks():
            logger.error("Pre-flight health checks failed. Worker cannot start.")
            return

        self._register()
        jobs_processed = 0

        while not self._shutdown_requested:
            try:
                processed = self._process_single_job()
                if processed:
                    jobs_processed += 1
                    if 0 < self.max_jobs <= jobs_processed:
                        logger.info(
                            "Processed %s jobs (max_jobs=%s); shutting down",
                            jobs_processed,
                            self.max_jobs,
                        )
                        break
                    continue

                # No job claimed; heartbeat + sleep
                self._heartbeat()

                # Check workspace size (only checks every N heartbeats)
                if not self._check_workspace_size():
                    logger.error("Workspace size limit exceeded. Shutting down worker.")
                    self._shutdown_requested = True
                    break

                time.sleep(self.config.workers.heartbeat_interval)
            except Exception as exc:
                logger.error("Unhandled error in worker loop: %s", exc, exc_info=True)
                self._update_state(WorkerStatus.ERRORED)
                break

        logger.info("Generic worker %s shutting down", self.worker_id)
        self._update_state(WorkerStatus.STOPPING)

    # ------------------------------------------------------------------ internals
    def _register(self):
        self.worker_repo.register(self.worker_obj)
        logger.info("Registered worker %s (%s); waiting for jobs", self.worker_id, self.machine_alias)
        self._update_state(WorkerStatus.IDLE, worker_type=self.base_worker_type)

    def _heartbeat(self):
        self.worker_repo.heartbeat(self.worker_id)
        logger.info("Heartbeat for worker %s", self.worker_id)

    def _update_state(
        self,
        status: WorkerStatus,
        job_id: Optional[str] = None,
        worker_type: Optional[str] = None,
    ):
        wt = worker_type or self.worker_obj.worker_type
        self.worker_obj.status = status
        self.worker_obj.current_job_id = job_id
        self.worker_obj.worker_type = wt
        self.worker_repo.update_status(
            self.worker_id,
            status,
            current_job_id=job_id,
            worker_type=wt,
        )

    def _process_single_job(self) -> bool:
        job = self.job_repo.claim_next(
            worker=self.worker_obj,
            lease_duration=timedelta(seconds=self.config.workers.heartbeat_interval * 4),
        )
        if not job:
            return False

        self.current_job_id = job.job_id
        claimed_type = job.job_type or self.base_worker_type
        self._update_state(WorkerStatus.BUSY, job.job_id, worker_type=claimed_type)

        try:
            service = self._get_service_for_job(claimed_type)
        except KeyError:
            error_msg = f"No service registered for job_type '{claimed_type}'"
            logger.error(error_msg)
            self.job_repo.update_status(
                job.job_id,
                JobStatus.FAILED,
                error_message=error_msg,
            )
        else:
            try:
                service.process_job(job)
            except WorkerLocalError as exc:
                # Worker-local failure - release job and shutdown worker
                logger.error(
                    "Worker-local failure detected: %s\n"
                    "Job '%s' will be released back to queue for another worker.\n"
                    "This worker is shutting down.",
                    exc,
                    job.job_id,
                    exc_info=True
                )
                # Release job back to PENDING
                self.job_repo.release(job.job_id)
                # Signal shutdown
                self._shutdown_requested = True
            except Exception as exc:
                logger.error("Error processing job %s: %s", job.job_id, exc, exc_info=True)
                # individual services should update job status, but ensure it's failed
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message=str(exc),
                )

        finally:
            self.current_job_id = None
            self._update_state(
                WorkerStatus.IDLE,
                job_id=None,
                worker_type=self.base_worker_type,
            )
            logger.info("Job %s completed and worker %s returned to IDLE", job.job_id, self.worker_id)
            # Clean up memory after each job to prevent accumulation
            self._cleanup_memory()
        return True

    def _get_service_for_job(self, job_type: str):
        if job_type not in self.service_factories:
            raise KeyError(job_type)
        if job_type not in self._services:
            self._services[job_type] = self.service_factories[job_type]()
        return self._services[job_type]

    def _cleanup_memory(self):
        """Clean up memory after processing a job to prevent accumulation."""
        try:
            # Run Python garbage collection
            gc.collect()

            # Clear CUDA cache if available
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    logger.debug("Cleared CUDA cache")
            except ImportError:
                pass  # torch not available, skip CUDA cleanup

            logger.debug("Memory cleanup completed")
        except Exception as exc:
            logger.warning("Error during memory cleanup: %s", exc)

    def _preflight_checks(self) -> bool:
        """
        Run pre-flight health checks before starting worker.
        Returns True if all checks pass, False if worker should not start.
        """
        try:
            from vidops.utils.local_health import check_disk_space, check_write_permission

            # Check workspace exists
            if not self.workspace_root.exists():
                logger.error(f"Workspace root does not exist: {self.workspace_root}")
                return False

            # Check tmp/ directory writable
            logger.info("Checking tmp/ directory...")
            check_write_permission(self.tmp_dir)
            logger.info("  ✓ tmp/ is writable")

            # Check disk space
            logger.info("Checking disk space...")
            check_disk_space(self.workspace_root, min_gb=30.0)
            logger.info("  ✓ Sufficient disk space (30GB minimum)")

            logger.info("Pre-flight checks passed")
            return True

        except DiskSpaceError as exc:
            # Special handling for disk space errors - nice terminal message
            logger.error("\n" + "=" * 60)
            logger.error("  ✗ INSUFFICIENT SPACE IN WORKING DRIVE!")
            logger.error("=" * 60)
            logger.error(f"  {exc}")
            logger.error(f"  Workspace: {self.workspace_root}")
            logger.error("=" * 60 + "\n")
            return False
        except Exception as exc:
            logger.error(f"Pre-flight check failed: {exc}", exc_info=True)
            return False

    def _check_workspace_size(self) -> bool:
        """
        Check if workspace size exceeds configured limits.
        Returns True if size is OK, False if limit exceeded (worker should terminate).
        """
        # Skip if monitoring is disabled
        if self.config.workspace.max_workspace_size_gb <= 0:
            return True

        # Only check every N heartbeats
        self.heartbeat_counter += 1
        if self.heartbeat_counter < self.config.workspace.size_check_interval:
            return True

        # Reset counter
        self.heartbeat_counter = 0

        try:
            # Check tmp/ separately if enabled (faster)
            if self.config.workspace.monitor_tmp_separately and self.config.workspace.max_tmp_size_gb > 0:
                tmp_size_gb = self._get_directory_size_gb(self.tmp_dir)
                logger.info(f"tmp/ size: {tmp_size_gb:.2f}GB / {self.config.workspace.max_tmp_size_gb}GB limit")

                if tmp_size_gb > self.config.workspace.max_tmp_size_gb:
                    logger.error(
                        f"tmp/ directory size ({tmp_size_gb:.2f}GB) exceeds limit "
                        f"({self.config.workspace.max_tmp_size_gb}GB). Worker terminating."
                    )
                    return False

            # Check full workspace size
            workspace_size_gb = self._get_directory_size_gb(self.workspace_root)
            logger.info(f"Workspace size: {workspace_size_gb:.2f}GB / {self.config.workspace.max_workspace_size_gb}GB limit")

            if workspace_size_gb > self.config.workspace.max_workspace_size_gb:
                logger.error(
                    f"Workspace size ({workspace_size_gb:.2f}GB) exceeds limit "
                    f"({self.config.workspace.max_workspace_size_gb}GB). Worker terminating."
                )
                return False

            return True

        except Exception as e:
            logger.warning(f"Error checking workspace size: {e}")
            # On error, continue running (don't kill worker due to monitoring failure)
            return True

    def _get_directory_size_gb(self, directory: Path) -> float:
        """
        Get directory size in GB using du command (fast: ~6ms for tmp/, ~800ms for full workspace).
        """
        try:
            result = subprocess.run(
                ["du", "-sb", str(directory)],
                capture_output=True,
                text=True,
                timeout=5,
                check=True
            )
            bytes_used = int(result.stdout.split()[0])
            return bytes_used / (1024 ** 3)
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout calculating size of {directory}")
            return 0.0
        except Exception as e:
            logger.warning(f"Error calculating size of {directory}: {e}")
            return 0.0

    def _handle_shutdown_signal(self, signum, frame):
        if self._shutdown_requested:
            # Second signal: fall back to default to force-exit
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return
        self._shutdown_requested = True
        logger.warning("Received signal %s, initiating shutdown", signum)
        if self.current_job_id:
            try:
                # Requeue the job with reduced priority so it doesn't block but can retry later
                self.job_repo.release(self.current_job_id)
            except Exception as exc:
                logger.error("Failed to cancel job %s: %s", self.current_job_id, exc, exc_info=True)
        self._update_state(WorkerStatus.STOPPING)
        self._kill_descendants()
        raise SystemExit(1)

    def _kill_descendants(self):
        """Best-effort kill any child processes (e.g., ffmpeg) on shutdown."""
        if self._descendants_killed:
            return
        try:
            to_kill = self._collect_descendants(os.getpid())
            for sig in (signal.SIGTERM, signal.SIGKILL):
                for pid in to_kill:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        continue
                time.sleep(0.25)
            self._descendants_killed = True
        except Exception as exc:
            logger.warning("Failed to kill descendants: %s", exc)

    def _collect_descendants(self, pid: int) -> list[int]:
        """Collect descendant PIDs via /proc."""
        descendants: list[int] = []
        try:
            children_path = f"/proc/{pid}/task/{pid}/children"
            with open(children_path, "r") as f:
                data = f.read().strip()
            for child_str in data.split():
                try:
                    child_pid = int(child_str)
                except ValueError:
                    continue
                descendants.append(child_pid)
                descendants.extend(self._collect_descendants(child_pid))
        except FileNotFoundError:
            return descendants
        except Exception:
            return descendants
        return descendants
