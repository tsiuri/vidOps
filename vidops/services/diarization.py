import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from vidops.dal import FilesystemCache, JobRepository, TranscriptRepository, VideoRepository
from vidops.models import Job, JobStatus

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

    def enqueue_diarization_job(
        self,
        ytid: str,
        transcript_kind: str,
        diarization_model: str,
        priority: int = 0,
        reference_dir: Optional[str] = None,
        words_path: Optional[str] = None,
        device: str = "auto",
        chunk_seconds: float = 6.0,
        overlap_seconds: float = 1.0,
        similarity_threshold: float = 0.6,
        gap_threshold: float = 0.15,
        output_dir: Optional[str] = None,
    ) -> Job:
        """
        Enqueues a single diarization job.
        """
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")

        transcript = self.transcript_repo.get(ytid, transcript_kind)
        if not transcript or not transcript.path:
            raise ValueError(f"Transcript of kind '{transcript_kind}' not found for video '{ytid}'.")

        media_asset = self._resolve_media_asset_path(ytid)
        words_rel = self._normalize_relative(words_path or transcript.path, allow_dir=False)
        ref_rel = self._normalize_relative(reference_dir or f"generated/diary_reference/{ytid}", allow_dir=True)
        output_rel = self._normalize_output_dir(output_dir, ytid)

        config = {
            "ytid": ytid,
            "transcript_kind": transcript_kind,
            "diarization_model": diarization_model,
            "media_asset_path": media_asset,
            "words_path": words_rel,
            "reference_dir": ref_rel,
            "device": device,
            "chunk_seconds": float(chunk_seconds),
            "overlap_seconds": float(overlap_seconds),
            "similarity_threshold": float(similarity_threshold),
            "gap_threshold": float(gap_threshold),
            "output_dir": output_rel,
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

    def process_job(self, job: Job) -> None:
        """
        Processes a single diarization job claimed by a worker.
        """
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid.")
            return

        try:
            workspace_root = self._workspace_root()
            media_rel = job.config.get("media_asset_path") or job.media_path
            words_rel = job.config.get("words_path")
            reference_dir = job.config.get("reference_dir")
            output_rel = self._normalize_output_dir(job.config.get("output_dir"), job.ytid)

            if not media_rel or not words_rel or not reference_dir:
                raise ValueError("Job missing media, words, or reference configuration.")

            audio_path = self._stage_media(media_rel, workspace_root)
            words_path = self._stage_words(words_rel, workspace_root, job.job_id)
            staged_reference_dir = self._stage_reference(reference_dir, workspace_root)
            output_dir = workspace_root / output_rel
            output_dir.mkdir(parents=True, exist_ok=True)

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting diarization.")
            self._run_legacy_diarize(job, workspace_root, audio_path, words_path, output_dir)
            job_result = self._persist_outputs(job, output_rel, output_dir)
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Successfully diarized %s", job.ytid)
        except Exception as exc:
            error_msg = f"Diarization failed for job {job.job_id} ({job.ytid}): {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _workspace_root(self) -> Path:
        if os.environ.get("VIDOPS_PROJECT_ROOT"):
            return Path(os.environ["VIDOPS_PROJECT_ROOT"]).resolve()
        return Path(__file__).resolve().parents[2]

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
        base = output_dir or f"generated/diarization_resemblyzer/{ytid}"
        return self._normalize_relative(base, allow_dir=True)

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
        dest = workspace_root / "generated" / "diarization_inputs" / job_id
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / Path(words_rel).name
        if not target.exists():
            shutil.copy2(cached, target)
        return target

    def _stage_reference(self, reference_rel: str, workspace_root: Path) -> Path:
        src_dir = self.fs_cache.get_central_path(reference_rel)
        if not src_dir.exists():
            raise FileNotFoundError(f"Reference directory not found: {src_dir}")
        dest_dir = workspace_root / "generated" / "diary_reference" / src_dir.name
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        for item in sorted(src_dir.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(self.fs_cache.central_storage_root)
            cached = self.fs_cache.pull_to_cache(str(rel))
            target = dest_dir / item.relative_to(src_dir)
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

        workspace_sh = Path(__file__).resolve().parents[2] / "workspace.sh"
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

    def _persist_outputs(self, job: Job, output_rel: str, output_dir: Path) -> dict:
        timestamps = output_dir / "diarized_timestamps.tsv"
        speaker_words = output_dir / "speaker_words.tsv"
        meta_path = output_dir / "diarization.json"
        for path in (timestamps, speaker_words, meta_path):
            if not path.exists():
                raise FileNotFoundError(f"Missing diarization output: {path}")

        rel_base = Path(output_rel.strip("/"))
        ts_rel = str(rel_base / timestamps.name)
        sw_rel = str(rel_base / speaker_words.name)
        meta_rel = str(rel_base / meta_path.name)

        ts_asset = self.fs_cache.persist_local_artifact(timestamps, ts_rel, self.video_repo, job.ytid, "diarization")
        sw_asset = self.fs_cache.persist_local_artifact(
            speaker_words, sw_rel, self.video_repo, job.ytid, "diarization"
        )
        meta_asset = self.fs_cache.persist_local_artifact(meta_path, meta_rel, self.video_repo, job.ytid, "diarization")

        segments = max(0, len(timestamps.read_text(encoding="utf-8").splitlines()) - 1)
        word_rows = max(0, len(speaker_words.read_text(encoding="utf-8").splitlines()) - 1)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        return {
            "timestamps_path": ts_rel,
            "speaker_words_path": sw_rel,
            "metadata_path": meta_rel,
            "segments": segments,
            "word_rows": word_rows,
            "device": job.config.get("device", "auto"),
            "diarization_model": job.config.get("diarization_model"),
            "meta": meta,
        }
