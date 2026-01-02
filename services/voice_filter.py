import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from dal import FilesystemCache, JobRepository, VideoRepository
from models import Job, JobStatus
from services.voice_filter_native import run_voice_filter

logger = logging.getLogger(__name__)

VOICE_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".mp4", ".mkv", ".mov"}


class VoiceFilterService:
    """
    Native voice filtering (no workspace.sh) with legacy outputs (voice_analysis.json, hasan_clips.txt).
    """

    def __init__(
        self,
        video_repo: VideoRepository,
        job_repo: JobRepository,
        fs_cache: FilesystemCache,
    ):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.fs_cache = fs_cache

    def enqueue_job(
        self,
        ytid: str,
        clips_path: str,
        reference_paths: List[str],
        threshold: float = 0.7,
        method: str = "chunked",
        priority: int = 50,
        output_dir: Optional[str] = None,
    ) -> Job:
        """
        Enqueue a voice-filter job for the provided video.
        """
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")
        if not reference_paths:
            raise ValueError("At least one reference clip must be provided.")

        clips_rel = self._normalize_relative(clips_path, allow_dir=True)
        ref_rel_paths = [self._normalize_relative(path, allow_dir=False) for path in reference_paths]
        output_rel = self._normalize_output_dir(output_dir, ytid)

        config = {
            "ytid": ytid,
            "clips_relative": clips_rel,
            "reference_rel_paths": ref_rel_paths,
            "threshold": threshold,
            "method": method,
            "output_dir": output_rel,
        }

        job = Job(
            job_type="voice",
            ytid=ytid,
            media_path=clips_rel,
            config=config,
            priority=priority,
            status=JobStatus.PENDING,
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Execute voice filtering for a claimed job via the legacy workspace.sh bridge.
        """
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job missing ytid.")
            return

        try:
            clips_rel = job.config.get("clips_relative") or job.config.get("clips_path")
            reference_rel_paths = job.config.get("reference_rel_paths") or job.config.get("reference_paths") or []
            threshold = float(job.config.get("threshold", 0.7))
            method = job.config.get("method", "chunked")
            output_rel = self._normalize_output_dir(job.config.get("output_dir"), job.ytid)

            if not clips_rel:
                raise ValueError("Job missing clips path.")
            if not reference_rel_paths:
                raise ValueError("Job missing reference clips.")

            workspace_root = self._workspace_root()
            clips_dir, staged_clips = self._stage_clips(clips_rel, workspace_root, job.job_id)
            reference_paths = self._stage_references(reference_rel_paths, workspace_root, job.ytid)
            output_dir = workspace_root / output_rel
            output_dir.mkdir(parents=True, exist_ok=True)

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting voice filter.")
            start_time = time.time()
            results_path, matches_path = self._run_native_voice(
                job,
                workspace_root,
                clips_dir,
                reference_paths,
                output_dir,
                threshold,
                method,
                staged_clips,
            )
            duration = time.time() - start_time

            results_payload, clips_processed, matches = self._parse_results(results_path, matches_path)
            job_result = self._persist_outputs(
                job,
                output_rel,
                results_path,
                matches_path,
                threshold,
                method,
                clips_processed,
                matches,
                duration,
                results_payload,
            )
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info(
                "Voice filter job %s completed (%d/%d matches)", job.job_id, matches, clips_processed
            )
        except Exception as exc:
            error_msg = f"Voice filter failed for job {job.job_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    # ------------------------------------------------------------------
    # Internal helpers
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
            try:
                rel = path.relative_to(self.fs_cache.central_storage_root)
            except ValueError:
                raise ValueError(
                    f"Path must live under central storage ({self.fs_cache.central_storage_root}): {path}"
                ) from None
            if not allow_dir and rel.name == "":
                raise ValueError(f"Expected file path, got directory: {path}")
            return str(rel)
        return path_str

    def _normalize_output_dir(self, output_dir: Optional[str], ytid: str) -> str:
        base = output_dir or f"results/voice_filter/{ytid}"
        return self._normalize_relative(base, allow_dir=True)

    def _stage_clips(self, clips_relative: str, workspace_root: Path, job_id: str) -> Tuple[Path, List[Path]]:
        source_dir = self.fs_cache.get_central_path(clips_relative)
        if not source_dir.is_dir():
            raise FileNotFoundError(f"Clips directory not found: {source_dir}")

        dest_dir = workspace_root / "media" / "clips" / "voice" / job_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        staged: List[Path] = []
        for entry in sorted(source_dir.rglob("*")):
            if not entry.is_file() or entry.suffix.lower() not in VOICE_EXTENSIONS:
                continue
            try:
                rel = entry.relative_to(self.fs_cache.central_storage_root)
            except ValueError as exc:
                raise ValueError(f"Clip {entry} is outside central storage") from exc
            cached = self.fs_cache.pull_to_cache(str(rel))
            target = dest_dir / entry.relative_to(source_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(cached, target)
            staged.append(target)

        if not staged:
            raise FileNotFoundError(f"No audio/video clips found in {source_dir}")
        return dest_dir, staged

    def _stage_references(self, reference_rel_paths: List[str], workspace_root: Path, ytid: str) -> List[Path]:
        dest_dir = workspace_root / "generated" / "voice_reference" / ytid
        dest_dir.mkdir(parents=True, exist_ok=True)

        staged: List[Path] = []
        for ref in reference_rel_paths:
            ref_path = self.fs_cache.get_central_path(ref)
            if not ref_path.exists():
                raise FileNotFoundError(f"Reference clip not found: {ref_path}")

            candidates = (
                sorted(p for p in ref_path.rglob("*") if p.is_file())
                if ref_path.is_dir()
                else [ref_path]
            )
            for cand in candidates:
                if cand.suffix.lower() not in VOICE_EXTENSIONS:
                    continue
                rel = cand.relative_to(self.fs_cache.central_storage_root)
                cached = self.fs_cache.pull_to_cache(str(rel))
                target = dest_dir / (cand.relative_to(ref_path.parent) if ref_path.is_file() else cand.relative_to(ref_path))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cached, target)
                staged.append(target)

        if not staged:
            raise FileNotFoundError("No reference clips staged")
        return staged

    def _run_native_voice(
        self,
        job: Job,
        workspace_root: Path,
        clips_dir: Path,
        reference_paths: List[Path],
        output_dir: Path,
        threshold: float,
        method: str,
        staged_clips: List[Path],
    ) -> Tuple[Path, Path]:
        if os.environ.get("VIDOPS_FAKE_VOICE", "").lower() in {"1", "true"}:
            return self._write_fake_voice_outputs(output_dir, staged_clips)

        mode = method or "chunked"
        results_path, matches_path = run_voice_filter(
            clips_dir=clips_dir,
            reference_paths=reference_paths,
            output_dir=output_dir,
            threshold=threshold,
            mode=mode,
        )
        if not results_path.exists() or not matches_path.exists():
            raise FileNotFoundError(f"Voice outputs missing in {output_dir}")
        return results_path, matches_path

    def _write_fake_voice_outputs(self, output_dir: Path, staged_clips: List[Path]) -> Tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        payload: List[dict] = []
        for idx, clip in enumerate(sorted(staged_clips)):
            payload.append(
                {
                    "file": str(clip),
                    "similarity": round(0.9 - idx * 0.1, 3),
                    "is_hasan": idx % 2 == 0,
                }
            )

        results_path = output_dir / "voice_analysis.json"
        matches_path = output_dir / "hasan_clips.txt"
        results_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        matches = [p for p in staged_clips if staged_clips and p == staged_clips[0]]
        matches_path.write_text("\n".join(str(m) for m in matches), encoding="utf-8")
        return results_path, matches_path

    def _parse_results(self, results_path: Path, matches_path: Path) -> Tuple[List[dict], int, int]:
        data = json.loads(results_path.read_text(encoding="utf-8"))
        results_list = data.get("results") if isinstance(data, dict) else data
        if not isinstance(results_list, list):
            results_list = []
        clips_processed = len(results_list)
        matches = sum(1 for item in results_list if item.get("is_match") or item.get("is_hasan"))
        if not matches and matches_path.exists():
            matches = len([ln for ln in matches_path.read_text(encoding="utf-8").splitlines() if ln.strip()])
        return results_list, clips_processed, matches

    def _persist_outputs(
        self,
        job: Job,
        output_rel: str,
        results_path: Path,
        matches_path: Path,
        threshold: float,
        method: str,
        clips_processed: int,
        matches: int,
        duration: float,
        results_payload: List[dict],
    ) -> dict:
        rel_base = Path(output_rel.strip("/"))
        results_rel = str(rel_base / "voice_analysis.json")
        matches_rel = str(rel_base / "hasan_clips.txt")

        self.fs_cache.persist_local_artifact(
            results_path,
            results_rel,
            self.video_repo,
            job.ytid,
            kind="voice_match",
        )
        self.fs_cache.persist_local_artifact(
            matches_path,
            matches_rel,
            self.video_repo,
            job.ytid,
            kind="voice_match",
        )

        return {
            "results_path": results_rel,
            "matches_path": matches_rel,
            "matches": matches,
            "clips_processed": clips_processed,
            "threshold": threshold,
            "method": method,
            "duration_seconds": round(duration, 3),
            "results": results_payload,
        }
