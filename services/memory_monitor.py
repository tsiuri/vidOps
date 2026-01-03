#!/usr/bin/env python3
"""
Memory monitoring for diarization processes.
Monitors child processes (especially ffmpeg) and terminates worker if memory exceeds limit.
"""

import os
import threading
import time
import logging
import signal
from typing import Optional, Set

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    psutil = None

logger = logging.getLogger(__name__)


class MemoryMonitor:
    """
    Monitors memory usage of the current process and its children (especially ffmpeg).
    Terminates the worker if memory exceeds the configured limit.
    
    Tracks processes across process groups and sessions to catch ffmpeg processes
    spawned by subprocesses with start_new_session=True.
    """

    def __init__(self, memory_limit_mb: int, check_interval: float = 1.0):
        """
        Args:
            memory_limit_mb: Maximum memory in MB before termination
            check_interval: How often to check memory (seconds)
        """
        if not HAS_PSUTIL:
            raise ImportError(
                "psutil is required for memory monitoring. "
                "Install it with: pip install psutil>=5.9.0"
            )
        self.memory_limit_mb = memory_limit_mb
        self.check_interval = check_interval
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.process = psutil.Process(os.getpid())
        self._stop_event = threading.Event()
        self._tracked_subprocess_pids: Set[int] = set()  # Track subprocess PIDs (like batch_diarize.py)
        self._last_warning_time: float = 0.0  # Throttle warning messages
        self._warned_ffmpeg_pids: Set[int] = set()  # Track which ffmpeg PIDs we've already warned about
        self._last_info_time: float = 0.0  # Throttle info messages

    def start(self):
        """Start monitoring in a background thread."""
        if self.monitoring:
            logger.warning("Memory monitor already running")
            return

        self.monitoring = True
        self._stop_event.clear()
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="MemoryMonitor"
        )
        self.monitor_thread.start()
        logger.info(f"Memory monitor started (limit: {self.memory_limit_mb} MB)")

    def stop(self):
        """Stop monitoring."""
        if not self.monitoring:
            return

        self.monitoring = False
        self._stop_event.set()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2.0)
        # Clear warning tracking when stopping
        self._warned_ffmpeg_pids.clear()
        self._tracked_subprocess_pids.clear()
        logger.info("Memory monitor stopped")

    def _monitor_loop(self):
        """Main monitoring loop that runs in background thread."""
        while not self._stop_event.is_set():
            try:
                total_memory_mb = self._get_total_memory_mb()
                
                if total_memory_mb > self.memory_limit_mb:
                    # Log detailed breakdown before terminating
                    logger.error(
                        f"Memory limit exceeded: {total_memory_mb:.1f} MB ({total_memory_mb/1024:.2f} GB) > "
                        f"{self.memory_limit_mb} MB ({self.memory_limit_mb/1024:.2f} GB). "
                        f"Terminating worker to prevent system OOM."
                    )
                    # Log top memory consumers
                    self._log_top_processes()
                    self._terminate_worker()
                    break

                # Sleep until next check
                self._stop_event.wait(self.check_interval)
            except Exception as exc:
                logger.error(f"Error in memory monitor: {exc}", exc_info=True)
                # Continue monitoring even on error
                self._stop_event.wait(self.check_interval)

    def add_subprocess_pid(self, pid: int):
        """Add a subprocess PID to track (e.g., batch_diarize.py)."""
        self._tracked_subprocess_pids.add(pid)
        logger.debug(f"Tracking subprocess PID: {pid}")

    def _get_total_memory_mb(self) -> float:
        """
        Get total memory usage (RSS) of current process and all related processes.
        
        Tracks:
        1. Main worker process
        2. All direct and indirect children (even across process groups)
        3. Tracked subprocess PIDs and their descendants
        4. Any ffmpeg processes that might be related
        """
        try:
            tracked_pids: Set[int] = {self.process.pid}
            total_memory = 0.0
            
            # Track main process
            try:
                main_memory = self.process.memory_info().rss / (1024 * 1024)  # MB
                total_memory += main_memory
                tracked_pids.add(self.process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            
            # Track all children of main process (recursive)
            main_children = self._get_all_children(self.process.pid)
            tracked_pids.update(main_children)
            
            # Track subprocess PIDs and their descendants (e.g., batch_diarize.py)
            for subproc_pid in list(self._tracked_subprocess_pids):
                try:
                    subproc = psutil.Process(subproc_pid)
                    # Verify it's still running
                    subproc.status()  # Will raise if process doesn't exist
                    tracked_pids.add(subproc_pid)
                    # Get all descendants of this subprocess
                    subproc_children = self._get_all_children(subproc_pid)
                    tracked_pids.update(subproc_children)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # Subprocess finished, remove from tracking
                    self._tracked_subprocess_pids.discard(subproc_pid)
                    continue
            
            # Also scan for ffmpeg processes that might be related
            # Only if we have tracked subprocesses (to avoid tracking unrelated ffmpeg)
            if self._tracked_subprocess_pids:
                current_user = None
                current_username = None
                if hasattr(os, "getuid"):
                    try:
                        current_user = os.getuid()
                    except Exception:
                        current_user = None
                if current_user is None:
                    try:
                        current_username = self.process.username()
                    except Exception:
                        current_username = None
                try:
                    for proc in psutil.process_iter([
                        'pid',
                        'name',
                        'cmdline',
                        'memory_info',
                        'uids',
                        'username',
                        'ppid',
                        'create_time',
                    ]):
                        try:
                            proc_info = proc.info
                            pid = proc_info['pid']
                            
                            # Skip if already tracked
                            if pid in tracked_pids:
                                continue
                            
                            # Only track ffmpeg processes owned by current user
                            if current_user is not None and proc_info.get('uids'):
                                if proc_info['uids'].real != current_user:
                                    continue
                            if current_user is None and current_username and proc_info.get('username'):
                                if proc_info['username'] != current_username:
                                    continue
                            
                            cmdline = proc_info.get('cmdline', [])
                            if cmdline and any('ffmpeg' in str(arg).lower() for arg in cmdline):
                                # Check if this ffmpeg might be a descendant of our tracked processes
                                # by checking if its parent or any ancestor is in our tracked set
                                ppid = proc_info.get('ppid')
                                if ppid and ppid in tracked_pids:
                                    tracked_pids.add(pid)
                                    logger.debug(f"Found ffmpeg process {pid} (parent {ppid} is tracked)")
                                else:
                                    # Check if it's a descendant by walking up the process tree
                                    ancestor_pid = ppid
                                    depth = 0
                                    while ancestor_pid and depth < 10:  # Limit depth to avoid infinite loops
                                        if ancestor_pid in tracked_pids:
                                            tracked_pids.add(pid)
                                            logger.debug(f"Found ffmpeg process {pid} (ancestor {ancestor_pid} is tracked)")
                                            break
                                        try:
                                            ancestor = psutil.Process(ancestor_pid)
                                            ancestor_pid = ancestor.ppid()
                                            depth += 1
                                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                                            break
                        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                            continue
                except Exception as exc:
                    logger.debug(f"Error scanning for ffmpeg processes: {exc}")
            
            # Calculate total memory for all tracked processes
            ffmpeg_memory = 0.0
            other_memory = 0.0
            current_time = time.time()
            
            for pid in tracked_pids:
                try:
                    proc = psutil.Process(pid)
                    mem_mb = proc.memory_info().rss / (1024 * 1024)  # MB
                    
                    # Check if it's ffmpeg
                    try:
                        cmdline = proc.cmdline()
                        is_ffmpeg = any('ffmpeg' in str(arg).lower() for arg in cmdline)
                        if is_ffmpeg:
                            ffmpeg_memory += mem_mb
                            # Log large ffmpeg processes at WARNING level, but throttle
                            if mem_mb > 1000:  # > 1GB
                                # Only warn once per PID, or if it grows significantly
                                if pid not in self._warned_ffmpeg_pids:
                                    logger.warning(
                                        f"Large ffmpeg process detected: PID {pid}, "
                                        f"{mem_mb:.1f} MB ({mem_mb/1024:.2f} GB)"
                                    )
                                    self._warned_ffmpeg_pids.add(pid)
                            else:
                                logger.debug(f"ffmpeg process {pid}: {mem_mb:.1f} MB")
                        else:
                            other_memory += mem_mb
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        other_memory += mem_mb
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # Process terminated, remove from warned set if it was there
                    self._warned_ffmpeg_pids.discard(pid)
                    continue
            
            total = total_memory + ffmpeg_memory + other_memory
            
            # Log summary at INFO level if memory is getting high, but throttle to every 30 seconds
            if total > self.memory_limit_mb * 0.7:  # Log if > 70% of limit
                if current_time - self._last_info_time > 30.0:  # Throttle to every 30 seconds
                    logger.info(
                        f"Memory usage: total={total:.1f} MB ({total/1024:.2f} GB) / "
                        f"limit={self.memory_limit_mb} MB ({self.memory_limit_mb/1024:.2f} GB), "
                        f"ffmpeg={ffmpeg_memory:.1f} MB, other={other_memory:.1f} MB"
                    )
                    self._last_info_time = current_time
            else:
                logger.debug(
                    f"Memory usage: total={total:.1f} MB, "
                    f"ffmpeg={ffmpeg_memory:.1f} MB, other={other_memory:.1f} MB"
                )
            
            return total
        except Exception as exc:
            logger.warning(f"Error getting memory usage: {exc}", exc_info=True)
            return 0.0

    def _get_all_children(self, pid: int) -> Set[int]:
        """Recursively collect all child PIDs."""
        children: Set[int] = set()
        try:
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                children.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return children

    def _log_top_processes(self):
        """Log top memory-consuming processes for debugging."""
        try:
            processes = []
            tracked_pids = {self.process.pid}
            tracked_pids.update(self._get_all_children(self.process.pid))
            for subproc_pid in self._tracked_subprocess_pids:
                tracked_pids.update(self._get_all_children(subproc_pid))
            
            for pid in tracked_pids:
                try:
                    proc = psutil.Process(pid)
                    mem_mb = proc.memory_info().rss / (1024 * 1024)
                    try:
                        cmdline = ' '.join(proc.cmdline()[:3])  # First 3 args
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        cmdline = f"PID {pid}"
                    processes.append((pid, mem_mb, cmdline))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # Sort by memory and log top 10
            processes.sort(key=lambda x: x[1], reverse=True)
            logger.error("Top memory-consuming processes:")
            for pid, mem_mb, cmdline in processes[:10]:
                logger.error(f"  PID {pid}: {mem_mb:.1f} MB - {cmdline}")
        except Exception as exc:
            logger.warning(f"Error logging top processes: {exc}")

    def _terminate_worker(self):
        """
        Terminate the worker process cleanly.
        This will cause the worker to stop processing jobs and exit.
        """
        logger.critical("Terminating worker due to memory limit exceeded")
        try:
            # Send SIGTERM to allow cleanup
            os.kill(os.getpid(), signal.SIGTERM)
            # If that doesn't work quickly, SIGKILL will be sent by signal handler
        except Exception as exc:
            logger.error(f"Failed to terminate worker: {exc}")
            # Fallback: force exit
            os._exit(1)

