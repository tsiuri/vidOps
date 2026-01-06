# vidops/workers/general.py

import logging
import os
import platform
import signal
import subprocess
import time
import gc
import threading
import socket
import sys
from datetime import timedelta
from pathlib import Path
from typing import Callable, Dict, Optional

try:
    import psutil
except Exception:
    psutil = None

from configuration import load_config
from dal import JobRepository, WorkerRepository
from models import JobStatus, Worker, WorkerStatus
from exceptions import WorkerLocalError, DiskSpaceError
from services import (
    get_distributed_analysis_service,
    get_clipping_service,
    get_diarization_service,
    get_download_service,
    get_stitching_service,
    get_subtitle_service,
    get_transcription_service,
    get_voice_service,
    get_dates_service,
    get_extra_utils_service,
    get_hc_project_service,
)
from workers.heartbeat import WorkerHeartbeat
from utils.workspace_cleanup import WorkspaceCleanup, WorkspaceCleanupConfig

logger = logging.getLogger(__name__)


class GenericWorker:
    """
    Generic worker that stays untyped until a job is claimed.
    After each job it reverts to the starter "general" state so the next loop
    can service any job type in the queue.
    """

    def __init__(self, web_port: int = 5000, metrics_port: int = 8888):
        self.config = load_config()
        self.worker_repo = WorkerRepository()
        self.job_repo = JobRepository()
        self.worker_id = f"{self.config.workers.machine_alias}-general-{os.getpid()}"
        self.machine_alias = self.config.workers.machine_alias
        self.current_job_id: Optional[str] = None
        self.base_worker_type = "general"
        self._shutdown_requested = False
        self._shutdown_event = threading.Event()
        self.max_jobs = self.config.workers.max_jobs
        self.worker_obj = Worker(
            worker_id=self.worker_id,
            worker_type=self.base_worker_type,
            machine_alias=self.machine_alias,
            status=WorkerStatus.REGISTERING,
            vram_gb=self.config.analysis.default_vram_gb,
            pid=os.getpid(),
            hostname=platform.node(),
        )
        self.heartbeat = WorkerHeartbeat(
            worker_repo=self.worker_repo,
            job_repo=self.job_repo,
            worker_id=self.worker_id,
            interval_seconds=self.config.workers.heartbeat_interval,
            logger=logger,
        )
        self.service_factories: Dict[str, Callable] = {
            "download": get_download_service,
            "transcription": get_transcription_service,
            "clipping": get_clipping_service,
            "analysis": get_distributed_analysis_service,  # legacy alias
            "analysis-distributed": get_distributed_analysis_service,
            "diarization": get_diarization_service,
            "stitching": get_stitching_service,
            "dl_subs": get_subtitle_service,
            "convert_captions": get_subtitle_service,
            "voice": get_voice_service,
            "dates": get_dates_service,
            "extra_utils": get_extra_utils_service,
            "hc_project_run": get_hc_project_service,
        }
        self._services: Dict[str, object] = {}
        self._descendants_killed = False

        # Workspace monitoring
        self.heartbeat_counter = 0
        self.workspace_root = Path(os.environ.get("TOOL_ROOT") or Path(__file__).resolve().parents[1])
        self.tmp_dir = self.workspace_root / "tmp"

        # Housekeeping (idle maintenance tasks)
        self.idle_claim_failures = 0

        # Web and metrics servers
        self.web_port = web_port
        self.metrics_port = metrics_port
        self.web_thread: Optional[threading.Thread] = None
        self.metrics_thread: Optional[threading.Thread] = None

        self._register_signal_handlers()

    def _register_signal_handlers(self) -> None:
        """Register shutdown handlers in a cross-platform way."""
        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
            if sig is None:
                continue
            try:
                signal.signal(sig, self._handle_shutdown_signal)
            except (ValueError, OSError, AttributeError) as exc:
                logger.debug("Skipping signal handler for %s: %s", sig, exc)

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

        # Start web and metrics servers in background threads
        self._start_background_servers()

        self._register()
        self.heartbeat.start()
        jobs_processed = 0

        try:
            while not self._shutdown_requested:
                try:
                    processed = self._process_single_job()
                    if processed:
                        jobs_processed += 1
                        self.idle_claim_failures = 0  # Reset idle counter on successful claim
                        if 0 < self.max_jobs <= jobs_processed:
                            logger.info(
                                "Processed %s jobs (max_jobs=%s); shutting down",
                                jobs_processed,
                                self.max_jobs,
                            )
                            break
                        continue

                    # No job claimed; increment idle counter
                    self.idle_claim_failures += 1

                    # Trigger housekeeping if idle threshold reached
                    if self.idle_claim_failures >= self.config.housekeeping.trigger_idle_attempts:
                        logger.info(
                            "Idle for %d consecutive claim attempts; triggering housekeeping",
                            self.idle_claim_failures,
                        )
                        self._run_housekeeping()
                        self.idle_claim_failures = 0  # Reset after housekeeping

                    # Check workspace size (only checks every N heartbeats)
                    if not self._check_workspace_size():
                        logger.error("Workspace size limit exceeded. Shutting down worker.")
                        self._shutdown_requested = True
                        break

                    # Wait, but wake immediately on shutdown signal
                    self._shutdown_event.wait(self.config.workers.heartbeat_interval)
                except Exception as exc:
                    logger.error("Unhandled error in worker loop: %s", exc, exc_info=True)
                    self._update_state(WorkerStatus.ERRORED)
                    break
        except KeyboardInterrupt:
            logger.warning("KeyboardInterrupt received; shutting down worker")
            self._shutdown_requested = True
        finally:
            logger.info("Generic worker %s shutting down", self.worker_id)
            self.heartbeat.stop()
            self._update_state(WorkerStatus.STOPPING)

    # ------------------------------------------------------------------ internals
    def _register(self):
        self.worker_repo.register(self.worker_obj)
        logger.info("Registered worker %s (%s); waiting for jobs", self.worker_id, self.machine_alias)
        self._update_state(WorkerStatus.IDLE, worker_type=self.base_worker_type)

    def _run_housekeeping(self):
        """Run housekeeping tasks (maintenance while idle)."""
        try:
            from services.housekeeping import HousekeepingService
            from dal import VideoRepository

            video_repo = VideoRepository()
            housekeeping_service = HousekeepingService(video_repo, self.job_repo)
            jobs_enqueued = housekeeping_service.run_housekeeping()

            if jobs_enqueued > 0:
                logger.info(
                    "Housekeeping cycle complete: enqueued %d maintenance jobs",
                    jobs_enqueued,
                )
            else:
                logger.debug("Housekeeping cycle: no jobs to enqueue")
        except Exception as exc:
            logger.error("Housekeeping error: %s", exc, exc_info=True)

    def _start_background_servers(self):
        """Start web UI and metrics servers in background daemon threads."""
        # Start web server if port is enabled
        if self.web_port > 0:
            if self._port_is_available("127.0.0.1", self.web_port):
                self.web_thread = threading.Thread(
                    target=self._run_web_server,
                    daemon=True,
                    name="GenericWorker-WebUI",
                )
                self.web_thread.start()
                logger.info("Web UI server started on http://127.0.0.1:%d", self.web_port)
            else:
                logger.warning(
                    "Web UI port %d already in use; skipping web server startup",
                    self.web_port,
                )

        # Start metrics server if port is enabled
        if self.metrics_port > 0:
            if self._port_is_available("0.0.0.0", self.metrics_port):
                self.metrics_thread = threading.Thread(
                    target=self._run_metrics_server,
                    daemon=True,
                    name="GenericWorker-Metrics",
                )
                self.metrics_thread.start()
                logger.info("Metrics server started on http://0.0.0.0:%d/metrics", self.metrics_port)
            else:
                logger.warning(
                    "Metrics port %d already in use; skipping metrics server startup",
                    self.metrics_port,
                )

    def _port_is_available(self, host: str, port: int) -> bool:
        """Check if a port is available for listening."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                return True
        except OSError:
            return False

    def _run_web_server(self):
        """Run Flask web server for analysis config and drills (runs in background thread)."""
        try:
            import sys
            from pathlib import Path

            # Add project paths so the web app can find scripts/analysis modules
            web_dir = Path(__file__).parent.parent / "web"
            project_root = web_dir.parent
            sys.path.insert(0, str(project_root / "scripts" / "analysis"))
            sys.path.insert(0, str(project_root / "scripts"))
            sys.path.insert(0, str(web_dir / "scripts"))
            sys.path.insert(0, str(web_dir))

            from web_app import app

            # Run Flask in this thread (daemon, so won't block shutdown)
            app.run(host="127.0.0.1", port=self.web_port, debug=False, use_reloader=False)
        except Exception as e:
            logger.error("Failed to start web server: %s", e, exc_info=True)

    def _run_metrics_server(self):
        """Run Prometheus metrics server (runs in background thread)."""
        try:
            from monitoring.exporter import start_metrics_server

            # Start metrics server (blocking call, runs in this thread)
            start_metrics_server(host="0.0.0.0", port=self.metrics_port)
        except Exception as e:
            logger.error("Failed to start metrics server: %s", e, exc_info=True)

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
            worker_vram_gb=self.worker_obj.vram_gb,
        )
        if not job:
            return False

        self.current_job_id = job.job_id
        self.heartbeat.set_current_job(job.job_id)
        claimed_type = job.job_type or self.base_worker_type
        self._update_state(WorkerStatus.BUSY, job.job_id, worker_type=claimed_type)
        job_released = False
        job_failed = False

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
                job_released = True
                # Signal shutdown
                self._shutdown_requested = True
            except KeyboardInterrupt:
                logger.warning("KeyboardInterrupt received; releasing job %s back to pending", job.job_id)
                try:
                    self.job_repo.release(job.job_id)
                    job_released = True
                except Exception as exc:
                    logger.error("Failed to release job %s after interrupt: %s", job.job_id, exc, exc_info=True)
                self._shutdown_requested = True
            except Exception as exc:
                logger.error("Error processing job %s: %s", job.job_id, exc, exc_info=True)
                # individual services should update job status, but ensure it's failed
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message=str(exc),
                )
                job_failed = True

        finally:
            self.current_job_id = None
            self.heartbeat.set_current_job(None)
            self._update_state(
                WorkerStatus.IDLE,
                job_id=None,
                worker_type=self.base_worker_type,
            )
            if job_released:
                logger.info("Job %s released; worker %s returned to IDLE", job.job_id, self.worker_id)
            elif job_failed:
                refreshed = self.job_repo.get(job.job_id) if job else None
                logger.info(
                    "Job %s finished with status %s; worker %s returned to IDLE",
                    job.job_id,
                    refreshed.status if refreshed else "failed",
                    self.worker_id,
                )
            else:
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

            # Free any cached GPU memory between jobs
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

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
            from utils.local_health import check_disk_space, check_write_permission

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
            check_disk_space(self.workspace_root, min_gb=20.0)
            logger.info("  ✓ Sufficient disk space (20GB minimum)")

            # Optional cleanup if workspace is above the configured trigger
            if not self._maybe_cleanup_workspace(prompt=True):
                return False

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

    def _maybe_cleanup_workspace(self, prompt: bool = True, current_size: Optional[float] = None) -> bool:
        """
        Run a targeted cleanup if workspace size exceeds the configured trigger.
        Returns True to continue startup, False to abort.
        """
        current_size = current_size if current_size is not None else self._get_directory_size_gb(self.workspace_root)
        cfg = getattr(self.config.workspace, "tmp_cleanup", None)
        get_val = (
            (lambda key, default=None: cfg.get(key, default))
            if isinstance(cfg, dict)
            else (lambda key, default=None: getattr(cfg, key, default))
        )
        enabled = bool(cfg and get_val("enabled", False))
        trigger_gb = float(get_val("trigger_workspace_size_gb", 0.0) or 0.0)

        logger.info(
            "Workspace size at startup: %.2fGB (cleanup %s; trigger: %.2fGB)",
            current_size,
            "enabled" if enabled else "disabled",
            trigger_gb,
        )

        if not enabled:
            return True

        if trigger_gb <= 0:
            return True

        if current_size <= trigger_gb:
            logger.info(
                "Cleanup not triggered: workspace %.2fGB <= trigger %.2fGB",
                current_size,
                trigger_gb,
            )
            return True

        cleanup = WorkspaceCleanup(
            WorkspaceCleanupConfig(
                enabled=True,
                trigger_workspace_size_gb=trigger_gb,
                min_bytes=int(get_val("min_bytes", 50_000_000)),
                max_deletions=int(get_val("max_deletions", 200)),
                min_age_minutes=int(get_val("min_age_minutes", 120)),
                paths=list(get_val("paths", [])),
                skip_exts=list(get_val("skip_exts", [])),
            )
        )

        stats = cleanup.run(self.workspace_root, self.tmp_dir)
        new_size = self._get_directory_size_gb(self.workspace_root)
        logger.info(
            "Workspace cleanup finished: %.2fGB -> %.2fGB (removed=%s, freed=%.2fGB)",
            current_size,
            new_size,
            stats.get("files_removed", 0),
            stats.get("bytes_freed", 0) / (1024 ** 3),
        )

        limit = float(self.config.workspace.max_workspace_size_gb or 0.0)
        # If no hard limit configured, fall back to the cleanup trigger as the threshold for prompting.
        prompt_limit = limit if limit > 0 else trigger_gb

        if prompt_limit <= 0 or new_size <= prompt_limit:
            return True

        if prompt and sys.stdin and sys.stdin.isatty():
            prompt_msg = (
                f"Workspace remains above limit: {new_size:.2f}GB > {prompt_limit:.2f}GB. "
                "Continue anyway? [y/N]: "
            )
            try:
                resp = input(prompt_msg).strip().lower()
            except Exception:
                resp = ""
            if resp in {"y", "yes"}:
                logger.warning(
                    "Continuing with workspace size %.2fGB above limit %.2fGB",
                    new_size,
                    prompt_limit,
                )
                return True
            logger.error(
                "Startup aborted: workspace size %.2fGB exceeds limit %.2fGB and user declined to continue",
                new_size,
                prompt_limit,
            )
            return False

        if prompt:
            logger.error(
                "Startup aborted: workspace size %.2fGB exceeds limit %.2fGB (no TTY for confirmation)",
                new_size,
                prompt_limit,
            )
            return False

        # Non-interactive call (e.g., heartbeat): return False to signal over-limit
        logger.error(
            "Workspace still above limit after cleanup: %.2fGB > %.2fGB",
            new_size,
            prompt_limit,
        )
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
                        f"({self.config.workspace.max_tmp_size_gb}GB). Attempting cleanup."
                    )
                    cleaned = self._maybe_cleanup_workspace(prompt=False)
                    if cleaned:
                        tmp_size_gb = self._get_directory_size_gb(self.tmp_dir)
                        logger.info("Post-cleanup tmp/ size: %.2fGB", tmp_size_gb)
                        if tmp_size_gb <= self.config.workspace.max_tmp_size_gb:
                            return True
                    logger.error(
                        f"tmp/ directory size ({tmp_size_gb:.2f}GB) still exceeds limit "
                        f"({self.config.workspace.max_tmp_size_gb}GB). Worker terminating."
                    )
                    return False

            # Check full workspace size
            workspace_size_gb = self._get_directory_size_gb(self.workspace_root)
            logger.info(f"Workspace size: {workspace_size_gb:.2f}GB / {self.config.workspace.max_workspace_size_gb}GB limit")

            if workspace_size_gb > self.config.workspace.max_workspace_size_gb:
                logger.error(
                    f"Workspace size ({workspace_size_gb:.2f}GB) exceeds limit "
                    f"({self.config.workspace.max_workspace_size_gb}GB). Attempting cleanup."
                )
                cleaned = self._maybe_cleanup_workspace(prompt=False, current_size=workspace_size_gb)
                if cleaned:
                    workspace_size_gb = self._get_directory_size_gb(self.workspace_root)
                    logger.info("Post-cleanup workspace size: %.2fGB", workspace_size_gb)
                    if workspace_size_gb <= self.config.workspace.max_workspace_size_gb:
                        return True
                logger.error(
                    f"Workspace size ({workspace_size_gb:.2f}GB) still exceeds limit "
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
        Get directory size in GB while staying on the same filesystem. Falls back to 0 on error.
        """
        try:
            root_dev = directory.stat().st_dev
        except Exception as exc:
            logger.warning(f"Error stat-ing {directory}: {exc}")
            return 0.0

        total_bytes = 0
        stack = [directory]
        try:
            while stack:
                current = stack.pop()
                try:
                    with os.scandir(current) as entries:
                        for entry in entries:
                            try:
                                stat = entry.stat(follow_symlinks=False)
                            except FileNotFoundError:
                                continue
                            except Exception as exc:
                                logger.debug("Error stat-ing %s: %s", entry.path, exc)
                                continue

                            if stat.st_dev != root_dev:
                                # Skip entries on other filesystems to mirror du -x
                                continue

                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            else:
                                total_bytes += stat.st_size
                except (FileNotFoundError, NotADirectoryError):
                    continue
                except Exception as exc:
                    logger.warning(f"Error traversing %s: %s", current, exc)
                    continue
        except Exception as exc:
            logger.warning(f"Error calculating size of {directory}: {exc}")
            return 0.0

        return total_bytes / (1024 ** 3)

    def _handle_shutdown_signal(self, signum, frame):
        if self._shutdown_requested:
            # Second signal: fall back to default to force-exit
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return
        self._shutdown_requested = True
        self._shutdown_event.set()
        logger.warning("Received signal %s, initiating shutdown", signum)
        if self.current_job_id:
            try:
                # Requeue the job with reduced priority so it doesn't block but can retry later
                self.job_repo.release(self.current_job_id)
            except Exception as exc:
                logger.error("Failed to cancel job %s: %s", self.current_job_id, exc, exc_info=True)
        self._update_state(WorkerStatus.STOPPING)
        self._kill_descendants()
        raise KeyboardInterrupt

    def _kill_descendants(self):
        """Best-effort kill any child processes (e.g., ffmpeg) on shutdown."""
        if self._descendants_killed:
            return
        try:
            to_kill = self._collect_descendants(os.getpid())
            if not to_kill:
                return
            if psutil:
                for pid in to_kill:
                    try:
                        proc = psutil.Process(pid)
                        proc.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                time.sleep(0.25)
                for pid in to_kill:
                    try:
                        proc = psutil.Process(pid)
                        if proc.is_running():
                            proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            else:
                sig_kill = getattr(signal, "SIGKILL", signal.SIGTERM)
                for sig in (signal.SIGTERM, sig_kill):
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
        """Collect descendant PIDs in a cross-platform way."""
        if psutil:
            try:
                proc = psutil.Process(pid)
                return [child.pid for child in proc.children(recursive=True)]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return []
            except Exception:
                return []
        return self._collect_descendants_proc(pid)

    def _collect_descendants_proc(self, pid: int) -> list[int]:
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
