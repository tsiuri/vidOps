# vidops/services/extra_utils.py

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from dal import FilesystemCache, JobRepository, VideoRepository
from models import Job, JobStatus

logger = logging.getLogger(__name__)


class ExtraUtilsService:
    """
    Bridge queued `workspace.sh extra-utils ...` invocations.
    Input manifests are rebuilt under PROJECT_ROOT, the legacy tool is executed,
    and any declared output is registered as `utility_output`.
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
        tool: str,
        inputs: Optional[List[str]] = None,
        output_name: Optional[str] = None,
        args: Optional[List[str]] = None,
        priority: int = 50,
        ytid: Optional[str] = None,
    ) -> Job:
        if not tool:
            raise ValueError("Tool is required for extra-utils jobs.")

        input_payload: List[dict] = []
        for path_str in inputs or []:
            path_obj = Path(path_str)
            if path_obj.is_absolute():
                raise ValueError(f"Input path must be relative to workspace: {path_str}")
            if not path_obj.exists():
                raise FileNotFoundError(f"Input file not found: {path_str}")
            input_payload.append(
                {
                    "path": str(path_obj),
                    "content": path_obj.read_text(encoding="utf-8", errors="ignore"),
                }
            )

        job = Job(
            job_type="extra_utils",
            ytid=ytid,
            config={
                "tool": tool,
                "inputs": input_payload,
                "args": list(args or []),
            },
            priority=priority,
            status=JobStatus.PENDING,
        )
        if output_name:
            job.config["output_relative_path"] = output_name
        else:
            job.config["output_relative_path"] = f"results/extra_utils/{tool}_{job.job_id}.out"
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        tool = job.config.get("tool")
        if not tool:
            self.job_repo.update_status(
                job.job_id, JobStatus.FAILED, error_message="Extra-utils job missing tool."
            )
            return

        project_root = self._project_root()
        try:
            materialized_inputs = self._materialize_inputs(project_root, job.config.get("inputs") or [])
            output_relative = job.config.get("output_relative_path")
            output_local = self._output_path(project_root, output_relative)

            cmd = self._build_command(
                project_root,
                tool=tool,
                args=job.config.get("args") or [],
                output_path=output_local,
                output_declared=bool(output_relative),
            )

            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting extra-utils tool.")
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
                    "utility_output": None,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "materialized_inputs": materialized_inputs,
                }
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message=f"Extra-utils exited {result.returncode}",
                    result=job_result,
                )
                return

            if output_relative:
                if not output_local.exists() or output_local.stat().st_size == 0:
                    job_result = {
                        "utility_output": None,
                        "stdout_tail": stdout_tail,
                        "stderr_tail": stderr_tail,
                        "materialized_inputs": materialized_inputs,
                        "error": "Legacy tool produced no output",
                    }
                    self.job_repo.update_status(
                        job.job_id,
                        JobStatus.FAILED,
                        error_message="Legacy tool produced no output",
                        result=job_result,
                    )
                    return

                stored_path = self.fs_cache.persist_local_artifact(
                    output_local,
                    output_relative,
                    video_repo=self.video_repo,
                    ytid=job.ytid or "utility",
                    kind="utility_output",
                )
                job_result = {
                    "utility_output": output_relative,
                    "stored_path": str(Path(stored_path)),
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "materialized_inputs": materialized_inputs,
                }
            else:
                job_result = {
                    "utility_output": None,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "materialized_inputs": materialized_inputs,
                }

            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Extra-utils job %s completed for %s", job.job_id, tool)

        except Exception as exc:
            error_msg = f"Extra-utils job failed for {job.job_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_message=error_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _project_root(self) -> Path:
        return Path(os.environ.get("VIDOPS_PROJECT_ROOT") or Path(__file__).resolve().parents[1])

    def _materialize_inputs(self, project_root: Path, inputs: List[dict]) -> List[str]:
        materialized: List[str] = []
        for entry in inputs:
            rel_path = Path(entry.get("path", ""))
            if not rel_path:
                continue
            target = rel_path if rel_path.is_absolute() else project_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry.get("content", ""), encoding="utf-8")
            materialized.append(str(target))
        return materialized

    def _output_path(self, project_root: Path, output_relative: Optional[str]) -> Optional[Path]:
        if not output_relative:
            return None
        rel = Path(output_relative)
        target = rel if rel.is_absolute() else project_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _build_command(
        self,
        project_root: Path,
        tool: str,
        args: List[str],
        output_path: Optional[Path],
        output_declared: bool,
    ) -> List[str]:
        workspace_sh = project_root / "workspace.sh"
        cmd = ["bash", str(workspace_sh), "extra-utils", tool]
        cmd.extend(args or [])
        if output_declared and output_path and str(output_path) not in cmd:
            cmd.append(str(output_path))
        return cmd

    def _legacy_env(self, project_root: Path) -> dict:
        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(project_root)
        return env
