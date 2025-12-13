# vidops/services/transcription.py

import gc
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from faster_whisper import WhisperModel
from configuration import load_config
from db import get_connection
from dal import (
    VideoRepository,
    JobRepository,
    TranscriptRepository,
    WordRepository,
    FilesystemCache,
)
from models import Job, JobStatus, Transcript, Word

logger = logging.getLogger(__name__)

class TranscriptionService:
    """
    Orchestrates the transcription process, managing job creation,
    dispatch, and result handling.
    """

    def __init__(
        self,
        video_repo: VideoRepository,
        job_repo: JobRepository,
        transcript_repo: TranscriptRepository,
        word_repo: WordRepository,
        fs_cache: FilesystemCache
    ):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.transcript_repo = transcript_repo
        self.word_repo = word_repo
        self.fs_cache = fs_cache
        self.config = load_config()

        # Whisper model cache (retained for compatibility with any direct invocations)
        self._model: Optional[WhisperModel] = None
        self._loaded_model_name: Optional[str] = None
        self._loaded_device: Optional[str] = None
        self._loaded_compute: Optional[str] = None

    def enqueue_video(
        self,
        ytid: str,
        model: str,
        language: str = "en",
        priority: int = 50,
        force: bool = False,
        force_job: bool = False,
    ) -> Job:
        """
        Enqueues a single video for transcription.

        Args:
            ytid: YouTube ID of the video.
            model: The Whisper model to use (e.g., 'medium').
            language: The language of the video (e.g., 'en').
            priority: Job priority (higher value = higher priority).
            force: If True, enqueue even if a transcript already exists.

        Returns:
            The created Job object.

        Raises:
            ValueError: If the video is not found or already transcribed without force.
        """
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")
        
        if not video.ytid:
            raise ValueError(f"Video {ytid} has no associated media_path to transcribe.")

        # Check if transcript already exists unless force is true
        existing_transcript = self.transcript_repo.get(ytid, f"words_whisper_{model}")
        if existing_transcript and not force:
            raise ValueError(f"Transcript for video '{ytid}' with model '{model}' already exists.")

        # Check for duplicate in-flight jobs for same video/model
        if not force_job:
            dup = self._find_inflight_job(ytid=ytid, model=model)
            if dup:
                raise ValueError(f"Transcription job already pending/running for '{ytid}' model '{model}' (job_id={dup})")

        # Create the job configuration
        config = {
            "model": model,
            "language": language,
            "ytid": ytid, # Redundant but explicit for job processing
            "media_path_hint": video.url # Hint for where to find the media
        }

        # Create the job in the database
        job = Job(
            job_type="transcription",
            ytid=ytid,
            media_path=video.url, # Use video URL as source for media
            config=config,
            priority=priority,
            status=JobStatus.PENDING
        )
        return self.job_repo.create(job)

    def _find_inflight_job(self, ytid: str, model: str) -> str | None:
        """Return job_id of an existing pending/claimed/running job for this video/model."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT job_id
                    FROM jobs
                    WHERE job_type='transcription'
                      AND ytid=%s
                      AND (config->>'model')=%s
                      AND status IN ('pending','claimed','running')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (ytid, model),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def enqueue_pending_videos(
        self,
        model: str,
        limit: int = 100,
        language: str = "en",
        priority: int = 50
    ) -> List[Job]:
        """
        Enqueues videos that do not yet have a transcript for the specified model.

        Args:
            model: The Whisper model to check for.
            limit: Maximum number of videos to enqueue.
            language: Default language for new jobs.
            priority: Priority for new jobs.

        Returns:
            A list of created Job objects.
        """
        videos_to_transcribe = self.video_repo.get_without_transcripts(model, limit)
        enqueued_jobs = []
        for video in videos_to_transcribe:
            try:
                job = self.enqueue_video(video.ytid, model, language, priority, force=False)
                enqueued_jobs.append(job)
            except ValueError as e:
                logger.warning(f"Skipping video '{video.ytid}' for enqueueing: {e}")
            except Exception as e:
                logger.error(f"Error enqueueing video '{video.ytid}': {e}")
        return enqueued_jobs

    def process_job(self, job: Job) -> None:
        """
        Processes a single transcription job claimed by a worker.

        This method encapsulates the worker's logic for executing the transcription.
        Handles both full-video and clip transcriptions.
        """
        if not job.ytid or not job.media_path:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid or media_path.")
            return

        try:
            # 1. Resolve media file path
            # Check if this is a clip transcription (has clip_context in config)
            is_clip_transcription = "clip_context" in job.config

            if is_clip_transcription:
                # For clip transcription, use the media_path directly (it's the clip file path)
                clip_media_path = job.config.get("clip_context", {}).get("clip_media_path", job.media_path)
                media_local_path = self.fs_cache.pull_to_cache(clip_media_path)
                if not media_local_path or not Path(media_local_path).exists():
                    raise FileNotFoundError(
                        f"Clip media file not found for path '{clip_media_path}' (resolved to {media_local_path})."
                    )
            else:
                # For full-video transcription, look up the video and get its media
                video_obj = self.video_repo.get(job.ytid)
                if not video_obj:
                    raise ValueError(f"Video object not found for ytid: {job.ytid}")

                media_local_path = self.fs_cache.get_media_path(video_obj, pull_to_local=True)
                if not media_local_path or not Path(media_local_path).exists():
                    raise FileNotFoundError(
                        f"Media file not found for ytid '{job.ytid}' (expected at {job.media_path})."
                    )

            # 2. Update job status to RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING)

            # 3. Reconstruct legacy inputs and run the legacy transcribe path
            model_name = self._resolve_model_name(job.config)
            language = self._resolve_language(job.config)
            legacy_workspace, legacy_media_path, filelist_path = self._prepare_legacy_inputs(
                Path(media_local_path), job
            )
            logger.info(
                "Launching legacy transcription for %s using model=%s language=%s",
                job.ytid,
                model_name,
                language or "auto",
            )
            start_time = time.time()
            legacy_result = self._run_legacy_transcribe(
                workspace_root=legacy_workspace,
                filelist_path=filelist_path,
                model_name=model_name,
                language=language,
                job=job,
            )
            if legacy_result.returncode != 0:
                stderr = (getattr(legacy_result, "stderr", "") or "").strip()
                stdout = (getattr(legacy_result, "stdout", "") or "").strip()
                known_safe = "ROCm unavailable; skipping AMD worker."
                if known_safe in stderr or known_safe in stdout:
                    logger.warning(
                        "Legacy transcribe returned %s with known-safe warning (%s); ingesting outputs",
                        legacy_result.returncode,
                        known_safe,
                    )
                else:
                    logger.warning(
                        "Legacy transcribe exited with code %s; attempting to ingest outputs anyway (stderr=%s)",
                        legacy_result.returncode,
                        stderr,
                    )

            # 4. Ingest legacy outputs
            vtt_path, words_path = self._locate_legacy_outputs(legacy_workspace, job.ytid)
            words = self._parse_legacy_words(words_path, job.ytid, model_name)
            words_count = len(words)
            segment_count = self._estimate_segments(words)

            if words_count:
                self.word_repo.bulk_insert(words)
            else:
                logger.warning("Legacy transcription produced no per-word entries for %s", job.ytid)

            vtt_content = Path(vtt_path).read_text(encoding="utf-8")
            words_tsv_content = Path(words_path).read_text(encoding="utf-8")

            # 5. Store transcripts via storage manager and register assets
            vtt_kind = f"vtt_whisper_{model_name}"
            words_kind = f"words_whisper_{model_name}"

            vtt_transcript = Transcript(
                ytid=job.ytid,
                kind=vtt_kind,
                lang=language or self.config.transcription.language,
                word_count=words_count,
                segment_count=segment_count,
            )
            words_transcript = Transcript(
                ytid=job.ytid,
                kind=words_kind,
                lang=language or self.config.transcription.language,
                word_count=words_count,
                segment_count=segment_count,
            )

            vtt_rel = self._persist_transcript_asset(Path(vtt_path), job.ytid, "transcript_vtt")
            words_rel = self._persist_transcript_asset(Path(words_path), job.ytid, "transcript_words")

            vtt_transcript.path = str(vtt_rel)
            words_transcript.path = str(words_rel)

            self.transcript_repo.upsert(vtt_transcript)
            self.transcript_repo.upsert(words_transcript)

            processing_time = time.time() - start_time

            # 6. Update job status to COMPLETED
            job_result = {
                "vtt_path": str(vtt_rel),
                "words_path": str(words_rel),
                "word_count": words_count,
                "segment_count": segment_count,
                "model": model_name,
                "processing_seconds": round(processing_time, 3),
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)

            # 7. Handle clip transcription completion if this is a clip context job
            if "clip_context" in job.config:
                logger.info(f"Clip transcription completed for job {job.job_id}, handling clip completion")
                self._handle_clip_transcription_completion(
                    job=job,
                    model_name=model_name,
                    language=language,
                    job_result=job_result,
                    words=words,
                )

            logger.info(
                "Successfully transcribed %s with model %s in %.2fs via legacy runner",
                job.ytid,
                model_name,
                processing_time,
            )

        except Exception as e:
            error_msg = f"Transcription failed for job {job.job_id} ({job.ytid}): {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    def _handle_clip_transcription_completion(
        self,
        job: Job,
        model_name: str,
        language: Optional[str],
        job_result: Dict[str, Any],
        words: List[Word],
    ) -> None:
        """
        Handle completion of a clip transcription job.
        Updates clip transcript metadata and registers transcript artifacts.

        Since we transcribe the clip file directly (not the full video),
        all words returned are already for the clip.

        Args:
            job: The transcription job with clip_context in config
            model_name: The Whisper model used
            language: The language transcribed
            job_result: Result dict with transcript file paths
            words: List of Word objects for this clip transcription
        """
        try:
            from db import get_connection
            import json

            clip_context = job.config.get("clip_context", {})
            clip_id = clip_context.get("clip_id")
            session_id = clip_context.get("session_id")

            if not clip_id:
                logger.warning("Clip transcription job has no clip_id in context, skipping update")
                return

            # All words are already for the clip (we transcribed the clip file directly)
            clip_word_count = len(words)

            # Build transcript text from clip words
            clip_text = " ".join([getattr(w, 'word', '') for w in words if getattr(w, 'word', '')])

            # Update clip transcript metadata in quickclip_clips
            from psycopg2.extras import Json
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Get current transcripts JSONB
                    cur.execute(
                        "SELECT transcripts FROM quickclip_clips WHERE clip_id = %s",
                        (clip_id,)
                    )
                    row = cur.fetchone()
                    transcripts = row[0] if row and row[0] else {}

                    # Update transcript entry with completed data
                    if model_name in transcripts:
                        transcripts[model_name].update({
                            "status": "completed",
                            "completed_at": datetime.utcnow().isoformat(),
                            "word_count": clip_word_count,
                            "text_preview": clip_text[:200] if clip_text else "(no speech detected)",
                            "vtt_path": job_result.get("vtt_path"),
                            "words_path": job_result.get("words_path"),
                        })
                        logger.info(f"Updated transcript for clip {clip_id} model {model_name} with vtt_path={job_result.get('vtt_path')} and words_path={job_result.get('words_path')}")
                    else:
                        logger.warning(f"Model {model_name} not found in transcripts for clip {clip_id}; transcripts keys: {list(transcripts.keys())}")

                    # Write back to database with Json wrapper for JSONB
                    cur.execute(
                        "UPDATE quickclip_clips SET transcripts = %s WHERE clip_id = %s",
                        (Json(transcripts), clip_id)
                    )
                    conn.commit()  # Explicitly commit the transaction
                    logger.debug(f"Committed transcript update for clip {clip_id}: {transcripts}")

            # Register transcript artifacts in assets table with clip_id
            if job_result.get("vtt_path"):
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        try:
                            cur.execute(
                                """
                                INSERT INTO assets (ytid, kind, path, rel_path, clip_id, bytes, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                ON CONFLICT (path) DO UPDATE SET clip_id = EXCLUDED.clip_id
                                """,
                                (
                                    job.ytid,
                                    "transcript_vtt",
                                    job_result.get("vtt_path"),
                                    job_result.get("vtt_path"),
                                    clip_id,
                                    0,  # Will be updated when file is actually stored
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Failed to register VTT asset for clip {clip_id}: {e}")

            if job_result.get("words_path"):
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        try:
                            cur.execute(
                                """
                                INSERT INTO assets (ytid, kind, path, rel_path, clip_id, bytes, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                ON CONFLICT (path) DO UPDATE SET clip_id = EXCLUDED.clip_id
                                """,
                                (
                                    job.ytid,
                                    "transcript_words",
                                    job_result.get("words_path"),
                                    job_result.get("words_path"),
                                    clip_id,
                                    0,  # Will be updated when file is actually stored
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Failed to register words asset for clip {clip_id}: {e}")

            logger.info(
                f"Clip transcription completed for {clip_id}: {clip_word_count} words extracted, "
                f"metadata updated, assets registered"
            )

        except Exception as e:
            logger.error(
                f"Failed to handle clip transcription completion for job {job.job_id}: {e}",
                exc_info=True
            )

    def update_job_status(self, job_id: str, status: JobStatus, message: Optional[str] = None, result: Optional[Dict[str, Any]] = None):
        """
        Wrapper to update a job's status in the database.
        """
        self.job_repo.update_status(job_id, status, error_message=message, result=result)

    def release_job(self, job_id: str):
        """
        Releases a claimed job back to pending status.
        """
        self.job_repo.release(job_id)

    def get_job(self, job_id: str) -> Optional[Job]:
        """
        Retrieves a job by its ID.
        """
        return self.job_repo.get(job_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_model_name(self, config: Dict[str, Any]) -> str:
        requested = config.get("model") or self.config.transcription.model
        return str(requested)

    def _resolve_language(self, config: Dict[str, Any]) -> Optional[str]:
        language = config.get("language") or self.config.transcription.language
        if not language:
            return None
        if isinstance(language, str) and language.lower() in {"auto", "detect"}:
            return None
        return language

    def _resolve_vad_filter(self, config: Dict[str, Any]) -> bool:
        if "vad_filter" in config:
            value = config["vad_filter"]
            if isinstance(value, str):
                return value.lower() in {"1", "true", "yes", "on"}
            return bool(value)
        return bool(self.config.transcription.nvidia.vad_filter)

    def _determine_runtime(self, config: Dict[str, Any]) -> Tuple[str, str, Optional[int]]:
        """Choose device, compute type, and cpu_threads."""
        override_value = config.get("device")
        if not override_value:
            override_value = os.environ.get("VIDOPS_TRANSCRIBE_DEVICE")
        device_override = override_value.lower() if isinstance(override_value, str) else ""
        if device_override not in {"cpu", "cuda"}:
            device_override = ""

        device = device_override or ("cuda" if self._gpu_available() else "cpu")

        if device == "cuda":
            compute = self.config.transcription.nvidia.compute_type
            return device, compute, None

        # CPU runtime
        compute = self.config.transcription.cpu.compute_type
        return "cpu", compute, int(self.config.transcription.cpu.threads)

    def _gpu_available(self) -> bool:
        """Best-effort GPU detection without raising if torch is missing."""
        cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES")
        if cuda_visible is not None and cuda_visible.strip() in {"", "-1"}:
            return False
        try:
            import torch

            return torch.cuda.is_available()
        except Exception:
            # Fall back to simple heuristic: assume GPU present if nvidia-smi exists
            return shutil.which("nvidia-smi") is not None

    def _ensure_model_loaded(self, model_name: str, device: str, compute: str, cpu_threads: Optional[int]) -> WhisperModel:
        """Load whisper model if not already cached for given runtime."""
        if (
            self._model
            and self._loaded_model_name == model_name
            and self._loaded_device == device
            and self._loaded_compute == compute
        ):
            return self._model

        # Release previous model before loading a new one to free VRAM/RAM
        if self._model is not None:
            del self._model
            gc.collect()
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:
                pass

        logger.info("Loading Whisper model '%s' on %s (compute=%s)", model_name, device, compute)
        kwargs: Dict[str, Any] = {
            "device": device,
            "compute_type": compute,
        }
        if device == "cpu":
            kwargs["cpu_threads"] = cpu_threads or self.config.transcription.cpu.threads
            kwargs["num_workers"] = cpu_threads or self.config.transcription.cpu.threads
        else:
            kwargs["num_workers"] = int(os.environ.get("WHISPER_NUM_WORKERS", "2"))

        try:
            self._model = WhisperModel(model_name, **kwargs)
            self._loaded_model_name = model_name
            self._loaded_device = device
            self._loaded_compute = compute
        except Exception as exc:
            if device == "cuda":
                logger.warning("Failed to load GPU model '%s': %s. Falling back to CPU.", model_name, exc)
                return self._ensure_model_loaded(model_name, "cpu", self.config.transcription.cpu.compute_type, self.config.transcription.cpu.threads)
            raise

        return self._model

    def _run_whisper(
        self,
        media_path: Path,
        model_name: str,
        language: Optional[str],
        vad_filter: bool,
        config: Optional[Dict[str, Any]] = None,
    ):
        device, compute, cpu_threads = self._determine_runtime(config or {})
        model = self._ensure_model_loaded(model_name, device, compute, cpu_threads)
        segments, _ = model.transcribe(
            str(media_path),
            language=language,
            vad_filter=vad_filter,
            word_timestamps=True,
        )
        return list(segments)

    def _segments_to_words(self, ytid: str, model_name: str, segments: List[Any]) -> List[Word]:
        words: List[Word] = []
        idx = 0
        source = f"whisper-{model_name}"
        for seg_idx, seg in enumerate(segments):
            seg_text = (getattr(seg, "text", "") or "").strip()
            seg_start = float(getattr(seg, "start", 0.0))
            seg_end = float(getattr(seg, "end", seg_start))
            confidence = float(getattr(seg, "avg_logprob", 0.0))
            seg_words = getattr(seg, "words", None)

            if seg_words:
                for raw_word in seg_words:
                    word_text = (getattr(raw_word, "word", "") or "").strip()
                    if not word_text:
                        continue
                    start = float(getattr(raw_word, "start", seg_start))
                    end = float(getattr(raw_word, "end", start))
                    words.append(
                        Word(
                            ytid=ytid,
                            source=source,
                            word=word_text,
                            start_sec=start,
                            end_sec=end,
                            confidence=confidence,
                            idx=idx,
                            segment_id=seg_idx,
                        )
                    )
                    idx += 1
            elif seg_text:
                words.append(
                    Word(
                        ytid=ytid,
                        source=source,
                        word=seg_text,
                        start_sec=seg_start,
                        end_sec=seg_end or seg_start,
                        confidence=confidence,
                        idx=idx,
                        segment_id=seg_idx,
                    )
                )
                idx += 1
        return words

    def _segments_to_vtt(self, segments: List[Any]) -> str:
        lines = ["WEBVTT", ""]
        for i, seg in enumerate(segments, start=1):
            start = self._format_timestamp(getattr(seg, "start", 0.0))
            end = self._format_timestamp(getattr(seg, "end", getattr(seg, "start", 0.0)))
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            lines.append(str(i))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _format_timestamp(self, seconds: float) -> str:
        total_ms = max(0, int(round(float(seconds) * 1000)))
        hours, remainder = divmod(total_ms // 1000, 3600)
        minutes, secs = divmod(remainder, 60)
        milliseconds = total_ms % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"

    def _words_to_tsv(self, words: List[Word]) -> str:
        lines = ["start\tend\tword\tseg\tconfidence\tretried"]
        for word in words:
            token = word.word.replace("\t", " ").replace("\n", " ").strip()
            lines.append(
                f"{word.start_sec:.3f}\t"
                f"{word.end_sec:.3f}\t"
                f"{token}\t"
                f"{word.segment_id if word.segment_id is not None else 0}\t"
                f"{(word.confidence or 0.0):.3f}\t0"
        )
        return "\n".join(lines) + ("\n" if lines else "")

    def _persist_transcript_asset(self, local_path: Path, ytid: str, asset_kind: str) -> Path:
        relative = Path("transcripts") / local_path.name
        stored_path = self.fs_cache.persist_local_artifact(
            local_path=local_path,
            relative_path=str(relative),
            video_repo=self.video_repo,
            ytid=ytid,
            kind=asset_kind,
        )
        return Path(stored_path)

    def _prepare_legacy_inputs(self, media_local_path: Path, job: Job) -> Tuple[Path, Path, Path]:
        """
        Place media where legacy scripts expect it (PROJECT_ROOT/pull) and build a filelist for workspace.sh.
        Returns (workspace_root, legacy_media_path, filelist_path).
        """
        # Reconstruct exactly where legacy expects files: under the worker's project root, not the repo root
        workspace_root = Path(
            os.environ.get("VIDOPS_PROJECT_ROOT")
            or os.environ.get("PWD")
            or Path.cwd()
        ).resolve()
        logger.info("Transcribe staging workspace_root=%s", workspace_root)

        pull_dir = workspace_root / "pull"
        generated_dir = workspace_root / "generated"
        logs_dir = workspace_root / "logs" / "transcribe"
        tmp_dir = workspace_root / "tmp"

        for d in (pull_dir, generated_dir, logs_dir, tmp_dir):
            d.mkdir(parents=True, exist_ok=True)

        legacy_media_path = pull_dir / media_local_path.name
        if not legacy_media_path.exists():
            shutil.copy2(media_local_path, legacy_media_path)

        filelist_path = tmp_dir / f"{job.job_id}_filelist.txt"
        filelist_path.write_text(str(legacy_media_path) + "\n", encoding="utf-8")
        return workspace_root, legacy_media_path, filelist_path

    def _run_legacy_transcribe(
        self,
        workspace_root: Path,
        filelist_path: Path,
        model_name: str,
        language: Optional[str],
        job: Job,
    ) -> subprocess.CompletedProcess:
        """Invoke the legacy workspace.sh transcribe path with DB-provided args."""
        tool_root = Path(__file__).resolve().parents[1]
        workspace_sh = tool_root / "workspace.sh"
        cmd = [
            "bash",
            str(workspace_sh),
            "transcribe",
            "--model",
            model_name,
            "--filelist",
            str(filelist_path),
            "--outfmt",
            "both",
            "--force",
        ]
        if language:
            cmd.extend(["--language", language])

        env = os.environ.copy()
        # Keep PROJECT_ROOT aligned with the legacy path (project root, not cache)
        env["PROJECT_ROOT"] = str(workspace_root)
        logger.info("Running legacy transcribe command: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            cwd=workspace_root,
            env=env,
            capture_output=False,  # inherit stdout/stderr so operators can see progress
            text=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info("Legacy transcribe completed for job %s", job.job_id)
        else:
            logger.warning(
                "Legacy transcribe returned %s for job %s",
                result.returncode,
                job.job_id,
            )
        return result

    def _locate_legacy_outputs(self, workspace_root: Path, ytid: str) -> Tuple[Path, Path]:
        """Find VTT and words outputs produced by the legacy runner."""
        generated_dir = workspace_root / "generated"
        if not generated_dir.exists():
            raise FileNotFoundError(f"Legacy generated dir missing: {generated_dir}")

        vtt_candidates = list(generated_dir.rglob(f"*{ytid}*.vtt"))
        words_candidates = []
        words_candidates.extend(generated_dir.rglob(f"*{ytid}*.words.tsv"))
        words_candidates.extend(generated_dir.rglob(f"*{ytid}*.words.yt.tsv"))
        words_candidates.extend(generated_dir.rglob(f"*{ytid}*.words.*.tsv"))

        if not vtt_candidates:
            raise FileNotFoundError(f"No VTT output found for {ytid} in {generated_dir}")
        if not words_candidates:
            raise FileNotFoundError(f"No words TSV output found for {ytid} in {generated_dir}")

        vtt_path = max(vtt_candidates, key=lambda p: p.stat().st_mtime)
        words_path = max(words_candidates, key=lambda p: p.stat().st_mtime)
        return vtt_path, words_path

    def _parse_legacy_words(self, words_path: Path, ytid: str, model_name: str) -> List[Word]:
        """Parse a legacy words TSV into Word models."""
        lines = [ln.strip() for ln in words_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return []

        header = lines[0].split("\t")
        has_header = all(k.lower() in {h.lower() for h in header} for k in ("start", "end", "word"))
        rows = lines[1:] if has_header else lines
        col_index = {name.lower(): idx for idx, name in enumerate(header)} if has_header else {}

        def _get(row_parts: List[str], key: str, default_idx: Optional[int] = None) -> Optional[str]:
            key = key.lower()
            if key in col_index:
                idx = col_index[key]
                return row_parts[idx] if idx < len(row_parts) else None
            if default_idx is not None and default_idx < len(row_parts):
                return row_parts[default_idx]
            return None

        words: List[Word] = []
        for idx, row in enumerate(rows):
            parts = row.split("\t")
            start_val = _get(parts, "start", 0)
            end_val = _get(parts, "end", 1)
            token_val = _get(parts, "word", 2)
            if not (start_val and end_val and token_val):
                continue
            confidence_val = _get(parts, "confidence")
            segment_val = _get(parts, "seg", 3)
            try:
                word = Word(
                    ytid=ytid,
                    source=f"whisper-{model_name}",
                    idx=idx,
                    word=token_val.strip(),
                    start_sec=float(start_val),
                    end_sec=float(end_val),
                    confidence=float(confidence_val) if confidence_val else None,
                    segment_id=int(segment_val) if segment_val is not None and segment_val != "" else None,
                )
                words.append(word)
            except ValueError:
                logger.warning("Skipping malformed legacy word row: %s", row)
                continue
        return words

    def _estimate_segments(self, words: List[Word]) -> int:
        """Approximate segment count from word segment IDs."""
        segment_ids = {w.segment_id for w in words if w.segment_id is not None}
        return max(segment_ids) + 1 if segment_ids else 0
