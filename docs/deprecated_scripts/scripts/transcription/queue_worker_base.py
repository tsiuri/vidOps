#!/usr/bin/env python3
"""
Base class for queue-based transcription workers.

This module provides the QueueWorkerBase abstract class that handles common
queue operations like worker registration, heartbeats, job claiming, and
job processing orchestration.

Usage:
    class MyWorker(QueueWorkerBase):
        def _process_job(self, job):
            # Process the job
            pass

        def _get_compute_type(self):
            return "float16"

        def _get_version(self):
            return "1.0"

    worker = MyWorker(worker_type="nvidia", gpu_index=0, model="medium")
    worker.run()
"""

import os
import sys
import time
import socket
import threading
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from pathlib import Path

# Add script directory to path
sys.path.insert(0, str(Path(__file__).parent))

from db_queue import TranscriptionQueue


class QueueWorkerBase(ABC):
    """Base class for queue-based transcription workers"""

    def __init__(
        self,
        worker_type: str,
        gpu_index: Optional[int] = None,
        model: str = "medium",
        heartbeat_interval: int = 30,
        poll_interval: int = 10
    ):
        """
        Initialize worker base.

        Args:
            worker_type: nvidia|cpu|amd
            gpu_index: GPU device index (None for CPU)
            model: Whisper model size
            heartbeat_interval: Seconds between heartbeats
            poll_interval: Seconds to wait when queue is empty
        """
        self.worker_type = worker_type
        self.gpu_index = gpu_index
        self.model = model
        self.heartbeat_interval = heartbeat_interval
        self.poll_interval = poll_interval

        # Generate worker ID
        hostname = socket.gethostname()
        if gpu_index is not None:
            self.worker_id = f"{hostname}-{worker_type}-gpu{gpu_index}"
        else:
            self.worker_id = f"{hostname}-{worker_type}-cpu"

        # Initialize queue
        self.queue = TranscriptionQueue()

        # Heartbeat thread
        self._running = False
        self._heartbeat_thread = None
        self._current_job = None

    def register(self):
        """Register worker with the queue"""
        gpu_uuid = self._get_gpu_uuid() if self.gpu_index is not None else None
        metadata = {
            'model': self.model,
            'compute_type': self._get_compute_type(),
            'version': self._get_version(),
            'python_version': sys.version.split()[0]
        }

        self.queue.register_worker(
            worker_id=self.worker_id,
            worker_type=self.worker_type,
            hostname=socket.gethostname(),
            gpu_index=self.gpu_index,
            gpu_uuid=gpu_uuid,
            metadata=metadata
        )
        print(f"[{self.worker_id}] Registered with queue")

    def start_heartbeat(self):
        """Start heartbeat thread"""
        self._running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True
        )
        self._heartbeat_thread.start()
        print(f"[{self.worker_id}] Heartbeat thread started")

    def stop_heartbeat(self):
        """Stop heartbeat thread"""
        self._running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
        print(f"[{self.worker_id}] Heartbeat thread stopped")

    def _heartbeat_loop(self):
        """Heartbeat background loop"""
        while self._running:
            try:
                stats = self._get_system_stats()
                status = 'busy' if self._current_job is not None else 'idle'
                self.queue.heartbeat(
                    worker_id=self.worker_id,
                    status=status,
                    stats=stats
                )
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[{self.worker_id}] Heartbeat error: {e}", file=sys.stderr)
                # Don't crash on heartbeat failure, just log and continue

            time.sleep(self.heartbeat_interval)

    def run(self, max_jobs: Optional[int] = None):
        """
        Main worker loop.

        Args:
            max_jobs: Maximum jobs to process before exiting (None = run forever)
        """
        self.register()
        self.start_heartbeat()

        jobs_processed = 0
        try:
            while max_jobs is None or jobs_processed < max_jobs:
                # Claim next job
                job = self.queue.claim(
                    worker_id=self.worker_id,
                    worker_type=self.worker_type,
                    lease_seconds=int(os.environ.get('WORKER_LEASE_SECONDS', 3600))
                )

                if not job:
                    print(f"[{self.worker_id}] No jobs available, waiting {self.poll_interval}s...")
                    time.sleep(self.poll_interval)
                    continue

                # Process job
                self._current_job = job
                try:
                    print(f"[{self.worker_id}] Processing job: {job['job_id']}")
                    self._process_job(job)
                    jobs_processed += 1
                    print(f"[{self.worker_id}] Completed job {jobs_processed}/{max_jobs or '∞'}: {job['job_id']}")
                except KeyboardInterrupt:
                    print(f"\n[{self.worker_id}] Interrupted during job processing")
                    # Mark job as pending so it can be retried
                    try:
                        with self.queue._conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    UPDATE transcribe_jobs
                                    SET status = 'pending', worker_id = NULL, lease_expires_at = NULL
                                    WHERE job_id = %s
                                    """,
                                    (job['job_id'],)
                                )
                    except:
                        pass
                    raise
                except Exception as e:
                    print(f"[{self.worker_id}] Job failed: {e}", file=sys.stderr)
                    import traceback
                    traceback.print_exc()
                    self.queue.fail_job(job['job_id'], str(e))
                finally:
                    self._current_job = None

        except KeyboardInterrupt:
            print(f"\n[{self.worker_id}] Interrupted, shutting down...")
        finally:
            self.stop_heartbeat()
            print(f"[{self.worker_id}] Processed {jobs_processed} jobs total")

    @abstractmethod
    def _process_job(self, job: Dict[str, Any]):
        """
        Process a single job (implemented by subclass).

        Args:
            job: Job dict with keys: job_id, media_path, model, language, output_format, options

        Raises:
            Exception if job processing fails
        """
        pass

    @abstractmethod
    def _get_compute_type(self) -> str:
        """Get compute type for this worker (e.g., 'float16', 'int8')"""
        pass

    @abstractmethod
    def _get_version(self) -> str:
        """Get worker version string"""
        pass

    def _get_gpu_uuid(self) -> Optional[str]:
        """Get GPU UUID (NVIDIA only)"""
        if self.gpu_index is None:
            return None
        try:
            import subprocess
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader',
                 f'--id={self.gpu_index}'],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except Exception:
            return None

    def _get_system_stats(self) -> Dict[str, Any]:
        """Collect system stats for heartbeat"""
        stats = {}

        try:
            import psutil
            stats['cpu_percent'] = psutil.cpu_percent()
            mem = psutil.virtual_memory()
            stats['memory_used_gb'] = round(mem.used / 1024**3, 2)
        except ImportError:
            pass

        if self.gpu_index is not None:
            try:
                import subprocess
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=utilization.gpu,memory.used',
                     '--format=csv,noheader,nounits', f'--id={self.gpu_index}'],
                    capture_output=True, text=True, check=True
                )
                util, mem = result.stdout.strip().split(',')
                stats['gpu_utilization'] = float(util)
                stats['gpu_memory_used_gb'] = round(float(mem) / 1024, 2)
            except Exception:
                pass

        return stats
