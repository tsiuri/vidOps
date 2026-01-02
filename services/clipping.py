# vidops/services/clipping.py

import logging
import math
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
import json
from datetime import datetime
from psycopg2.extras import Json

from dal import VideoRepository, JobRepository, FilesystemCache
from models import Job, JobStatus
from configuration import load_config
from db import get_connection
import os
import csv
import time
from services.clipping_native import NativeClipper, ClipRow

logger = logging.getLogger(__name__)

class ClippingService:
    """
    Orchestrates the video clipping process, managing job creation,
    dispatch, and result handling.
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
        self._clipper = NativeClipper()

    def enqueue_clip_job(
        self,
        ytid: str,
        start_sec: float,
        end_sec: float,
        label: str,
        priority: int = 50,
        media_relative_path: Optional[str] = None,
        transcript_source: Optional[str] = None,
        run_name: Optional[str] = None,
        output_dir: Optional[str] = None,
        manifest_path: Optional[str] = None,
        transcribe_clips: bool = False,
        transcription_model: Optional[str] = None,
        transcription_language: Optional[str] = None,
    ) -> Job:
        """
        Enqueues a single video clipping job.

        Args:
            ytid: YouTube ID of the video to clip.
            start_sec: Start time of the clip in seconds.
            end_sec: End time of the clip in seconds.
            label: A label for the clip (e.g., search term).
            priority: Job priority.

        Returns:
            The created Job object.

        Raises:
            ValueError: If the video is not found.
        """
        video = self.video_repo.get(ytid)
        if not video:
            raise ValueError(f"Video with ytid '{ytid}' not found.")

        media_rel = media_relative_path or self._resolve_media_asset_path(ytid)
        if not media_rel:
            raise ValueError(f"No stored media asset for video '{ytid}'. Download it first.")

        # Create the job configuration
        config = {
            "ytid": ytid,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "label": label,
            "media_asset_path": media_rel,
            "transcript_source": transcript_source,
            "run_name": run_name,
            "output_dir": output_dir,
            "manifest_path": manifest_path,
            "output_relative_path": self._build_clip_relative_path(
                ytid,
                label,
                start_sec,
                end_sec,
                output_dir=output_dir,
                run_name=run_name,
            ),
            "transcribe_clips": transcribe_clips,
            "transcription_model": transcription_model or self.config.transcription.model,
            "transcription_language": transcription_language or self.config.transcription.language,
        }

        # Create the job in the database
        job = Job(
            job_type="clipping",
            ytid=ytid,
            media_path=media_rel,
            config=config,
            priority=priority,
            status=JobStatus.PENDING
        )
        return self.job_repo.create(job)

    def enqueue_manifest_job(
        self,
        manifest_path: str,
        run_name: str,
        output_dir: Optional[str],
        ytid: str,
        priority: int = 50,
        mode: str = "net",
        session_id: Optional[str] = None,
        transcribe_clips: bool = False,
        transcription_model: Optional[str] = None,
        transcription_language: Optional[str] = None,
    ) -> Job:
        """
        Enqueue a clipping job for a full manifest (legacy cut-local will process all rows).
        """
        config = {
            "run_name": run_name,
            "output_dir": output_dir,
            "manifest_path": manifest_path,
            "mode": mode,
            "session_id": session_id,
            "transcribe_clips": transcribe_clips,
            "transcription_model": transcription_model or self.config.transcription.model,
            "transcription_language": transcription_language or self.config.transcription.language,
        }
        job = Job(
            job_type="clipping",
            ytid=ytid,
            media_path=None,
            config=config,
            priority=priority,
            status=JobStatus.PENDING,
        )
        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Processes a single clipping job claimed by a worker.
        """
        if not job.ytid:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "Job has no ytid.")
            return

        try:
            # 2. Update job status to RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING, "Starting clipping.")

            # 3. Reconstruct legacy inputs
            project_root = Path(
                os.environ.get("VIDOPS_PROJECT_ROOT", "")
            ).resolve() if os.environ.get("VIDOPS_PROJECT_ROOT") else Path(__file__).resolve().parents[1]
            run_name = job.config.get("run_name") or job.config.get("label") or "clip"
            hits_manifest = self._prepare_manifest(job, project_root, run_name)
            output_dir = self._prepare_output_dir(job, project_root, run_name)
            rel_output_dir = output_dir.relative_to(project_root) if output_dir.is_absolute() else output_dir

            # Stage media for all ytids in manifest
            ytids = self._collect_manifest_ytids(hits_manifest)
            staged_media: dict[str, Path] = {}
            for mid in ytids:
                media_relative = self._resolve_media_asset_path(mid)
                if not media_relative:
                    logger.warning("No media asset for %s; legacy cutter may skip", mid)
                    continue
                local_source = self.fs_cache.pull_to_cache(media_relative)
                staged_path = self._stage_media(Path(local_source), project_root)
                staged_media[mid] = staged_path

            # 4. Invoke native cutter (mirrors legacy outputs/layout)
            rows = self._read_manifest_rows(hits_manifest)
            mode = job.config.get("mode", "net")
            start_ts = time.time()
            if mode == "local":
                clip_paths = self._clipper.cut_local(rows, output_dir, staged_media)
            else:
                clip_paths = self._clipper.cut_net(rows, output_dir)

            # Fallback to mtime scan if nothing returned
            if not clip_paths:
                clip_paths = self._find_new_clips(output_dir, since=start_ts, ytids=ytids)
            registered = []

            # Persist manifest
            manifest_rel = hits_manifest.relative_to(project_root)
            stored_manifest = self.fs_cache.persist_local_artifact(
                hits_manifest,
                str(manifest_rel),
                video_repo=self.video_repo,
                ytid=job.ytid,
                kind="clips_manifest",
            )

            for clip_path in clip_paths:
                sanitized_name = self._sanitize_filename(clip_path.name)
                if sanitized_name != clip_path.name:
                    new_path = clip_path.with_name(sanitized_name)
                    clip_path.rename(new_path)
                    clip_path = new_path
                relative_output = str(rel_output_dir / clip_path.name)

                # Upload to central storage
                self.fs_cache.persist_local_artifact(
                    clip_path,
                    relative_output,
                    video_repo=self.video_repo,
                    ytid=job.ytid,
                    kind="clip"
                )

                # Keep a local copy in the output directory
                final_local_path = output_dir / clip_path.name
                if clip_path.resolve() != final_local_path.resolve():
                    if not final_local_path.exists():
                        shutil.copy2(clip_path, final_local_path)
                        logger.info(f"Kept local copy: {final_local_path}")

                registered.append(relative_output)

            if not registered:
                raise FileNotFoundError(f"No clip output found in {output_dir}")

            # Sort registered clips by filename to ensure consistent order matching with clip_index
            # Clips come from glob() which has arbitrary order, but we need them sorted alphabetically
            # This ensures clip 030.00-060 comes before clip 031.00-035, matching database order
            registered = sorted(registered)

            session_id = job.config.get("session_id")
            if session_id:
                self._update_quickclip_asset_paths(session_id, registered)

            # Handle transcription if enabled
            transcribe_clips_value = job.config.get("transcribe_clips")
            logger.info(f"Clipping job config transcribe_clips value: {transcribe_clips_value} (type: {type(transcribe_clips_value)})")
            logger.info(f"Full job config: {job.config}")

            if transcribe_clips_value:
                logger.info(f"Enqueuing clip transcriptions for session {run_name}")
                self._enqueue_clip_transcriptions(job, registered, run_name)
            else:
                logger.debug(f"Skipping clip transcriptions: transcribe_clips={transcribe_clips_value}")

            job_result = {
                "clip_paths": registered,
                "run_name": run_name,
                "manifest_path": str(stored_manifest),
            }
            self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=job_result)
            logger.info("Successfully clipped manifest %s with %d outputs", hits_manifest, len(registered))

        except Exception as e:
            error_msg = f"Clipping failed for job {job.job_id} ({job.ytid}): {e}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_media_asset_path(self, ytid: str) -> Optional[str]:
        asset = self.video_repo.get_primary_asset(ytid, "media")
        return asset.path if asset else None

    def _build_clip_relative_path(
        self,
        ytid: str,
        label: str,
        start_sec: float,
        end_sec: float,
        output_dir: Optional[str] = None,
        run_name: Optional[str] = None,
    ) -> str:
        safe_label = "".join(ch if ch.isalnum() else "-" for ch in label.lower()).strip("-") or "clip"
        start_tag = f"{math.floor(start_sec*1000):07d}"
        end_tag = f"{math.floor(end_sec*1000):07d}"
        filename = f"{ytid}_{safe_label}_{start_tag}-{end_tag}.mp4"
        base_dir = Path(output_dir) if output_dir else Path("clips") / (run_name or ytid)
        return str(base_dir / filename)

    def _prepare_manifest(self, job: Job, project_root: Path, run_name: str) -> Path:
        if job.config.get("manifest_path"):
            src = Path(job.config["manifest_path"])
            if not src.exists():
                raise FileNotFoundError(f"Manifest not found: {src}")
            target = project_root / "generated" / "hits" / run_name / "hits.tsv"
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != target.resolve():
                shutil.copy2(src, target)
            return target

        target = project_root / "generated" / "hits" / run_name / "hits.tsv"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Build a single-row manifest in legacy format: url, start, end, label, caption
        fields = ["url", "start", "end", "label", "caption"]
        url = f"https://www.youtube.com/watch?v={job.ytid}"
        row = {
            "url": url,
            "start": job.config.get("start_sec"),
            "end": job.config.get("end_sec"),
            "label": job.config.get("label"),
            "caption": job.config.get("phrase") or job.config.get("label") or "",
        }
        with target.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerow(row)
        return target

    def _prepare_output_dir(self, job: Job, project_root: Path, run_name: str) -> Path:
        output_cfg = job.config.get("output_dir")
        # Default to generated/hits/<run_name> to align with manifest location
        output_dir = Path(output_cfg) if output_cfg else Path("generated") / "hits" / run_name
        if not output_dir.is_absolute():
            output_dir = project_root / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _stage_media(self, cached_media: Path, project_root: Path) -> Path:
        pull_dir = project_root / "pull"
        pull_dir.mkdir(parents=True, exist_ok=True)
        target = pull_dir / cached_media.name
        if not target.exists():
            shutil.copy2(cached_media, target)
        return target

    def _run_legacy_cut(self, manifest: Path, output_dir: Path, project_root: Path, job: Job) -> None:
        workspace_sh = Path(__file__).resolve().parents[1] / "workspace.sh"
        mode = job.config.get("mode", "net")
        subcmd = "cut-net" if mode == "net" else "cut-local"
        cmd = ["bash", str(workspace_sh), "clips", subcmd, str(manifest), str(output_dir)]
        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(project_root)
        logger.info("Running legacy clips %s: %s", subcmd, " ".join(cmd))
        result = subprocess.run(cmd, cwd=project_root, env=env, text=True, capture_output=False)
        if result.returncode != 0:
            raise RuntimeError(f"Legacy clips {subcmd} failed (exit {result.returncode})")

    def _find_new_clips(self, output_dir: Path, since: float, ytids: List[str]) -> List[Path]:
        exts = ["mp4", "mkv", "webm", "mp3", "mka", "opus"]
        candidates: List[Path] = []
        for ext in exts:
            candidates.extend([p for p in output_dir.glob(f"*.{ext}") if p.stat().st_mtime >= since - 1])
        if not candidates and ytids:
            for ext in exts:
                for y in ytids:
                    candidates.extend(output_dir.glob(f"*{y}*.{ext}"))
        return candidates

    def _collect_manifest_ytids(self, manifest: Path) -> List[str]:
        ytids: List[str] = []
        with manifest.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("url") or line.strip() == "":
                    continue
                parts = line.strip().split("\t")
                if not parts or not parts[0]:
                    continue
                url = parts[0]
                if "watch?v=" in url:
                ytids.append(url.split("watch?v=")[-1][:11])
        return ytids or [""]

    def _read_manifest_rows(self, manifest: Path) -> List[ClipRow]:
        """Read manifest TSV (url, start, end, label, caption) into ClipRow objects."""
        rows: List[ClipRow] = []
        with manifest.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                try:
                    rows.append(
                        ClipRow(
                            url=row.get("url", ""),
                            start=float(row.get("start") or 0.0),
                            end=float(row.get("end") or 0.0),
                            label=row.get("label", ""),
                            caption=row.get("caption", ""),
                        )
                    )
                except Exception:
                    continue
        return rows

    def _sanitize_filename(self, name: str) -> str:
        safe = []
        for ch in name:
            if ord(ch) < 128:
                safe.append(ch)
            else:
                safe.append("-")
        sanitized = "".join(safe)
        # collapse consecutive dashes
        while "--" in sanitized:
            sanitized = sanitized.replace("--", "-")
        return sanitized

    def _render_clip(self, source_path: Path, output_path: Path, start_sec: float, end_sec: float) -> None:
        duration = max(0.1, end_sec - start_sec)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-ss", f"{start_sec:.3f}",
            "-i", str(source_path),
            "-t", f"{duration:.3f}",
            "-c", "copy",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            shutil.copy2(source_path, output_path)
        except subprocess.CalledProcessError as exc:
            logger.warning("ffmpeg clipping failed (%s). Falling back to file copy.", exc)
            shutil.copy2(source_path, output_path)

    def _enqueue_clip_transcriptions(self, job: Job, registered_clips: List[str], run_name: str) -> None:
        """
        Enqueue transcription jobs for extracted clips.

        Transcribes the clip media file directly (not the full video),
        allowing clips to be transcribed even if the full video wasn't downloaded.

        Args:
            job: The clipping job
            registered_clips: List of registered clip relative paths
            run_name: The run name (typically session_id for quickclips)
        """
        from models import Job as JobModel, JobStatus

        logger.info(f"_enqueue_clip_transcriptions called with {len(registered_clips)} clips, job_config keys: {list(job.config.keys())}")

        session_id = job.config.get("session_id")
        if not session_id:
            logger.warning("Cannot enqueue clip transcriptions: session_id not in job config")
            return

        model = job.config.get("transcription_model", self.config.transcription.model)
        language = job.config.get("transcription_language", self.config.transcription.language)
        ytid = job.ytid
        # Inherit priority from the clipping job so clip transcriptions are processed with same importance
        priority = job.priority

        logger.info(f"Enqueuing transcription jobs for {len(registered_clips)} clips from session {session_id} (model={model}, language={language}, priority={priority})")

        try:
            # Find quickclip_clips records for this session
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT clip_id, start_sec, end_sec, label
                        FROM quickclip_clips
                        WHERE session_id = %s
                        ORDER BY clip_index ASC
                        """,
                        (session_id,)
                    )
                    clip_records = cur.fetchall()

            if not clip_records:
                logger.warning(f"No quickclip_clips records found for session {session_id}")
                return

            # Enqueue transcription job for each clip
            for clip_idx, (clip_id, start_sec, end_sec, label) in enumerate(clip_records):
                try:
                    # Find the registered clip path by matching timestamps in the filename
                    # e.g., "030.00-060.00" from start_sec=30.0, end_sec=60.0
                    clip_rel_path = None
                    start_str = f"{float(start_sec):06.2f}"
                    end_str = f"{float(end_sec):06.2f}"
                    timestamp_marker = f"{start_str}-{end_str}"

                    for reg_path in registered_clips:
                        if timestamp_marker in reg_path:
                            clip_rel_path = reg_path
                            break

                    if not clip_rel_path:
                        logger.warning(f"No registered clip path found for clip_id {clip_id} with timestamps {timestamp_marker}")
                        continue

                    # Enqueue transcription job for the CLIP FILE, not the full video
                    # This allows transcription of clips even without the full video
                    clip_config = {
                        "model": model,
                        "language": language,
                        "ytid": ytid,  # Keep ytid for reference, but transcribe the clip file
                        "media_path_hint": clip_rel_path,
                        "clip_context": {
                            "clip_id": clip_id,
                            "session_id": session_id,
                            "clipping_job_id": job.job_id,
                            "start_sec": float(start_sec),
                            "end_sec": float(end_sec),
                            "label": label,
                            "clip_media_path": clip_rel_path,  # Full clip media to transcribe
                        }
                    }

                    # Create transcription job for the clip media file
                    trans_job = JobModel(
                        job_type="transcription",
                        ytid=ytid,  # Reference to original video
                        media_path=clip_rel_path,  # Clip file path for transcription
                        config=clip_config,
                        priority=priority,
                        status=JobStatus.PENDING,
                    )
                    trans_job = self.job_repo.create(trans_job)

                    # Store clip metadata in quickclip_clips.transcripts JSONB
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            # Get or initialize the transcripts JSONB
                            cur.execute(
                                "SELECT transcripts FROM quickclip_clips WHERE clip_id = %s",
                                (clip_id,)
                            )
                            row = cur.fetchone()
                            transcripts = row[0] if row and row[0] else {}

                            # Add transcription job reference
                            transcripts[model] = {
                                "model": model,
                                "language": language,
                                "job_id": trans_job.job_id,
                                "status": "pending",
                                "created_at": datetime.utcnow().isoformat(),
                            }

                            # Update quickclip_clips with JSONB data
                            cur.execute(
                                "UPDATE quickclip_clips SET transcripts = %s WHERE clip_id = %s",
                                (Json(transcripts), clip_id)
                            )

                    logger.info(f"Enqueued transcription for clip {clip_id} (job={trans_job.job_id}, media={clip_rel_path})")

                except Exception as exc:
                    if "unique" in str(exc).lower():
                        logger.debug(f"Transcription job already exists for {ytid} model {model}, skipping")
                    else:
                        logger.warning(f"Failed to enqueue transcription for clip {clip_id}: {exc}")

        except Exception as e:
            logger.error(f"Error enqueueing clip transcriptions: {e}", exc_info=True)

    def _update_quickclip_asset_paths(self, session_id: str, registered_clips: List[str]) -> None:
        if not registered_clips:
            return
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT clip_id, start_sec, end_sec, clip_index
                        FROM quickclip_clips
                        WHERE session_id = %s
                        ORDER BY clip_index ASC
                        """,
                        (session_id,),
                    )
                    clip_records = cur.fetchall()
            if not clip_records:
                logger.debug("No quickclip_clips records found for session %s", session_id)
                return

            fallback_by_index = registered_clips if len(registered_clips) == len(clip_records) else []

            for idx, (clip_id, start_sec, end_sec, clip_index) in enumerate(clip_records):
                start_str = f"{float(start_sec):06.2f}"
                end_str = f"{float(end_sec):06.2f}"
                timestamp_marker = f"{start_str}-{end_str}"
                clip_rel_path = None
                for reg_path in registered_clips:
                    if timestamp_marker in reg_path:
                        clip_rel_path = reg_path
                        break
                if not clip_rel_path and fallback_by_index:
                    clip_rel_path = fallback_by_index[idx]
                if not clip_rel_path:
                    logger.warning(
                        "No registered clip path found for session %s clip %s (%s)",
                        session_id,
                        clip_id,
                        timestamp_marker,
                    )
                    continue
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE quickclip_clips SET asset_path = %s WHERE clip_id = %s",
                            (clip_rel_path, clip_id),
                        )
        except Exception as exc:
            logger.warning("Failed to update quickclip asset paths for session %s: %s", session_id, exc)
