import json
import logging
import os
import shutil
import subprocess
import csv
import sys
import signal
import time
import yaml
from pathlib import Path
from typing import Dict, List, Optional

from dal import FilesystemCache, JobRepository, TranscriptRepository, VideoRepository
from models import Job, JobStatus
from configuration import load_config
from .reference_builder import ReferenceBuilder
from .memory_monitor import MemoryMonitor

logger = logging.getLogger(__name__)


class DiarizationService:
    """
    Bridge diarization jobs through the legacy workspace.sh diarize command.
    """

    def __init__(
        self,
        video_repo: VideoRepository,
        job_repo: JobRepository,
        transcript_repo: TranscriptRepository,
        fs_cache: FilesystemCache,
    ):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.transcript_repo = transcript_repo
        self.fs_cache = fs_cache

        # Load diarization defaults from config
        self.config = load_config()

        self._memory_monitor: Optional[MemoryMonitor] = None
        self._load_memory_monitor_config()

    def _wait_with_progress(self, proc: subprocess.Popen, description: str, log_interval: int = 60) -> int:
        """
        Wait for subprocess to complete with periodic progress logging.

        Args:
            proc: Subprocess to wait for
            description: Description of what's running (for logs)
            log_interval: Seconds between progress log messages

        Returns:
            Process return code
        """
        start_time = time.time()
        last_log_time = start_time

        while proc.poll() is None:
            time.sleep(1)  # Check every second
            elapsed = time.time() - start_time

            # Log progress every log_interval seconds
            if time.time() - last_log_time >= log_interval:
                elapsed_min = int(elapsed / 60)
                elapsed_sec = int(elapsed % 60)
                logger.info(f"{description} - still running (elapsed: {elapsed_min}m {elapsed_sec}s)")
                last_log_time = time.time()

        return_code = proc.returncode
        total_elapsed = time.time() - start_time
        elapsed_min = int(total_elapsed / 60)
        elapsed_sec = int(total_elapsed % 60)
        logger.info(f"{description} - completed in {elapsed_min}m {elapsed_sec}s (exit code: {return_code})")

        return return_code

    def enqueue_diarization_job(
        self,
        ytid: str,
        transcript_kind: str,
        diarization_model: Optional[str] = None,
        priority: int = 50,
        reference_name: Optional[str] = None,
        match_threshold: Optional[float] = None,
        match_margin: Optional[float] = None,
        match_force_best: Optional[bool] = None,
        words_path: Optional[str] = None,
        device: Optional[str] = None,
        chunk_seconds: Optional[float] = None,
        overlap_seconds: Optional[float] = None,
        similarity_threshold: Optional[float] = None,
        gap_threshold: Optional[float] = None,
        output_dir: Optional[str] = None,
        build_reference: bool = False,
        force_enqueue: bool = False,
    ) -> Job:
        """
        Enqueues a single diarization job.

        All parameters (except ytid, transcript_kind, and priority) default to values
        from config.yaml (diarization section) if not explicitly provided.

        If transcript_kind is "best", automatically selects the highest quality
        transcript available for this ytid.

        Args:
            force_enqueue: If True, skip validation checks (for pipeline orchestration).
                          Useful when enqueueing jobs before dependencies are complete.
        """
        # Apply config defaults for any None parameters
        diarization_model = diarization_model or self.config.diarization.model
        device = device or self.config.diarization.device
        chunk_seconds = chunk_seconds or self.config.diarization.chunk_seconds
        overlap_seconds = overlap_seconds or self.config.diarization.overlap_seconds
        similarity_threshold = similarity_threshold or self.config.diarization.similarity_threshold
        gap_threshold = gap_threshold or self.config.diarization.gap_threshold
        match_threshold = match_threshold or self.config.diarization.match_threshold
        match_margin = match_margin or self.config.diarization.match_margin
        match_force_best = match_force_best if match_force_best is not None else self.config.diarization.match_force_best
        if not force_enqueue:
            video = self.video_repo.get(ytid)
            if not video:
                raise ValueError(f"Video with ytid '{ytid}' not found.")

            # Handle "best" transcript selection
            if transcript_kind.lower() == "best":
                transcript = self.transcript_repo.get_best_available(ytid)
                if not transcript:
                    raise ValueError(f"No transcripts available for video '{ytid}'.")
                logger.info(f"Selected best available transcript for {ytid}: {transcript.kind}")
                transcript_kind = transcript.kind
            else:
                transcript = self.transcript_repo.get(ytid, transcript_kind)
                if not transcript or not transcript.path:
                    raise ValueError(f"Transcript of kind '{transcript_kind}' not found for video '{ytid}'.")

            media_asset = self._resolve_media_asset_path(ytid)
            words_rel = self._normalize_relative(words_path or transcript.path, allow_dir=False)
            ref_name = reference_name or ytid
            ref_rel = self._normalize_relative(f"data/references/{ref_name}", allow_dir=True)
            output_rel = self._normalize_output_dir(output_dir, ytid)

            reference_dir = self._ensure_reference_at_enqueue(
                ytid=ytid,
                media_rel=media_asset,
                words_rel=words_rel,
                reference_rel=ref_rel,
                build_reference=build_reference,
            )
        else:
            # For forced enqueue (pipeline mode), skip reference setup and use defaults
            media_asset = f"data/media/{ytid}/{ytid}.*"  # Wildcard - will be resolved at runtime
            words_rel = self._normalize_relative(words_path or f"data/transcripts/{ytid}/words_{transcript_kind}.jsonl", allow_dir=False)
            ref_rel = self._normalize_relative(f"data/references/{reference_name or ytid}", allow_dir=True)
            output_rel = self._normalize_output_dir(output_dir, ytid)
            reference_dir = None  # Will be created during processing if needed

        config = {
            "ytid": ytid,
            "transcript_kind": transcript_kind,
            "diarization_model": diarization_model,
            "media_asset_path": media_asset,
            "words_path": words_rel,
            "reference_dir": reference_dir,
            "device": device,
            "chunk_seconds": float(chunk_seconds),
            "overlap_seconds": float(overlap_seconds),
            "similarity_threshold": float(similarity_threshold),
            "gap_threshold": float(gap_threshold),
            "output_dir": output_rel,
            "build_reference": False,
            "match_threshold": float(match_threshold),
            "match_margin": float(match_margin),
            "match_force_best": bool(match_force_best),
        }

        job = Job(
            job_type="diarization",
            ytid=ytid,
            media_path=media_asset,
            config=config,
            priority=priority,
            status=JobStatus.PENDING,
        )
        return self.job_repo.create(job)

    def build_shared_reference(
        self,
        ytids: list[str],
        transcript_kind: str,
        reference_name: str,
        clips_count: int = 50,
    ) -> str:
        """
        Build (or reuse) a shared reference directory for a batch of ytids.
        Clips are sampled from distinct videos in the provided list (one clip per video).
        """
        reference_rel = f"data/references/{reference_name}"
        dest = self.fs_cache.get_central_path(reference_rel)
        if (dest / "reference.json").exists():
            return reference_rel

        workspace_root = self._workspace_root()
        builder = ReferenceBuilder(self.fs_cache, workspace_root)

        import random

        shuffled = list(ytids)
        random.shuffle(shuffled)

        sources: list[tuple[str, Path, Path]] = []
        for ytid in shuffled:
            video = self.video_repo.get(ytid)
            if not video:
                continue
            try:
                media_rel = self._resolve_media_asset_path(ytid)
            except Exception:
                continue

            if transcript_kind.lower() == "best":
                transcript = self.transcript_repo.get_best_available(ytid)
            else:
                transcript = self.transcript_repo.get(ytid, transcript_kind)
            if not transcript or not transcript.path:
                continue
            try:
                words_rel = self._normalize_relative(transcript.path, allow_dir=False)
            except Exception:
                continue

            try:
                media_local = self.fs_cache.pull_to_cache(media_rel)
                words_local = self.fs_cache.pull_to_cache(words_rel)
            except Exception:
                continue

            sources.append((ytid, media_local, words_local))
            if len(sources) >= clips_count:
                break

        if not sources:
            raise ValueError("No usable media+words sources found for shared reference.")

        builder.build_shared(
            reference_rel=reference_rel,
            sources=sources,
            clips_per_video=1,
            max_clips=clips_count,
        )
        return reference_rel

    def process_job(self, job: Job) -> None:
        """
        Processes a single diarization job claimed by a worker.
        """
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid.")
            return

        # Start memory monitoring if enabled
        monitor_started = False
        if self._memory_monitor:
            try:
                self._memory_monitor.start()
                monitor_started = True
                logger.info("Memory monitoring started for diarization job %s", job.job_id)
            except Exception as exc:
                logger.warning("Failed to start memory monitor: %s", exc)

        try:
            workspace_root = self._workspace_root()
            media_rel = job.config.get("media_asset_path") or job.media_path
            words_rel = job.config.get("words_path")
            reference_dir = job.config.get("reference_dir")
            output_rel = self._normalize_output_dir(job.config.get("output_dir"), job.ytid)

            if not media_rel or not words_rel:
                raise ValueError("Job missing media, words, or reference configuration.")

            audio_path = self._stage_media(media_rel, workspace_root)
            words_path = self._stage_words(words_rel, workspace_root, job.job_id)

            staged_reference_dir = self._stage_reference(reference_dir, workspace_root)

            output_dir = workspace_root / output_rel
            output_dir.mkdir(parents=True, exist_ok=True)

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting diarization.")
            # Memory monitor is active during preprocessing and diarization
            self._run_pyannote_diarize(job, workspace_root, audio_path, words_path, output_dir, staged_reference_dir)
            job_result = self._persist_outputs(job, output_rel, output_dir)
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Successfully diarized %s", job.ytid)
        except Exception as exc:
            error_msg = f"Diarization failed for job {job.job_id} ({job.ytid}): {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)
        finally:
            # Always stop memory monitoring
            if monitor_started and self._memory_monitor:
                try:
                    self._memory_monitor.stop()
                    logger.info("Memory monitoring stopped for diarization job %s", job.job_id)
                except Exception as exc:
                    logger.warning("Error stopping memory monitor: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _workspace_root(self) -> Path:
        if os.environ.get("VIDOPS_PROJECT_ROOT"):
            return Path(os.environ["VIDOPS_PROJECT_ROOT"]).resolve()
        return Path(__file__).resolve().parents[1]

    def _normalize_relative(self, path_str: str, allow_dir: bool) -> str:
        if not path_str:
            raise ValueError("Path configuration is missing.")
        if ".." in Path(path_str).parts:
            raise ValueError(f"Unsafe relative path: {path_str}")

        path = Path(path_str)
        if path.is_absolute():
            # First resolve the path with prefix (if configured)
            resolved_path = self.fs_cache._resolve_path_with_prefix(path_str)

            # Now try to make it relative to central storage root
            try:
                rel = resolved_path.relative_to(self.fs_cache.central_storage_root)
            except ValueError:
                raise ValueError(
                    f"Path must live under central storage ({self.fs_cache.central_storage_root}): {resolved_path}"
                ) from None
            if not allow_dir and rel.name == "":
                raise ValueError(f"Expected file path, got directory: {resolved_path}")
            return str(rel)
        return path_str

    def _normalize_output_dir(self, output_dir: Optional[str], ytid: str) -> str:
        base = output_dir or f"results/diarization/{ytid}"
        return self._normalize_relative(base, allow_dir=True)

    def _ensure_reference_at_enqueue(
        self,
        ytid: str,
        media_rel: str,
        words_rel: str,
        reference_rel: str,
        build_reference: bool,
    ) -> str:
        ref_central = self.fs_cache.get_central_path(reference_rel)
        if ref_central.exists():
            return reference_rel

        # If interactive and allowed, prompt once
        if (
            not build_reference
            and sys.stdin.isatty()
            and not os.environ.get("BATCH_DIARIZE_SKIP_PROMPT")
        ):
            resp = input(
                f"[?] Reference not found at {reference_rel}. Build it now from media/words? [y/N]: "
            ).strip().lower()
            build_reference = resp in {"y", "yes"}

        if not build_reference:
            raise FileNotFoundError(f"Reference not found and build_reference is false: {reference_rel}")

        workspace_root = self._workspace_root()
        ref_central.parent.mkdir(parents=True, exist_ok=True)
        media_local = self.fs_cache.pull_to_cache(media_rel)
        words_local = self.fs_cache.pull_to_cache(words_rel)
        builder = ReferenceBuilder(self.fs_cache, workspace_root)
        # Force non-interactive reference build in worker/CLI paths
        prev_skip = os.environ.get("BATCH_DIARIZE_SKIP_PROMPT")
        os.environ["BATCH_DIARIZE_SKIP_PROMPT"] = "1"
        built_dir = builder.build(ytid, media_local, words_local, reference_rel)
        if prev_skip is not None:
            os.environ["BATCH_DIARIZE_SKIP_PROMPT"] = prev_skip
        else:
            os.environ.pop("BATCH_DIARIZE_SKIP_PROMPT", None)
        logger.info("Built reference for %s at %s", ytid, built_dir)

        # Copy built reference into central storage under reference_rel
        dest_dir = ref_central
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(built_dir, dest_dir)
        return reference_rel

    def _resolve_media_asset_path(self, ytid: str) -> str:
        asset = self.video_repo.get_primary_asset(ytid, "media")
        if not asset or not asset.path:
            raise ValueError(f"No media asset registered for {ytid}. Download first.")
        return asset.path

    def _stage_media(self, media_rel: str, workspace_root: Path) -> Path:
        cached = self.fs_cache.pull_to_cache(media_rel)
        pull_dir = workspace_root / "pull"
        pull_dir.mkdir(parents=True, exist_ok=True)
        target = pull_dir / Path(media_rel).name
        if not target.exists():
            shutil.copy2(cached, target)
        return target

    def _stage_words(self, words_rel: str, workspace_root: Path, job_id: str) -> Path:
        cached = self.fs_cache.pull_to_cache(words_rel)
        dest = workspace_root / "generated"
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / Path(words_rel).name
        if not target.exists():
            shutil.copy2(cached, target)
        return target

    def _stage_reference(self, reference_rel: str, workspace_root: Path) -> Path:
        src_dir = self.fs_cache.get_central_path(reference_rel)
        if not src_dir.exists():
            raise FileNotFoundError(f"Reference directory not found: {src_dir}")
        dest_dir = workspace_root / "data" / "references" / src_dir.name
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        for item in sorted(src_dir.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(self.fs_cache.central_storage_root)
            cached = self.fs_cache.pull_to_cache(str(rel))
            relative = item.relative_to(src_dir)
            if relative.parts and relative.parts[0] == "clips":
                target = dest_dir / item.name
            else:
                target = dest_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, target)
        ref_json = dest_dir / "reference.json"
        if not ref_json.exists():
            raise FileNotFoundError(f"reference.json missing in {dest_dir}")
        return dest_dir

    def _run_legacy_diarize(
        self,
        job: Job,
        workspace_root: Path,
        audio_path: Path,
        words_path: Path,
        output_dir: Path,
    ) -> None:
        if os.environ.get("VIDOPS_FAKE_DIARIZATION", "").lower() in {"1", "true"}:
            self._write_fake_outputs(job, output_dir, audio_path)
            return

        workspace_sh = Path(__file__).resolve().parents[1] / "workspace.sh"
        cmd = [
            "bash",
            str(workspace_sh),
            "diarize",
            "--ytid",
            job.ytid,
            "--audio",
            str(audio_path),
            "--words",
            str(words_path),
            "--project-root",
            str(workspace_root),
            "--device",
            str(job.config.get("device", "auto")),
            "--chunk-seconds",
            str(job.config.get("chunk_seconds", 6.0)),
            "--overlap-seconds",
            str(job.config.get("overlap_seconds", 1.0)),
            "--similarity-threshold",
            str(job.config.get("similarity_threshold", 0.6)),
            "--gap-threshold",
            str(job.config.get("gap_threshold", 0.15)),
        ]
        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(workspace_root)
        logger.info("Running legacy diarize: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            cwd=workspace_root,
            env=env,
            text=True,
            capture_output=False,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Legacy diarization failed with exit {result.returncode}")

    def _get_audio_duration(self, audio_path: Path) -> float:
        """Get audio duration in seconds using ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except Exception as exc:
            logger.warning(f"Failed to get audio duration: {exc}")
            return 0.0

    def _run_pyannote_diarize(
        self,
        job: Job,
        workspace_root: Path,
        audio_path: Path,
        words_path: Path,
        output_dir: Path,
        reference_dir: Path,
    ) -> None:
        if os.environ.get("VIDOPS_FAKE_DIARIZATION", "").lower() in {"1", "true"}:
            self._write_fake_outputs(job, output_dir, audio_path)
            return

        # Check audio duration and use chunking for files > 1 hour
        chunk_threshold = float(os.environ.get("DIAR_CHUNK_THRESHOLD", "3600"))  # Default: 1 hour
        audio_duration = self._get_audio_duration(audio_path)

        if audio_duration > chunk_threshold:
            logger.info(
                f"Audio duration {audio_duration:.1f}s (>{chunk_threshold:.1f}s), using chunked processing"
            )
            self._run_chunked_diarization(
                job=job,
                workspace_root=workspace_root,
                audio_path=audio_path,
                words_path=words_path,
                output_dir=output_dir,
                reference_dir=reference_dir,
                audio_duration=audio_duration,
            )
            return

        # Standard processing for files <= 1 hour
        self._run_single_diarization(
            job=job,
            workspace_root=workspace_root,
            audio_path=audio_path,
            words_path=words_path,
            output_dir=output_dir,
            reference_dir=reference_dir,
        )

    def _run_single_diarization(
        self,
        job: Job,
        workspace_root: Path,
        audio_path: Path,
        words_path: Path,
        output_dir: Path,
        reference_dir: Path,
    ) -> None:
        """Run diarization on a single file (no chunking)."""
        # TOOL_ROOT should point to the code tree that has scripts/diarization/batch_diarize.py.
        # Fall back to PROJECT_ROOT/workspace_root if env is not set to avoid hardcoded paths.
        tool_root = Path(
            os.environ.get("TOOL_ROOT")
            or os.environ.get("VIDOPS_PROJECT_ROOT")
            or os.environ.get("PROJECT_ROOT")
            or workspace_root
        ).resolve()
        batch_script = tool_root / "scripts" / "diarization" / "batch_diarize.py"
        config_path = tool_root / "config" / "diarization.yaml"
        if not batch_script.exists():
            raise FileNotFoundError(f"batch_diarize.py not found at {batch_script} (set TOOL_ROOT appropriately)")

        # Ensure ytids file for batch_diarize
        tmp_dir = workspace_root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ytids_file = tmp_dir / f"{job.job_id}_ytids.txt"
        ytids_file.write_text(f"{job.ytid}\n", encoding="utf-8")

        results_root = output_dir.parent  # batch_diarize writes under base/<ytid>
        # Prefer configured python for pyannote pipeline
        py_bin = Path(os.environ.get("DIAR_PYTHON_BIN", "/home/billie/tools/vidops/.venv/bin/python"))
        if not py_bin.exists():
            alt_py = workspace_root / ".venv" / "bin" / "python"
            py_bin = alt_py if alt_py.exists() else Path(sys.executable)

        # Allow per-process device override for multi-GPU setups.
        # Prefer env-provided device if present (e.g., VIDOPS_DIAR_DEVICE=cuda, cuda:0, cpu).
        device_override = os.environ.get("VIDOPS_DIAR_DEVICE") or os.environ.get("DIAR_DEVICE")
        device_arg = device_override or str(job.config.get("device", "auto"))

        cmd = [
            str(py_bin),
            str(batch_script),
            str(ytids_file),
            str(results_root),
            "--config",
            str(config_path),
            "--device",
            device_arg,
        ]

        ref_name = reference_dir.name
        if ref_name:
            cmd.extend(["--reference", ref_name])
            cmd.extend(["--match-threshold", str(job.config.get("match_threshold", 0.75))])
            cmd.extend(["--match-margin", str(job.config.get("match_margin", 0.01))])
            if job.config.get("match_force_best", True) is False:
                cmd.append("--no-match-force-best")

        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(workspace_root)
        env["TOOL_ROOT"] = str(tool_root)
        env["BATCH_DIARIZE_SKIP_PROMPT"] = "1"
        # Default to running preprocessing so we canonicalize audio before diarization.
        # Skipping preprocessing forces torchaudio to decode multi-hour MP4/OPUS directly,
        # which can balloon resident memory. Users can still override to skip if needed.
        env.setdefault("BATCH_DIARIZE_SKIP_PREPROCESS", "0")

        logger.info("Running pyannote diarize: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            cwd=workspace_root,
            env=env,
            text=True,
            start_new_session=True,  # allow clean group termination on interrupts
        )

        # Register subprocess PID with memory monitor so it tracks all descendants
        if self._memory_monitor and self._memory_monitor.monitoring:
            self._memory_monitor.add_subprocess_pid(proc.pid)
            logger.debug(f"Registered batch_diarize subprocess PID {proc.pid} with memory monitor")

        try:
            return_code = self._wait_with_progress(proc, f"Diarization for {job.ytid}", log_interval=60)
        except BaseException as exc:  # catch KeyboardInterrupt/SystemExit for cleanup
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
            raise exc
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        if return_code != 0:
            raise RuntimeError(f"pyannote diarization failed with exit {return_code}")

    def _run_chunked_diarization(
        self,
        job: Job,
        workspace_root: Path,
        audio_path: Path,
        words_path: Path,
        output_dir: Path,
        reference_dir: Path,
        audio_duration: float,
    ) -> None:
        """
        Run diarization on audio file using chunking for memory efficiency.
        Splits file into ~1 hour chunks, processes each separately, then combines results.
        """
        tool_root = Path(
            os.environ.get("TOOL_ROOT")
            or os.environ.get("VIDOPS_PROJECT_ROOT")
            or os.environ.get("PROJECT_ROOT")
            or workspace_root
        ).resolve()

        chunk_script = tool_root / "scripts" / "diarization" / "chunk_audio.py"
        combine_script = tool_root / "scripts" / "diarization" / "combine_chunks.py"
        batch_script = tool_root / "scripts" / "diarization" / "batch_diarize.py"
        config_path = tool_root / "config" / "diarization.yaml"

        for script in [chunk_script, combine_script, batch_script]:
            if not script.exists():
                raise FileNotFoundError(f"Script not found: {script}")

        # Create chunks directory
        chunks_dir = workspace_root / "tmp" / f"{job.job_id}_chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        chunk_results_dir = workspace_root / "tmp" / f"{job.job_id}_chunk_results"
        chunk_results_dir.mkdir(parents=True, exist_ok=True)

        py_bin = Path(os.environ.get("DIAR_PYTHON_BIN", "/home/billie/tools/vidops/.venv/bin/python"))
        if not py_bin.exists():
            alt_py = workspace_root / ".venv" / "bin" / "python"
            py_bin = alt_py if alt_py.exists() else Path(sys.executable)

        chunk_duration = float(os.environ.get("DIAR_CHUNK_DURATION", "3600"))  # 1 hour
        chunk_overlap = float(os.environ.get("DIAR_CHUNK_OVERLAP", "30"))  # 30 seconds

        logger.info(
            f"Chunking audio: {audio_duration:.1f}s total, {chunk_duration:.1f}s per chunk, {chunk_overlap:.1f}s overlap"
        )

        # Step 1: Chunk the audio
        chunk_cmd = [
            str(py_bin),
            str(chunk_script),
            str(audio_path),
            str(chunks_dir),
            "--chunk-duration", str(chunk_duration),
            "--overlap", str(chunk_overlap),
        ]

        logger.info(f"Running chunking: {' '.join(chunk_cmd)}")
        result = subprocess.run(chunk_cmd, cwd=workspace_root, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Chunking failed: {result.stderr}")
            raise RuntimeError(f"Audio chunking failed with exit {result.returncode}")

        # Load chunk metadata
        chunks_metadata_path = chunks_dir / "chunks_metadata.json"
        if not chunks_metadata_path.exists():
            raise FileNotFoundError(f"Chunk metadata not found: {chunks_metadata_path}")

        with chunks_metadata_path.open("r", encoding="utf-8") as f:
            chunks_metadata = json.load(f)

        num_chunks = chunks_metadata["num_chunks"]
        logger.info(f"Created {num_chunks} chunk(s)")

        # Step 2: Process each chunk with batch_diarize
        device_override = os.environ.get("VIDOPS_DIAR_DEVICE") or os.environ.get("DIAR_DEVICE")
        device_arg = device_override or str(job.config.get("device", "auto"))

        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(workspace_root)
        env["TOOL_ROOT"] = str(tool_root)
        env["BATCH_DIARIZE_SKIP_PROMPT"] = "1"
        env["BATCH_DIARIZE_SKIP_PREPROCESS"] = "0"

        for chunk_idx, chunk in enumerate(chunks_metadata["chunks"]):
            # Skip original file marker (no chunking case)
            if chunk.get("is_original", False):
                logger.info("Single chunk (original file), processing directly")
                # Just run normal processing on the original file
                self._run_single_diarization(
                    job=job,
                    workspace_root=workspace_root,
                    audio_path=audio_path,
                    words_path=words_path,
                    output_dir=output_dir,
                    reference_dir=reference_dir,
                )
                return

            chunk_path = Path(chunk["chunk_path"])
            chunk_ytid = f"{job.ytid}_chunk_{chunk_idx:03d}"

            logger.info(f"Processing chunk {chunk_idx+1}/{num_chunks}: {chunk_ytid}")

            # Create ytids file for this chunk
            tmp_dir = workspace_root / "tmp"
            chunk_ytids_file = tmp_dir / f"{job.job_id}_chunk_{chunk_idx:03d}_ytids.txt"
            chunk_ytids_file.write_text(f"{chunk_ytid}\n", encoding="utf-8")

            # Copy chunk to pull directory so batch_diarize can find it
            # IMPORTANT: Name must match what batch_diarize expects: {ytid}.wav
            pull_dir = workspace_root / "pull"
            pull_dir.mkdir(parents=True, exist_ok=True)
            chunk_in_pull = pull_dir / f"{chunk_ytid}.wav"
            if not chunk_in_pull.exists():
                shutil.copy2(chunk_path, chunk_in_pull)

            # Run batch_diarize on this chunk
            chunk_cmd = [
                str(py_bin),
                str(batch_script),
                str(chunk_ytids_file),
                str(chunk_results_dir),
                "--config", str(config_path),
                "--device", device_arg,
            ]

            ref_name = reference_dir.name
            if ref_name:
                chunk_cmd.extend(["--reference", ref_name])
                chunk_cmd.extend(["--match-threshold", str(job.config.get("match_threshold", 0.75))])
                chunk_cmd.extend(["--match-margin", str(job.config.get("match_margin", 0.01))])
                if job.config.get("match_force_best", True) is False:
                    chunk_cmd.append("--no-match-force-best")

            logger.info(f"Running diarization on chunk {chunk_idx}: {' '.join(chunk_cmd)}")
            proc = subprocess.Popen(
                chunk_cmd,
                cwd=workspace_root,
                env=env,
                text=True,
                start_new_session=True,
            )

            # Register with memory monitor
            if self._memory_monitor and self._memory_monitor.monitoring:
                self._memory_monitor.add_subprocess_pid(proc.pid)

            try:
                return_code = self._wait_with_progress(
                    proc,
                    f"Chunk {chunk_idx+1}/{num_chunks} ({chunk_ytid})",
                    log_interval=60
                )
            except BaseException as exc:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        pass
                raise exc

            if return_code != 0:
                raise RuntimeError(f"Chunk {chunk_idx} diarization failed with exit {return_code}")

            # Clean up chunk from pull directory
            if chunk_in_pull.exists():
                chunk_in_pull.unlink()

            logger.info(f"Chunk {chunk_idx+1}/{num_chunks} completed")

        # Step 3: Combine chunk results
        logger.info("Combining chunk results")
        combine_cmd = [
            str(py_bin),
            str(combine_script),
            str(chunks_dir),
            str(chunk_results_dir),
            str(output_dir),
            "--",  # ensure ytids that start with '-' are not parsed as flags
            job.ytid,
        ]

        logger.info(f"Running combine: {' '.join(combine_cmd)}")
        result = subprocess.run(combine_cmd, cwd=workspace_root, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Combining failed: {result.stderr}")
            raise RuntimeError(f"Chunk combination failed with exit {result.returncode}")

        logger.info(f"Chunked diarization completed for {job.ytid}")

        # Step 4: Map words to combined timestamps so downstream consumers always have speaker_words.tsv
        combined_timestamps = output_dir / "diarized_timestamps.tsv"
        speaker_words_path = output_dir / "speaker_words.tsv"
        if combined_timestamps.exists():
            if words_path and Path(words_path).exists():
                try:
                    mapping_stats = self._map_words_to_speakers(
                        timestamps_file=combined_timestamps,
                        words_file=Path(words_path),
                        output_file=speaker_words_path,
                        gap_tolerance=job.config.get("gap_threshold", 0.15),
                    )
                    meta_path = output_dir / "diarization.json"
                    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
                    meta["word_mapping"] = mapping_stats
                    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                except Exception as exc:
                    logger.warning("Word mapping failed on combined output for %s: %s", job.ytid, exc)
                    self._write_placeholder_speaker_words(output_dir)
            else:
                logger.warning("Words file not found for %s; writing placeholder speaker_words.tsv", job.ytid)
                self._write_placeholder_speaker_words(output_dir)
        else:
            logger.warning("Combined timestamps not found for %s; writing placeholder speaker_words.tsv", job.ytid)
            self._write_placeholder_speaker_words(output_dir)

        # Clean up chunk files and intermediate results
        try:
            shutil.rmtree(chunks_dir)
            shutil.rmtree(chunk_results_dir)
            logger.debug(f"Cleaned up chunk directories")
        except Exception as exc:
            logger.warning(f"Failed to clean up chunk directories: {exc}")

    def _write_fake_outputs(self, job: Job, output_dir: Path, audio_path: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamps = output_dir / "diarized_timestamps.tsv"
        speaker_words = output_dir / "speaker_words.tsv"
        meta_path = output_dir / "diarization.json"

        timestamps.write_text(
            "ytid\tspeaker_name\tstart_sec\tend_sec\tduration_sec\tsource_audio\n"
            f"{job.ytid}\tSpeaker 1\t0.000\t1.000\t1.000\t{audio_path}\n",
            encoding="utf-8",
        )
        speaker_words.write_text(
            "start\tend\tword\tseg\tconfidence\tretried\tspeaker\n"
            "0.000\t0.500\thello\tseg1\t0.0\t\tSpeaker 1\n",
            encoding="utf-8",
        )
        meta = {
            "ytid": job.ytid,
            "source_audio": str(audio_path),
            "segments": 1,
            "words": 1,
            "chunk_seconds": job.config.get("chunk_seconds", 6.0),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _write_placeholder_speaker_words(self, output_dir: Path) -> None:
        """Ensure speaker_words.tsv exists, even when word mapping is unavailable."""
        output_dir.mkdir(parents=True, exist_ok=True)
        speaker_words = output_dir / "speaker_words.tsv"
        if not speaker_words.exists():
            speaker_words.write_text(
                "start\tend\tword\tseg\tconfidence\tretried\tspeaker\n",
                encoding="utf-8",
            )

    def _map_words_to_speakers(
        self,
        timestamps_file: Path,
        words_file: Path,
        output_file: Path,
        gap_tolerance: float = 0.15,
    ) -> Dict:
        """
        Lightweight word-to-speaker mapping for combined chunk outputs.

        Matches transcript words to diarization turns by maximum temporal overlap with a small tolerance.
        """
        turns: List[dict] = []
        with timestamps_file.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                try:
                    start_val = row.get("start") or row.get("start_sec")
                    end_val = row.get("end") or row.get("end_sec")
                    if start_val is None or end_val is None:
                        continue
                    start = float(start_val)
                    end = float(end_val)
                    speaker = row.get("speaker") or row.get("speaker_name") or "SPEAKER_00"
                except Exception:
                    continue
                turns.append({"start": start, "end": end, "speaker": speaker})

        turns.sort(key=lambda t: (t["start"], t["end"]))
        if not turns:
            raise ValueError(f"No diarization turns found in {timestamps_file}")

        # Load words
        with words_file.open("r", encoding="utf-8") as f:
            words_reader = csv.DictReader(f, delimiter="\t")
            word_rows = list(words_reader)
            fieldnames = words_reader.fieldnames or []

        if not word_rows:
            raise ValueError(f"No words found in {words_file}")

        # Ensure speaker column is present
        if "speaker" not in fieldnames:
            fieldnames = fieldnames + ["speaker"]

        assignments = []
        for word in word_rows:
            try:
                w_start = float(word.get("start", 0))
                w_end = float(word.get("end", 0))
            except Exception:
                assignments.append("UNKNOWN")
                continue

            best_speaker = "UNKNOWN"
            best_overlap = float("-inf")
            for turn in turns:
                if turn["end"] + gap_tolerance < w_start:
                    continue
                if turn["start"] - gap_tolerance > w_end:
                    break
                overlap = min(w_end, turn["end"]) - max(w_start, turn["start"])
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = turn["speaker"]

            assignments.append(best_speaker)

        # Write output with assigned speakers
        with output_file.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            for word, speaker in zip(word_rows, assignments):
                word["speaker"] = speaker
                writer.writerow(word)

        unknown_count = sum(1 for s in assignments if s == "UNKNOWN")
        return {
            "total_words": len(assignments),
            "assigned": len(assignments) - unknown_count,
            "unknown": unknown_count,
            "unknown_pct": 100 * unknown_count / len(assignments) if assignments else 0,
        }

    def _persist_outputs(self, job: Job, output_rel: str, output_dir: Path) -> dict:
        rel_base = Path(output_rel.strip("/"))
        ts_candidates = [
            output_dir / "diarized_timestamps_clean.tsv",
            output_dir / "diarized_timestamps_matched.tsv",
            output_dir / "diarized_timestamps.tsv",
        ]
        timestamps = next((p for p in ts_candidates if p.exists()), None)
        if not timestamps:
            raise FileNotFoundError("Missing diarization timestamps output.")

        speaker_words = output_dir / "speaker_words.tsv"
        meta_path = output_dir / "diarization.json"
        for path in (speaker_words, meta_path):
            if not path.exists():
                raise FileNotFoundError(f"Missing diarization output: {path}")

        ts_rel = str(rel_base / timestamps.name)
        sw_rel = str(rel_base / speaker_words.name)
        meta_rel = str(rel_base / meta_path.name)

        self.fs_cache.persist_local_artifact(timestamps, ts_rel, self.video_repo, job.ytid, "diarization")
        self.fs_cache.persist_local_artifact(speaker_words, sw_rel, self.video_repo, job.ytid, "diarization")
        self.fs_cache.persist_local_artifact(meta_path, meta_rel, self.video_repo, job.ytid, "diarization")

        segments = max(0, len(timestamps.read_text(encoding="utf-8").splitlines()) - 1)
        word_rows = max(0, len(speaker_words.read_text(encoding="utf-8").splitlines()) - 1)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        db_spans = self._write_spans_to_db(job.ytid, timestamps)

        return {
            "timestamps_path": ts_rel,
            "speaker_words_path": sw_rel,
            "metadata_path": meta_rel,
            "segments": segments,
            "word_rows": word_rows,
            "device": job.config.get("device", "auto"),
            "diarization_model": job.config.get("diarization_model"),
            "meta": meta,
            "db_spans_inserted": db_spans,
        }

    def _load_memory_monitor_config(self):
        """Load memory monitor configuration from diarization.yaml."""
        try:
            workspace_root = self._workspace_root()
            config_path = workspace_root / "config" / "diarization.yaml"
            
            if not config_path.exists():
                # Try TOOL_ROOT if PROJECT_ROOT doesn't have it
                tool_root = Path(os.environ.get("TOOL_ROOT", workspace_root))
                config_path = tool_root / "config" / "diarization.yaml"
            
            if not config_path.exists():
                logger.debug("Diarization config not found, memory monitoring disabled")
                return
            
            with config_path.open() as f:
                config = yaml.safe_load(f) or {}
            
            diar_config = config.get("diarization", {})
            monitor_config = diar_config.get("memory_monitor", {})
            
            if not monitor_config.get("enabled", True):
                logger.debug("Memory monitoring disabled in config")
                return
            
            memory_limit_mb = monitor_config.get("memory_limit_mb", 8192)  # Default 8GB
            check_interval = monitor_config.get("check_interval", 1.0)
            
            try:
                self._memory_monitor = MemoryMonitor(
                    memory_limit_mb=memory_limit_mb,
                    check_interval=check_interval
                )
                logger.info(
                    "Memory monitor configured: limit=%d MB, check_interval=%.1f s",
                    memory_limit_mb,
                    check_interval
                )
            except ImportError as exc:
                logger.warning(
                    "Memory monitoring requested but psutil not available: %s. "
                    "Install with: pip install psutil>=5.9.0",
                    exc
                )
                self._memory_monitor = None
        except Exception as exc:
            logger.warning("Failed to load memory monitor config: %s", exc)
            self._memory_monitor = None

    def _write_spans_to_db(self, ytid: str, timestamps_path: Path, replace_existing: bool = True) -> int:
        from psycopg2.extras import execute_values  # type: ignore
        from db import get_connection

        rows: list[tuple[str, str, float, float, str]] = []
        with timestamps_path.open("r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            for i, row in enumerate(reader):
                if i == 0 and row and row[0].lower() == "ytid":
                    continue
                if len(row) < 4:
                    continue
                try:
                    _, speaker, start, end, *rest = row
                    start_f = float(start)
                    end_f = float(end)
                    source_path = rest[1] if len(rest) > 1 else (rest[0] if rest else "")
                    rows.append((ytid, speaker, round(start_f, 3), round(end_f, 3), source_path))
                except Exception:
                    continue

        if not rows:
            logger.warning("No diarization spans parsed from %s", timestamps_path)
            return 0

        inserted = 0
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    if replace_existing:
                        cur.execute("DELETE FROM diarized_timestamps WHERE ytid=%s", (ytid,))
                    execute_values(
                        cur,
                        """
                        INSERT INTO diarized_timestamps
                          (ytid, speaker_name, start_sec, end_sec, source_path)
                        VALUES %s
                        ON CONFLICT (ytid, speaker_name, start_sec, end_sec) DO NOTHING
                        """,
                        rows,
                    )
                    inserted = len(rows)
        except Exception as exc:
            logger.warning("Failed to insert diarization spans into DB for %s: %s", ytid, exc)
            return 0
        logger.info("Inserted %s diarized spans for %s", inserted, ytid)
        return inserted
