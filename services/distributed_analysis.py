# vidops/services/distributed_analysis.py

"""
Distributed analysis service for GenericWorker integration.

This is now a thin bridge that delegates to the full-featured AnalysisWorker
implementation so the dedicated worker CLI and the GenericWorker path share
one code path (DB store, spans, drills, etc.).
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from pathlib import Path

from models import Job, JobStatus
from workers.analysis_distributed import AnalysisWorker
from configuration import load_config
from dal import TranscriptRepository
from utils.path_utils import resolve_db_path
from scripts.analysis.analyze_to_db import create_analysis_job, export_vtt_from_db
from scripts.analysis.analyze_transcript import TranscriptChunker, VTTParser
from scripts.analysis.analysis_config import AnalysisConfig
from db import get_connection

logger = logging.getLogger(__name__)


class DistributedAnalysisService:
    """
    Bridge between GenericWorker (jobs table) and the distributed analysis worker.

    When GenericWorker claims a job with job_type="analysis-distributed" (or the
    legacy "analysis" alias), we hydrate an AnalysisWorker instance and run the
    job's analysis_tasks through the same pass implementations used by the
    dedicated distributed worker.
    """

    def __init__(
        self,
        job_repo=None,
        db_host: str = "localhost",
        db_name: str = "transcripts",
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
        model_url: str = "http://localhost:11434",
        model_name: str = "llama3",
        available_vram_gb: float = 0.0,
        model_profile_id: Optional[int] = None,
        machine_alias: Optional[str] = None,
    ):
        self.job_repo = job_repo
        self.db_host = db_host
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.model_url = model_url
        self.model_name = model_name
        self.available_vram_gb = max(float(available_vram_gb or 0), 0.0)
        self.model_profile_id = model_profile_id
        self.machine_alias = machine_alias

    def process_job(self, job: Job) -> None:
        """
        Main entry point for GenericWorker.
        Processes a distributed analysis job using the shared AnalysisWorker.
        """
        worker: Optional[AnalysisWorker] = None
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job missing ytid.")
            return

        try:
            analysis_job_id = job.config.get("analysis_job_id")
            config_id = job.config.get("config_id")
            if not config_id:
                raise ValueError(f"Job {job.job_id} missing 'config_id' in config")

            if not analysis_job_id:
                analysis_job_id = self._ensure_analysis_job(job, config_id)
                job.config["analysis_job_id"] = analysis_job_id
                try:
                    self.job_repo.update_config(job.job_id, job.config)
                except Exception:
                    logger.warning("Failed to persist analysis_job_id for %s", job.job_id, exc_info=True)

            # Worker's settings take precedence over job config (for multi-GPU setups)
            # This allows workers with --gpu flags to override job defaults
            model_url = self.model_url or job.config.get("model_url")
            model_name = self.model_name or job.config.get("model_name")
            model_profile_id = self.model_profile_id or job.config.get("model_profile_id")
            machine_alias = self.machine_alias or job.claimed_by or self.db_host
            # VRAM filtering now happens at claim time via jobs.config->>'required_vram_gb'
            # No need to check again here - if we claimed it, we have enough VRAM
            task_profile_id = self._get_model_profile_for_job(analysis_job_id)

            worker = AnalysisWorker(
                machine_alias=machine_alias,
                worker_type="analysis_bridge",
                model_url=model_url,
                model_name=model_name,
                available_vram_gb=self.available_vram_gb,
                model_profile_id=model_profile_id or task_profile_id,
                db_host=self.db_host,
                db_name=self.db_name,
                db_user=self.db_user,
                db_password=self.db_password,
                lease_duration_minutes=60,
                metrics_port=0,  # disable metrics server for the bridge path
            )

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING)

            job_result = worker.engine.process_job(
                analysis_job_id,
                worker_id=worker.worker_id,
                force_job_level=True,
            )
            success = job_result.aggregate_status in ("ok", "partial")

            result = {
                "analysis_job_id": analysis_job_id,
                "config_id": config_id,
                "status": "completed" if success else "incomplete",
                "aggregate_status": job_result.aggregate_status,
                "tasks_total": job_result.tasks_total,
                "tasks_ok": job_result.tasks_ok,
                "tasks_failed": job_result.tasks_failed,
                "duration_s": job_result.duration_s,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            if success:
                self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=result)
                logger.info(
                    "Distributed analysis job %s (analysis_job_id=%s) completed",
                    job.job_id,
                    analysis_job_id,
                )
            else:
                self.job_repo.update_status(
                    job.job_id, JobStatus.FAILED, "Analysis tasks did not complete"
                )
                logger.error(
                    "Distributed analysis job %s (analysis_job_id=%s) ended incomplete",
                    job.job_id,
                    analysis_job_id,
                )

        except Exception as exc:
            error_msg = f"Distributed analysis failed for job {job.job_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

        finally:
            try:
                if worker:
                    worker.shutdown()
            except Exception:
                pass

    def _ensure_analysis_job(self, job: Job, config_id: str) -> str:
        """
        Build analysis_tasks entry on the fly if it wasn't created at enqueue time.
        """
        cfg = load_config()
        db = None
        transcript_repo = TranscriptRepository()

        transcript_kind = job.config.get("transcript_kind") or f"words_whisper_{cfg.transcription.model}"
        transcript = transcript_repo.get(job.ytid, transcript_kind) or transcript_repo.get_best_available(job.ytid)
        if not transcript or not transcript.path:
            raise ValueError(f"No transcript available for {job.ytid} (kind={transcript_kind})")

        transcript_path = self._resolve_transcript_path(Path(transcript.path), cfg)
        if not transcript_path.exists():
            transcript_path = self._rebuild_transcript_from_db(job.ytid, cfg)
            if not transcript_path or not transcript_path.exists():
                raise FileNotFoundError(f"Transcript file missing and rebuild failed for {job.ytid}")

        text = self._load_transcript_text(transcript_path)
        if not text.strip():
            raise ValueError(f"Transcript at {transcript_path} is empty")

        chunker = TranscriptChunker(
            chunk_size=cfg.analysis.chunk_size_words,
            overlap=cfg.analysis.chunk_overlap_words,
        )
        raw_chunks = chunker.chunk(text)
        if not raw_chunks:
            raise ValueError("Transcript chunking produced no chunks")

        chunk_payload = []
        for idx, chunk in enumerate(raw_chunks):
            payload = {
                "chunk_id": chunk.get("chunk_id", idx),
                "text": chunk.get("text", ""),
                "word_count": chunk.get("word_count"),
                "start_sec": chunk.get("start_sec"),
                "end_sec": chunk.get("end_sec"),
                "speaker": chunk.get("speaker"),
            }
            if transcript_path.suffix.lower() == ".vtt":
                payload["vtt_path"] = str(transcript_path)
            chunk_payload.append(payload)

        db = self._connect_analysis_db()
        try:
            config_row = db.get_analysis_config(config_id)
            if not config_row:
                raise ValueError(f"Config id '{config_id}' not found in analysis_configs")
            config_obj = AnalysisConfig.model_validate(config_row["config_json"])
            analysis_job_id = create_analysis_job(
                ytid=job.ytid,
                config_id=config_id,
                config=config_obj,
                chunks=chunk_payload,
                db=db,
                model_name=job.config.get("model_name") or self.model_name,
                model_profile_id=job.config.get("model_profile_id") or getattr(config_obj, "model_profile_id", None),
            )
        finally:
            if db:
                db.disconnect()
        return analysis_job_id

    def _connect_analysis_db(self):
        from dal.analysis_task_repository import AnalysisDatabase

        db = AnalysisDatabase(
            host=self.db_host,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password,
        )
        db.connect()
        return db

    def _get_model_profile_for_job(self, analysis_job_id: str) -> Optional[int]:
        db = self._connect_analysis_db()
        try:
            cur = db.cursor
            cur.execute(
                "SELECT DISTINCT model_profile_id FROM analysis_tasks WHERE job_id = %s",
                (analysis_job_id,),
            )
            rows = [r[0] for r in cur.fetchall() if r and r[0] is not None]
            if not rows:
                return None
            return int(rows[0])
        except Exception:
            return None
        finally:
            try:
                db.disconnect()
            except Exception:
                pass

    def _resolve_transcript_path(self, path: Path, cfg) -> Path:
        return resolve_db_path(str(path), cfg.paths).resolve()

    def _load_transcript_text(self, transcript_path: Path) -> str:
        if transcript_path.suffix.lower() in {".vtt", ".srt"}:
            parser = VTTParser()
            return parser.parse(transcript_path)
        return transcript_path.read_text(encoding="utf-8", errors="ignore")

    def _rebuild_transcript_from_db(self, ytid: str, cfg) -> Optional[Path]:
        """
        Rebuild a VTT from the words table when the stored file is missing.
        """
        try:
            base = Path(cfg.paths.local_temp_dir or Path.cwd() / "tmp")
            target_dir = base if base.is_absolute() else Path.cwd() / base
            target_dir.mkdir(parents=True, exist_ok=True)
            with get_connection() as conn:
                return export_vtt_from_db(conn, ytid, target_dir)
        except Exception as exc:
            logger.error("Failed to rebuild transcript for %s: %s", ytid, exc, exc_info=True)
            return None
