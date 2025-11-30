# vidops/services/transcription.py

import gc
import logging
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from faster_whisper import WhisperModel

from vidops.config import load_config
from vidops.dal import (
    VideoRepository,
    JobRepository,
    TranscriptRepository,
    WordRepository,
    FilesystemCache,
)
from vidops.models import Job, JobStatus, Transcript, Word

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

        # Whisper model cache
        self._model: Optional[WhisperModel] = None
        self._loaded_model_name: Optional[str] = None
        self._loaded_device: Optional[str] = None
        self._loaded_compute: Optional[str] = None

    def enqueue_video(
        self,
        ytid: str,
        model: str,
        language: str = "en",
        priority: int = 0,
        force: bool = False
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

    def enqueue_pending_videos(
        self,
        model: str,
        limit: int = 100,
        language: str = "en",
        priority: int = 0
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
        """
        if not job.ytid or not job.media_path:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid or media_path.")
            return

        try:
            # 1. Resolve media file path
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

            # 3. Execute transcription with faster-whisper
            model_name = self._resolve_model_name(job.config)
            language = self._resolve_language(job.config)
            vad_filter = self._resolve_vad_filter(job.config)

            logger.info(
                "Starting transcription for %s using model=%s, language=%s",
                job.ytid,
                model_name,
                language or "auto",
            )

            start_time = time.time()
            segments = self._run_whisper(
                media_path=Path(media_local_path),
                model_name=model_name,
                language=language,
                vad_filter=vad_filter,
                config=job.config,
            )
            if not segments:
                logger.warning("Whisper produced no segments for %s. Continuing with empty transcript.", job.ytid)

            # 4. Persist transcripts + words
            words = self._segments_to_words(job.ytid, model_name, segments)
            words_count = len(words)
            segment_count = len(segments)

            if words_count:
                self.word_repo.bulk_insert(words)
            else:
                logger.warning("Transcription produced no per-word entries for %s", job.ytid)

            vtt_content = self._segments_to_vtt(segments)
            words_tsv_content = self._words_to_tsv(words)

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

            local_vtt = self.fs_cache.write_transcript(vtt_transcript, vtt_content, "vtt")
            local_words = self.fs_cache.write_transcript(words_transcript, words_tsv_content, "words.tsv")

            vtt_rel = self._persist_transcript_asset(Path(local_vtt), job.ytid, "transcript_vtt")
            words_rel = self._persist_transcript_asset(Path(local_words), job.ytid, "transcript_words")

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
            logger.info(
                "Successfully transcribed %s with model %s in %.2fs",
                job.ytid,
                model_name,
                processing_time,
            )

        except Exception as e:
            error_msg = f"Transcription failed for job {job.job_id} ({job.ytid}): {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

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
        central_path = self.fs_cache.get_central_path(str(relative))
        central_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, central_path)
        try:
            self.fs_cache.register_asset(
                video_repo=self.video_repo,
                relative_path=str(relative),
                ytid=ytid,
                kind=asset_kind,
            )
        except Exception as exc:
            logger.warning("Failed to register asset %s (%s): %s", local_path, asset_kind, exc)
        return relative
