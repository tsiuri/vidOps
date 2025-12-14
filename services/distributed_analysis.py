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
        capabilities: Optional[List[str]] = None,
        machine_alias: Optional[str] = None,
    ):
        self.job_repo = job_repo
        self.db_host = db_host
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.model_url = model_url
        self.model_name = model_name
        self.capabilities = capabilities or []
        self.machine_alias = machine_alias

    def _build_capabilities(self, model_name: str) -> List[str]:
        caps = list(self.capabilities)
        if not caps:
            caps = [model_name, "gpu_8gb"]
        elif model_name not in caps:
            caps.append(model_name)
        return caps

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

            model_url = job.config.get("model_url") or self.model_url
            model_name = job.config.get("model_name") or self.model_name
            capabilities = self._build_capabilities(model_name)
            machine_alias = self.machine_alias or job.claimed_by or self.db_host

            worker = AnalysisWorker(
                machine_alias=machine_alias,
                worker_type="analysis_bridge",
                model_url=model_url,
                model_name=model_name,
                capabilities=capabilities,
                db_host=self.db_host,
                db_name=self.db_name,
                db_user=self.db_user,
                db_password=self.db_password,
                lease_duration_minutes=60,
                metrics_port=0,  # disable metrics server for the bridge path
            )

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING)

            success = worker.process_analysis_job(
                analysis_job_id=analysis_job_id,
                force_job_level_passes=True,
            )

            result = {
                "analysis_job_id": analysis_job_id,
                "config_id": config_id,
                "status": "completed" if success else "incomplete",
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

    def _resolve_transcript_path(self, path: Path, cfg) -> Path:
        if path.is_absolute():
            return path
        prefix = cfg.paths.path_prefix or cfg.paths.central_storage_root
        base = Path(prefix) if prefix else Path.cwd()
        return (base / path).resolve()

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
