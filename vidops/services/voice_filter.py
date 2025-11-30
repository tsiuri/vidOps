# vidops/services/voice_filter.py

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from vidops.config import load_config
from vidops.dal import VideoRepository, JobRepository, FilesystemCache
from vidops.models import Job, JobStatus

logger = logging.getLogger(__name__)

VOICE_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".mp4", ".mkv", ".mov"}


@dataclass
class VoiceFilterResult:
    clip_path: str
    similarity: float
    is_match: bool
    error: Optional[str] = None


class VoiceFilterEngine:
    """
    Thin wrapper around Resemblyzer for speaker verification.
    """

    def __init__(self, device: Optional[str] = None):
        try:
            import torch
            from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Voice filtering requires torch, torchaudio, and resemblyzer. "
                "Install the extra dependencies via `pip install -r requirements.txt`."
            ) from exc

        self._torch = torch
        self._VoiceEncoder = VoiceEncoder
        self._preprocess_wav = preprocess_wav
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.encoder = self._VoiceEncoder(device=self.device)

    def build_reference(self, reference_paths: List[Path]) -> np.ndarray:
        embeddings = []
        for ref in reference_paths:
            try:
                wav = self._preprocess_wav(str(ref))
                embeddings.append(self.encoder.embed_utterance(wav))
            except Exception as exc:  # pragma: no cover - diagnostic log
                logger.warning("Failed to build voice profile from %s: %s", ref, exc)
        if not embeddings:
            raise RuntimeError("No usable reference clips found for voice filtering.")
        return np.mean(embeddings, axis=0)

    def analyze_clip(self, clip_path: Path, profile: np.ndarray) -> float:
        wav = self._preprocess_wav(str(clip_path))
        clip_embedding = self.encoder.embed_utterance(wav)
        return float(np.dot(profile, clip_embedding))


class VoiceFilterService:
    """
    Voice filtering backed by Resemblyzer. Jobs describe a directory of clips to scan
    plus reference audio clips for the desired speaker.
    """

    def __init__(
        self,
        video_repo: VideoRepository,
        job_repo: JobRepository,
        fs_cache: FilesystemCache
    ):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.fs_cache = fs_cache
        self.config = load_config()

    def enqueue_job(
        self,
        ytid: str,
        clips_path: str,
        reference_paths: List[str],
        threshold: float = 0.7,
        method: str = "chunked",
        priority: int = 0,
        output_dir: Optional[str] = None
    ) -> Job:
        """
        Enqueue a voice-filter job for the provided video.
        """
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")

        if not reference_paths:
            raise ValueError("At least one reference clip must be provided.")

        config = {
            "ytid": ytid,
            "clips_path": clips_path,
            "reference_paths": reference_paths,
            "threshold": threshold,
            "method": method,
            "output_dir": output_dir,
        }

        job = Job(
            job_type="voice",
            ytid=ytid,
            media_path=clips_path,
            config=config,
            priority=priority,
            status=JobStatus.PENDING
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Execute voice filtering for a claimed job.
        """
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job missing ytid.")
            return

        try:
            clips_dir = self._resolve_path(job.config.get("clips_path"))
            reference_paths = [self._resolve_path(path) for path in job.config.get("reference_paths", [])]
            threshold = float(job.config.get("threshold", 0.7))
            output_dir = job.config.get("output_dir") or f"results/voice_filter/{job.ytid}"

            if not clips_dir.exists():
                raise FileNotFoundError(f"Clips directory not found: {clips_dir}")

            for ref in reference_paths:
                if not ref.exists():
                    raise FileNotFoundError(f"Reference clip not found: {ref}")

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting voice filter.")

            start_time = time.time()
            engine = VoiceFilterEngine()
            profile = engine.build_reference(reference_paths)
            results = self._scan_directory(engine, profile, clips_dir, threshold)
            duration = time.time() - start_time

            matched = [r for r in results if r.is_match]
            payload = {
                "job_id": job.job_id,
                "ytid": job.ytid,
                "clips_path": str(clips_dir),
                "reference_count": len(reference_paths),
                "threshold": threshold,
                "clips_processed": len(results),
                "matches": len(matched),
                "duration_seconds": round(duration, 3),
                "results": [r.__dict__ for r in results],
            }

            local_json = self.fs_cache.prepare_local_path(f"voice_filter/tmp/{job.job_id}.json")
            local_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            local_matches = self.fs_cache.prepare_local_path(f"voice_filter/tmp/{job.job_id}_matches.txt")
            with local_matches.open("w", encoding="utf-8") as handle:
                for result in matched:
                    handle.write(f"{result.clip_path}\t{result.similarity:.6f}\n")

            relative_base = Path(output_dir.strip("/"))
            json_rel = str(relative_base / f"{job.job_id}_results.json")
            matches_rel = str(relative_base / f"{job.job_id}_matches.txt")

            self.fs_cache.persist_local_artifact(local_json, json_rel, self.video_repo, job.ytid, "voice_filter")
            self.fs_cache.persist_local_artifact(local_matches, matches_rel, self.video_repo, job.ytid, "voice_filter")

            job_result = {
                "results_path": json_rel,
                "matches_path": matches_rel,
                "matches": len(matched),
                "clips_processed": len(results),
                "threshold": threshold,
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Voice filter job %s completed (%d/%d matches)", job.job_id, len(matched), len(results))

            for tmp in (local_json, local_matches):
                if tmp.exists():
                    tmp.unlink()

        except Exception as exc:
            error_msg = f"Voice filter failed for job {job.job_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    def _scan_directory(
        self,
        engine: VoiceFilterEngine,
        profile: np.ndarray,
        clips_dir: Path,
        threshold: float
    ) -> List[VoiceFilterResult]:
        clips = sorted(
            p for p in clips_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in VOICE_EXTENSIONS
        )
        if not clips:
            logger.warning("No audio/video clips found under %s", clips_dir)

        central_root = self.fs_cache.central_storage_root
        results: List[VoiceFilterResult] = []
        for clip in clips:
            try:
                similarity = engine.analyze_clip(clip, profile)
                rel_path = clip
                try:
                    rel_path = clip.relative_to(central_root)
                except ValueError:
                    pass
                results.append(
                    VoiceFilterResult(
                        clip_path=str(rel_path),
                        similarity=similarity,
                        is_match=similarity >= threshold,
                    )
                )
            except Exception as exc:
                results.append(
                    VoiceFilterResult(
                        clip_path=str(clip),
                        similarity=0.0,
                        is_match=False,
                        error=str(exc)
                    )
                )
        return results

    def _resolve_path(self, candidate: Optional[str]) -> Path:
        if not candidate:
            raise ValueError("Path configuration is missing.")
        path = Path(candidate)
        if path.is_absolute():
            return path
        return self.fs_cache.get_central_path(candidate)
