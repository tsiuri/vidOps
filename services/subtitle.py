# vidops/services/subtitle.py

import csv
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from configuration import load_config
from dal import (
    VideoRepository,
    JobRepository,
    TranscriptRepository,
    WordRepository,
    FilesystemCache,
)
from models import Job, JobStatus, Transcript, Word

logger = logging.getLogger(__name__)

DEFAULT_LANG = "en"


class SubtitleService:
    """
    Bridge legacy dl-subs and convert-captions into the DB queue and storage manager.
    """

    def __init__(
        self,
        video_repo: VideoRepository,
        job_repo: JobRepository,
        transcript_repo: TranscriptRepository,
        word_repo: WordRepository,
        fs_cache: FilesystemCache,
    ):
        self.video_repo = video_repo
        self.job_repo = job_repo
        self.transcript_repo = transcript_repo
        self.word_repo = word_repo
        self.fs_cache = fs_cache
        self.config = load_config()

    # ------------------------------------------------------------------
    # Enqueue helpers
    # ------------------------------------------------------------------
    def enqueue_dl_subs(
        self,
        ytid: str,
        lang: str = DEFAULT_LANG,
        subtitle_format: str = "vtt",
        priority: int = 50,
    ) -> Job:
        """
        Enqueue a single legacy dl-subs job for a video.
        """
        video = self._require_video(ytid)
        config = {
            "ytid": ytid,
            "url": video.url,
            "lang": lang or DEFAULT_LANG,
            "format": subtitle_format,
        }
        job = Job(
            job_type="dl_subs",
            ytid=ytid,
            media_path=video.url,
            config=config,
            priority=priority,
            status=JobStatus.PENDING,
        )
        return self.job_repo.create(job)

    def enqueue_dl_subs_from_list(
        self,
        list_path: Path,
        lang: str = DEFAULT_LANG,
        subtitle_format: str = "vtt",
        priority: int = 50,
    ) -> List[Job]:
        """
        Enqueue dl-subs jobs for each ytid in a file (one per line).
        """
        jobs: List[Job] = []
        with Path(list_path).open("r", encoding="utf-8") as handle:
            for raw in handle:
                ytid = raw.strip()
                if not ytid or ytid.startswith("#"):
                    continue
                try:
                    jobs.append(
                        self.enqueue_dl_subs(
                            ytid=ytid,
                            lang=lang,
                            subtitle_format=subtitle_format,
                            priority=priority,
                        )
                    )
                except Exception as exc:  # pragma: no cover - operator feedback
                    logger.warning("Skipping %s from %s: %s", ytid, list_path, exc)
        return jobs

    def enqueue_convert_captions(
        self,
        ytid: str,
        subtitle_asset_path: Optional[str] = None,
        overwrite: bool = False,
        subtitle_format: str = "vtt",
        priority: int = 50,
    ) -> Job:
        """
        Enqueue a job that converts captions into *.words.yt.tsv via the legacy converter.
        """
        video = self._require_video(ytid)
        config = {
            "ytid": ytid,
            "subtitle_asset_path": subtitle_asset_path,
            "overwrite": overwrite,
            "format": subtitle_format,
            "url": video.url,
        }
        job = Job(
            job_type="convert_captions",
            ytid=ytid,
            media_path=subtitle_asset_path,
            config=config,
            priority=priority,
            status=JobStatus.PENDING,
        )
        return self.job_repo.create(job)

    # ------------------------------------------------------------------
    # Worker entrypoint
    # ------------------------------------------------------------------
    def process_job(self, job: Job) -> None:
        """
        Dispatch to the appropriate legacy bridge based on job_type.
        """
        try:
            if job.job_type == "dl_subs":
                self._process_dl_subs(job)
            elif job.job_type == "convert_captions":
                self._process_convert_captions(job)
            else:
                raise ValueError(f"Unsupported subtitle job_type: {job.job_type}")
        except Exception as exc:
            error_msg = f"Subtitle pipeline failed for job {job.job_id} ({job.ytid}): {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    # ------------------------------------------------------------------
    # dl-subs bridge
    # ------------------------------------------------------------------
    def _process_dl_subs(self, job: Job) -> None:
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid.")
            return

        video = self._require_video(job.ytid)
        url = job.config.get("url") or video.url
        lang = job.config.get("lang") or DEFAULT_LANG
        subtitle_format = job.config.get("format", "vtt")

        workspace_root = self._workspace_root()
        pull_dir = workspace_root / "pull"
        pull_dir.mkdir(parents=True, exist_ok=True)

        url_list = pull_dir / f"{job.job_id}_urls.txt"
        url_list.write_text(f"{url}\n", encoding="utf-8")

        cmd = [
            "bash",
            str(self._workspace_sh()),
            "dl-subs",
            "batch",
            str(url_list),
            subtitle_format,
        ]

        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(workspace_root)

        self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Downloading subtitles via legacy dl-subs.")
        logger.info("Running legacy dl-subs for %s: %s", job.ytid, " ".join(cmd))
        start_ts = time.time()
        result = subprocess.run(cmd, cwd=workspace_root, env=env, capture_output=False, text=True, check=False)
        if result.returncode != 0:
            logger.warning("dl-subs exited %s; will verify outputs anyway", result.returncode)

        subtitle_path = self._find_subtitle_output(pull_dir, job.ytid, subtitle_format)
        relative_path = subtitle_path.relative_to(workspace_root)
        self.fs_cache.persist_local_artifact(
            subtitle_path,
            str(relative_path),
            video_repo=self.video_repo,
            ytid=job.ytid,
            kind="vtt",
        )

        transcript = Transcript(
            ytid=job.ytid,
            kind="vtt",
            lang=lang,
            path=str(relative_path),
        )
        self.transcript_repo.upsert(transcript)

        duration = time.time() - start_ts
        self.job_repo.update_status(
            job.job_id,
            JobStatus.COMPLETED,
            result={
                "subtitle_path": str(relative_path),
                "format": subtitle_format,
                "lang": lang,
                "duration_seconds": round(duration, 3),
            },
        )
        logger.info("Subtitle download complete for %s → %s", job.ytid, relative_path)

    def _find_subtitle_output(self, pull_dir: Path, ytid: str, subtitle_format: str) -> Path:
        """Locate the newest subtitle artifact for the given ytid."""
        patterns = [
            f"{ytid}__*.transcript.*.{subtitle_format}",
            f"{ytid}__*.{subtitle_format}",
            f"{ytid}*.transcript.*.{subtitle_format}",
        ]
        candidates: List[Path] = []
        for pattern in patterns:
            candidates.extend(pull_dir.glob(pattern))
        if not candidates:
            raise FileNotFoundError(f"No subtitles found for {ytid} in {pull_dir}")
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    # ------------------------------------------------------------------
    # convert-captions bridge
    # ------------------------------------------------------------------
    def _process_convert_captions(self, job: Job) -> None:
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid.")
            return

        workspace_root = self._workspace_root()
        pull_dir = workspace_root / "pull"
        generated_dir = workspace_root / "generated"
        pull_dir.mkdir(parents=True, exist_ok=True)
        generated_dir.mkdir(parents=True, exist_ok=True)

        subtitle_path = self._resolve_subtitle(job)
        legacy_vtt = pull_dir / subtitle_path.name
        if not legacy_vtt.exists():
            shutil.copy2(subtitle_path, legacy_vtt)

        overwrite = bool(job.config.get("overwrite"))
        subtitle_format = job.config.get("format", "vtt")

        cmd = ["bash", str(self._workspace_sh()), "convert-captions", str(legacy_vtt)]
        if overwrite:
            cmd.insert(3, "--overwrite")

        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(workspace_root)

        self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Running legacy convert-captions.")
        logger.info("Running convert-captions for %s: %s", job.ytid, " ".join(cmd))
        start_ts = time.time()
        result = subprocess.run(cmd, cwd=workspace_root, env=env, capture_output=False, text=True, check=False)
        if result.returncode != 0:
            logger.warning("convert-captions exited %s; will attempt ingestion", result.returncode)

        words_path = self._locate_words_output(generated_dir, legacy_vtt)
        relative_words = words_path.relative_to(workspace_root)
        stored_words = self.fs_cache.persist_local_artifact(
            words_path,
            str(relative_words),
            video_repo=self.video_repo,
            ytid=job.ytid,
            kind="words_ytt",
        )

        vtt_relative = legacy_vtt.relative_to(workspace_root)
        # Ensure subtitle asset exists in central storage as well
        self.fs_cache.persist_local_artifact(
            legacy_vtt,
            str(vtt_relative),
            video_repo=self.video_repo,
            ytid=job.ytid,
            kind="vtt",
        )

        words, segment_count = self._parse_words(words_path, job.ytid)
        if words:
            self.word_repo.bulk_insert(words, job_id=job.job_id)

        lang = job.config.get("lang") or DEFAULT_LANG

        subtitle_transcript = Transcript(
            ytid=job.ytid,
            kind="vtt",
            lang=lang,
            path=str(vtt_relative),
        )
        words_transcript = Transcript(
            ytid=job.ytid,
            kind="words_ytt",
            lang=lang,
            path=str(relative_words),
            word_count=len(words),
            segment_count=segment_count,
        )
        self.transcript_repo.upsert(subtitle_transcript)
        self.transcript_repo.upsert(words_transcript)

        duration = time.time() - start_ts
        self.job_repo.update_status(
            job.job_id,
            JobStatus.COMPLETED,
            result={
                "words_path": str(relative_words),
                "subtitle_path": str(vtt_relative),
                "word_count": len(words),
                "segment_count": segment_count,
                "duration_seconds": round(duration, 3),
            },
        )
        logger.info("convert-captions complete for %s → %s", job.ytid, relative_words)

    def _locate_words_output(self, generated_dir: Path, vtt_path: Path) -> Path:
        """Given a VTT path, locate the associated *.words.yt.tsv output."""
        base = vtt_path.name
        if base.endswith(".transcript.en.vtt"):
            base = base[: -len(".transcript.en.vtt")]
        elif base.endswith(".vtt"):
            base = base[: -len(".vtt")]
        elif base.endswith(".srt"):
            base = base[: -len(".srt")]
        candidate = generated_dir / f"{base}.words.yt.tsv"
        if candidate.exists():
            return candidate
        matches = list(generated_dir.glob(f"{base}*.words.yt.tsv"))
        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return matches[0]
        raise FileNotFoundError(f"No words.yt.tsv found for {vtt_path}")

    def _parse_words(self, words_path: Path, ytid: str) -> Tuple[List[Word], int]:
        """Parse a words.yt.tsv into Word objects and return (words, segment_count)."""
        if not words_path.exists():
            raise FileNotFoundError(words_path)
        words: List[Word] = []
        max_seg = 0
        with words_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            idx = 0
            for row in reader:
                token = (row.get("word") or "").strip()
                if not token:
                    continue
                try:
                    start = float(row.get("start") or row.get("start_sec") or 0.0)
                except Exception:
                    start = 0.0
                try:
                    end = float(row.get("end") or row.get("end_sec") or start)
                except Exception:
                    end = start
                try:
                    seg = int(row.get("seg") or row.get("segment") or row.get("segment_id") or 0)
                except Exception:
                    seg = 0
                try:
                    conf = float(row.get("confidence") or 0.0)
                except Exception:
                    conf = 0.0
                words.append(
                    Word(
                        ytid=ytid,
                        source="yt",
                        word=token,
                        start_sec=start,
                        end_sec=end,
                        confidence=conf,
                        idx=idx,
                        segment_id=seg,
                    )
                )
                idx += 1
                if seg > max_seg:
                    max_seg = seg
        return words, (max_seg + 1 if words else 0)

    def _resolve_subtitle(self, job: Job) -> Path:
        """
        Resolve the local path to the subtitle asset for the given job.
        Pulls from storage if necessary.
        """
        candidate = job.config.get("subtitle_asset_path")
        if candidate:
            try:
                return Path(self.fs_cache.pull_to_cache(candidate))
            except FileNotFoundError:
                logger.warning("subtitle_asset_path %s missing in storage, will search workspace", candidate)

        asset = self.video_repo.get_primary_asset(job.ytid, "subtitle")
        if asset:
            return Path(self.fs_cache.pull_to_cache(asset.path))

        # Fallback: search local workspace pull/
        pull_dir = self._workspace_root() / "pull"
        matches = list(pull_dir.glob(f"{job.ytid}__*.transcript*.vtt"))
        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return matches[0]

        raise FileNotFoundError(f"No subtitle asset available for {job.ytid}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_video(self, ytid: str):
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")
        return video

    def _workspace_root(self) -> Path:
        return (
            Path(os.environ.get("VIDOPS_PROJECT_ROOT", "")).resolve()
            if os.environ.get("VIDOPS_PROJECT_ROOT")
            else Path(__file__).resolve().parents[1]
        )

    def _workspace_sh(self) -> Path:
        return Path(__file__).resolve().parents[1] / "workspace.sh"
