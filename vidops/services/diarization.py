import json
import logging
import os
import shutil
import subprocess
import csv
import sys
from pathlib import Path
from typing import List, Optional

from vidops.dal import FilesystemCache, JobRepository, TranscriptRepository, VideoRepository
from vidops.models import Job, JobStatus
from vidops.services.reference_builder import ReferenceBuilder

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
        reference_name: Optional[str] = None,
        match_threshold: float = 0.75,
        match_margin: float = 0.01,
        match_force_best: bool = True,
        words_path: Optional[str] = None,
        device: str = "auto",
        chunk_seconds: float = 15.0,
        overlap_seconds: float = 2.5,
        similarity_threshold: float = 0.6,
        gap_threshold: float = 0.15,
        output_dir: Optional[str] = None,
        build_reference: bool = False,
    ) -> Job:
        """
        Enqueues a single diarization job.

        If transcript_kind is "best", automatically selects the highest quality
        transcript available for this ytid.
        """
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
            self._run_pyannote_diarize(job, workspace_root, audio_path, words_path, output_dir, staged_reference_dir)
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

        tool_root = Path(
            os.environ.get("TOOL_ROOT", "/home/billie/projects/vidops")
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

        cmd = [
            str(py_bin),
            str(batch_script),
            str(ytids_file),
            str(results_root),
            "--config",
            str(config_path),
            "--device",
            str(job.config.get("device", "auto")),
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

        logger.info("Running pyannote diarize: %s", " ".join(cmd))
        result = subprocess.run(cmd, cwd=workspace_root, env=env, text=True, capture_output=False, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"pyannote diarization failed with exit {result.returncode}")

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

    def _write_spans_to_db(self, ytid: str, timestamps_path: Path, replace_existing: bool = True) -> int:
        from psycopg2.extras import execute_values  # type: ignore
        from vidops.db import get_connection

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
