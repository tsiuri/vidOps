# vidops/services/dates.py

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from vidops.dal import FilesystemCache, JobRepository, VideoRepository
from vidops.models import Job, JobStatus

logger = logging.getLogger(__name__)


class DatesService:
    """
    Bridge legacy `workspace.sh dates ...` helpers into the DB queue.
    Inputs (date lists) are rebuilt under PROJECT_ROOT/data, the legacy script
    runs, and any manifest output is pushed to storage as `dates_manifest`.
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
        action: str,
        dates_file: Optional[str] = None,
        source_dir: Optional[str] = None,
        archive_cache: Optional[str] = None,
        dest_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
        priority: int = 50,
    ) -> Job:
        if not action:
            raise ValueError("Action is required for dates jobs.")

        config = {
            "action": action,
            "source_dir": source_dir,
            "archive_cache": archive_cache,
            "dest_dir": dest_dir,
            "extra_args": list(extra_args or []),
        }
        if dates_file:
            path_obj = Path(dates_file)
            if not path_obj.exists():
                raise FileNotFoundError(f"Dates file not found: {dates_file}")
            config["dates_file"] = str(path_obj)
            config["dates_file_content"] = path_obj.read_text(encoding="utf-8")

        job = Job(
            job_type="dates",
            ytid=None,
            config=config,
            priority=priority,
            status=JobStatus.PENDING,
        )
        if output_name:
            output_rel = output_name
        elif action in {"find-missing", "create-list"}:
            output_rel = f"results/dates/{action}_{job.job_id}.txt"
        else:
            output_rel = None
        if output_rel:
            job.config["output_relative_path"] = output_rel
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        action = job.config.get("action")
        if not action:
            self.job_repo.update_status(
                job.job_id, JobStatus.FAILED, error_message="Dates job missing action."
            )
            return

        project_root = self._project_root()
        try:
            dates_file = self._rebuild_dates_file(project_root, job.config)
            source_dir = job.config.get("source_dir")
            archive_cache = job.config.get("archive_cache")
            dest_dir = job.config.get("dest_dir")
            output_relative = job.config.get("output_relative_path")
            output_local = self._local_output_path(project_root, output_relative, job.job_id)
            cmd = self._build_command(
                project_root,
                action=action,
                dates_file=dates_file,
                source_dir=source_dir,
                archive_cache=archive_cache,
                dest_dir=dest_dir,
                output_path=output_local,
                extra_args=job.config.get("extra_args") or [],
            )

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting dates helper.")
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

            expects_output = output_relative is not None

            if result.returncode != 0:
                job_result = {
                    "dates_manifest": None,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                }
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message=f"Legacy dates exited {result.returncode}",
                    result=job_result,
                )
                return

            if expects_output and (not output_local.exists() or output_local.stat().st_size == 0):
                job_result = {
                    "dates_manifest": None,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "error": "Legacy dates produced no output",
                }
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message="Legacy dates produced no output",
                    result=job_result,
                )
                return

            registered_path = None
            if expects_output and output_local.exists():
                registered_path = self.fs_cache.persist_local_artifact(
                    output_local,
                    output_relative,
                    video_repo=self.video_repo,
                    ytid=job.ytid or "dates",
                    kind="dates_manifest",
                )

            job_result = {
                "dates_manifest": output_relative if expects_output else None,
                "stored_path": str(Path(registered_path)) if registered_path else None,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Dates job %s completed (%s)", job.job_id, output_relative)

        except Exception as exc:
            error_msg = f"Dates job failed for {job.job_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_message=error_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _project_root(self) -> Path:
        return Path(os.environ.get("VIDOPS_PROJECT_ROOT") or Path(__file__).resolve().parents[2])

    def _rebuild_dates_file(self, project_root: Path, config: dict) -> Optional[Path]:
        content = config.get("dates_file_content")
        path_str = config.get("dates_file")
        if not path_str or content is None:
            return Path(path_str) if path_str else None

        target = project_root / path_str if not Path(path_str).is_absolute() else Path(path_str)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _resolve_path(self, project_root: Path, raw_path: Optional[str]) -> Optional[Path]:
        if not raw_path:
            return None
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return candidate

        project_candidate = project_root / candidate
        storage_candidate = self.fs_cache.get_central_path(str(candidate))
        if project_candidate.exists():
            return project_candidate
        if storage_candidate.exists():
            return storage_candidate
        return project_candidate

    def _local_output_path(self, project_root: Path, output_relative: Optional[str], job_id: str) -> Optional[Path]:
        if not output_relative:
            return None
        rel = Path(output_relative)
        target = rel if rel.is_absolute() else project_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _build_command(
        self,
        project_root: Path,
        action: str,
        dates_file: Optional[Path],
        source_dir: Optional[str],
        archive_cache: Optional[str],
        dest_dir: Optional[str],
        output_path: Optional[Path],
        extra_args: List[str],
    ) -> List[str]:
        workspace_sh = project_root / "workspace.sh"
        cmd = ["bash", str(workspace_sh), "dates", action]

        resolved_source = self._resolve_path(project_root, source_dir)
        resolved_archive = self._resolve_path(project_root, archive_cache)
        resolved_dest = self._resolve_path(project_root, dest_dir)

        if action == "find-missing":
            if not dates_file or not resolved_source:
                raise ValueError("find-missing requires dates_file and source_dir")
            cmd.extend([str(dates_file), str(resolved_source)])
            if output_path:
                cmd.extend(["--output", str(output_path)])
        elif action == "create-list":
            if not dates_file or not resolved_archive:
                raise ValueError("create-list requires dates_file and archive_cache")
            cmd.extend([str(dates_file), str(resolved_archive)])
            if output_path:
                cmd.extend(["--output", str(output_path)])
        elif action == "move":
            if not dates_file or not resolved_source:
                raise ValueError("move requires dates_file and source_dir")
            cmd.extend([str(dates_file), str(resolved_source)])
            if resolved_dest:
                cmd.extend(["--dest-dir", str(resolved_dest)])
        else:
            if dates_file:
                cmd.append(str(dates_file))
            if resolved_source:
                cmd.append(str(resolved_source))
            if resolved_archive:
                cmd.append(str(resolved_archive))
            if resolved_dest:
                cmd.append(str(resolved_dest))

        cmd.extend(extra_args or [])
        return cmd

    def _legacy_env(self, project_root: Path) -> dict:
        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(project_root)
        return env
