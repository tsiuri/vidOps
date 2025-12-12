# vidops/services/analysis.py

import json
import logging
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

from dal import VideoRepository, JobRepository, TranscriptRepository, WordRepository, FilesystemCache
from models import Job, JobStatus, Word

logger = logging.getLogger(__name__)


class AnalysisService:
    """
    Bridge DB-backed analysis jobs into the legacy workspace.sh analyze command.
    Workers rebuild transcript inputs in legacy locations, invoke the legacy script,
    and register the resulting artifacts back into storage with rel_path set.
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

    def enqueue_analysis_job(
        self,
        ytid: str,
        transcript_kind: str,
        analysis_model: str,
        priority: int = 50,
        output_name: Optional[str] = None,
    ) -> Job:
        transcript = self.transcript_repo.get(ytid, transcript_kind)
        if not transcript:
            raise ValueError(f"Transcript of kind '{transcript_kind}' not found for video '{ytid}'.")

        config = {
            "ytid": ytid,
            "transcript_kind": transcript_kind,
            "analysis_model": analysis_model,
            "transcript_path": transcript.path,
        }

        job = Job(
            job_type="analysis",
            ytid=ytid,
            config=config,
            priority=priority,
            status=JobStatus.PENDING,
        )
        job.config["output_relative_path"] = str(
            Path(output_name)
            if output_name
            else Path("analysis") / ytid / f"{job.job_id}_{analysis_model}.json"
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job missing ytid.")
            return

        try:
            transcript_kind = job.config.get("transcript_kind")
            analysis_model = job.config.get("analysis_model", "llama3")
            if not transcript_kind:
                raise ValueError("Analysis job missing transcript_kind in config.")

            transcript = self.transcript_repo.get(job.ytid, transcript_kind)
            if not transcript or not (transcript.path or job.config.get("transcript_path")):
                raise FileNotFoundError(f"Transcript metadata missing for {job.ytid}:{transcript_kind}")

            project_root = Path(
            os.environ.get("VIDOPS_PROJECT_ROOT") or Path(__file__).resolve().parents[1]
            )
            workspace_sh = project_root / "workspace.sh"
            transcript_path = Path(job.config.get("transcript_path") or transcript.path)
            staged_transcript = self._materialize_transcript(project_root, transcript_path)
            output_relative = job.config.get("output_relative_path") or str(
                Path("analysis") / job.ytid / f"{job.job_id}_{analysis_model}.json"
            )
            output_local = self._local_output_path(project_root, output_relative)

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting analysis.")
            cmd = [
                "bash",
                str(workspace_sh),
                "analyze",
                "--transcript",
                str(staged_transcript),
                "--model",
                analysis_model,
                "--output",
                str(output_local),
                "--ytid",
                job.ytid,
            ]
            logger.info("Running legacy analyze command: %s", " ".join(cmd))
            result = subprocess.run(
                cmd,
                cwd=project_root,
                env=self._legacy_env(project_root),
                capture_output=True,
                text=True,
                check=False,
            )

            stdout_tail = (result.stdout or "").strip()[-500:]
            stderr_tail = (result.stderr or "").strip()[-500:]
            if result.returncode != 0:
                job_result = {
                    "analysis_path": None,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "output_expected": output_relative,
                }
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message=f"Legacy analyze exited {result.returncode}",
                    result=job_result,
                )
                return

            if not output_local.exists() or output_local.stat().st_size == 0:
                job_result = {
                    "analysis_path": None,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "error": "Legacy analyze produced no output",
                }
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message="Legacy analyze produced no output",
                    result=job_result,
                )
                return

            stored_path = self.fs_cache.persist_local_artifact(
                output_local,
                output_relative,
                video_repo=self.video_repo,
                ytid=job.ytid,
                kind="analysis",
            )

            preview, top_terms = self._job_preview(job.ytid, transcript_kind, staged_transcript, output_local)
            job_result = {
                "analysis_path": str(Path(output_relative)),
                "stored_path": str(Path(stored_path)),
                "model": analysis_model,
                "transcript_kind": transcript_kind,
                "summary_preview": preview,
                "top_terms": top_terms[:5],
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Successfully analyzed %s -> %s", job.ytid, output_relative)

        except Exception as exc:
            error_msg = f"Analysis failed for job {job.job_id} ({job.ytid}): {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    def _legacy_env(self, project_root: Path) -> dict:
        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(project_root)
        return env

    def _materialize_transcript(self, project_root: Path, transcript_path: Path) -> Path:
        relative = str(transcript_path)
        if transcript_path.is_absolute():
            source = transcript_path
        else:
            try:
                source = self.fs_cache.pull_to_cache(relative)
            except FileNotFoundError:
                source = self.fs_cache.get_central_path(relative)

        if not source.exists():
            raise FileNotFoundError(f"Transcript not found at {source}")

        target_dir = project_root / "generated"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / transcript_path.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target

    def _local_output_path(self, project_root: Path, relative_output: str) -> Path:
        relative_path = Path(relative_output)
        if relative_path.is_absolute():
            return relative_path
        target = project_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _job_preview(
        self,
        ytid: str,
        transcript_kind: str,
        staged_transcript: Path,
        output_local: Path,
    ) -> Tuple[str, List[str]]:
        preview = ""
        top_terms: List[str] = []
        try:
            if output_local.suffix.lower() == ".json":
                payload = json.loads(output_local.read_text(encoding="utf-8"))
                preview = (payload.get("summary") or payload.get("content") or "")[:200]
                top_terms = payload.get("top_terms") or []
        except Exception:
            preview = staged_transcript.read_text(encoding="utf-8")[:200]

        word_source = self._derive_word_source(transcript_kind)
        if word_source:
            try:
                words = self.word_repo.fetch_for_source(ytid, word_source, limit=500)
                if words:
                    top_terms = top_terms or self._top_terms(words)
            except Exception:
                pass
        return preview, top_terms

    def _derive_word_source(self, transcript_kind: str) -> Optional[str]:
        if transcript_kind.startswith("words_whisper_"):
            suffix = transcript_kind.replace("words_whisper_", "")
            return f"whisper-{suffix.replace('_', '-')}"
        if transcript_kind.startswith("vtt_whisper_"):
            suffix = transcript_kind.replace("vtt_whisper_", "")
            return f"whisper-{suffix.replace('_', '-')}"
        return None

    def _top_terms(self, words: List[Word]) -> List[str]:
        stop_words = {"the", "and", "for", "that", "with", "this", "have", "from", "your", "just", "you", "but"}
        counter: Counter[str] = Counter()
        for word in words:
            token = (word.word or "").strip().lower()
            if len(token) < 3 or token in stop_words:
                continue
            counter[token] += 1
        return [term for term, _ in counter.most_common(10)]
