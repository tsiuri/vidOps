# vidops/services/download.py

import logging
import os
import json
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError

from vidops.dal import VideoRepository, JobRepository, FilesystemCache
from vidops.models import Video, Job, JobStatus
from vidops.config import load_config, get_project_root

logger = logging.getLogger(__name__)

class DownloadService:
    """
    Orchestrates video downloads using yt-dlp.
    Downloads videos and registers them in the database.
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

    def enqueue_download(
        self,
        url: str,
        priority: int = 0,
        cookies_browser: str | None = None,
        upload_type: str | None = None,
    ) -> Job:
        """
        Enqueues a video download job.

        Args:
            url: YouTube video URL
            priority: Job priority
            cookies_browser: Browser name (or browser:profile) for cookies passthrough
            upload_type: Optional upload type/id to persist with the video record

        Returns:
            Created Job object
        """
        # Extract ytid from URL
        ytid = self._extract_ytid(url)

        # Create download job
        dl_cfg = self.config.download
        cookies_browser = cookies_browser or dl_cfg.cookies_browser or None
        ytdlp_cfg = {
            "format": dl_cfg.format,
            "audio_only": dl_cfg.audio_only,
            "audio_format": dl_cfg.audio_format,
            "audio_quality": dl_cfg.audio_quality,
            "embed_metadata": dl_cfg.embed_metadata,
            "use_archive": dl_cfg.use_archive,
            "archive_path": dl_cfg.archive_path,
            "cookies_browser": cookies_browser,
            "sleep_requests": dl_cfg.sleep_requests,
            "sleep_interval": dl_cfg.sleep_interval,
            "sleep_max_interval": dl_cfg.sleep_max_interval,
            "retries": dl_cfg.retries,
            "fragment_retries": dl_cfg.fragment_retries,
            "extractor_retries": dl_cfg.extractor_retries,
            "concurrent_fragments": dl_cfg.concurrent_fragments,
            "write_auto_subs": dl_cfg.write_auto_subs,
            "sub_langs": dl_cfg.sub_langs,
            "no_transcript_log": dl_cfg.no_transcript_log,
            "no_overwrites": dl_cfg.no_overwrites,
        }
        job_config: dict[str, Any] = {
            "url": url,
            "ytdlp": ytdlp_cfg,
        }
        if upload_type:
            job_config["upload_type"] = upload_type

        job = Job(
            job_type="download",
            ytid=ytid,
            media_path=url,
            config=job_config,
            priority=priority,
            status=JobStatus.PENDING
        )

        return self.job_repo.create(job)

    def process_job(self, job: Job) -> None:
        """
        Processes a download job by downloading the video with yt-dlp.

        Args:
            job: The download job to process
        """
        job_config = job.config if isinstance(job.config, dict) else {}
        url = job_config.get('url') or job.media_path

        if not url:
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, "No URL provided")
            return

        try:
            start_time = time.time()
            # Update status to RUNNING
            self.job_repo.update_status(job.job_id, JobStatus.RUNNING)
            upload_type = self._resolve_upload_type(job, job_config)
            job_config = job.config if isinstance(job.config, dict) else job_config

            logger.info(f"Downloading video from {url}")

            # Download into local cache first (avoids relying on mounted storage)
            project_root = Path(
                os.environ.get("VIDOPS_PROJECT_ROOT")
                or os.environ.get("PWD")
                or Path.cwd()
            )
            download_dir = project_root / "pull"
            download_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Download staging dir: %s", download_dir)

            # yt-dlp options (configurable)
            ytdlp_cfg = job_config.get("ytdlp") if isinstance(job_config, dict) else None
            if not ytdlp_cfg:
                # Do not apply live-config defaults to old jobs; force re-enqueue
                self.job_repo.update_status(
                    job.job_id,
                    JobStatus.FAILED,
                    error_message="Job missing yt-dlp config; re-enqueue with current settings.",
                )
                return
            ytid = self._extract_ytid(url)
            is_single_video = self._looks_like_video_id(ytid)
            # Early exit if media already exists in pull/ (only for single-video URLs)
            if is_single_video:
                existing = None
                for ext in (".opus", ".m4a", ".mp3", ".mp4", ".mkv", ".webm", ".mka"):
                    candidates = sorted(download_dir.glob(f"{ytid}__*.{ext}"))
                    if candidates:
                        existing = candidates[0]
                        break
                if existing and existing.exists():
                    info_path = existing.with_name(existing.name + ".info.json")
                    meta = self._load_info_json(info_path) or {}
                    upload_date = self._parse_upload_date(meta.get("upload_date"))
                    video = Video(
                        ytid=ytid,
                        url=meta.get("webpage_url") or url,
                        title=meta.get("title") or existing.stem,
                        upload_date=upload_date,
                        duration_sec=meta.get("duration"),
                        channel=meta.get("uploader") or meta.get("channel"),
                        channel_id=meta.get("channel_id") or meta.get("uploader_id"),
                        extractor_key=meta.get("extractor_key"),
                        tags=meta.get("tags") or [],
                        categories=meta.get("categories") or [],
                        upload_type=upload_type,
                    )
                    self.video_repo.upsert(video)

                    relative_path = str(Path("raw") / existing.name)
                    logger.info("Found existing media in pull/: %s; registering without re-download", existing)
                    stored_path = self.fs_cache.persist_local_artifact(
                        local_path=existing,
                        relative_path=relative_path,
                        video_repo=self.video_repo,
                        ytid=ytid,
                        kind='media'
                    )
                    result = {
                        "ytid": ytid,
                        "relative_path": relative_path,
                        "absolute_path": str(self.fs_cache.get_central_path(relative_path)),
                        "title": existing.name,
                        "duration_sec": None,
                        "filesize_bytes": existing.stat().st_size if existing.exists() else None,
                        "subtitles": [],
                        "registered_media": [{"ytid": ytid, "rel_path": relative_path}],
                    }
                    self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=result)
                    logger.info("Reused existing download %s -> %s", existing, stored_path)
                    return

            audio_only = bool(ytdlp_cfg.get("audio_only", False))
            fmt = ytdlp_cfg.get("format", "")
            audio_format = ytdlp_cfg.get("audio_format", "opus")
            audio_quality = ytdlp_cfg.get("audio_quality", "0")
            embed_metadata = bool(ytdlp_cfg.get("embed_metadata", True))
            use_archive = bool(ytdlp_cfg.get("use_archive", False))
            archive_path = ytdlp_cfg.get("archive_path") or ""
            no_overwrites = bool(ytdlp_cfg.get("no_overwrites", False))
            cookies_browser = ytdlp_cfg.get("cookies_browser") or None
            sleep_requests = int(ytdlp_cfg.get("sleep_requests", 0) or 0)
            sleep_interval = int(ytdlp_cfg.get("sleep_interval", 0) or 0)
            sleep_max_interval = int(ytdlp_cfg.get("sleep_max_interval", 0) or 0)
            retries = int(ytdlp_cfg.get("retries", 5) or 5)
            fragment_retries = int(ytdlp_cfg.get("fragment_retries", 5) or 5)
            extractor_retries = int(ytdlp_cfg.get("extractor_retries", 3) or 3)
            concurrent_fragments = int(ytdlp_cfg.get("concurrent_fragments", 1) or 1)
            write_auto_subs = bool(ytdlp_cfg.get("write_auto_subs", False))
            sub_langs = ytdlp_cfg.get("sub_langs") or "en"
            no_transcript_log = ytdlp_cfg.get("no_transcript_log") or "logs/no_transcripts_available.txt"

            ydl_opts = {
                "format": fmt,
                "outtmpl": str(download_dir / "%(id)s__%(upload_date>%Y-%m-%d)s - %(title).120B.%(ext)s"),
                "writeinfojson": True,
                "quiet": False,
                "no_warnings": False,
            }
            if use_archive and archive_path:
                ydl_opts["download_archive"] = str(project_root / archive_path)
            if cookies_browser:
                # Parse cookies_browser: "browser" or "browser:profile"
                parts = cookies_browser.split(":", 1)
                if len(parts) == 2:
                    ydl_opts["cookiesfrombrowser"] = (parts[0], parts[1])
                else:
                    ydl_opts["cookiesfrombrowser"] = (parts[0],)
            if no_overwrites:
                ydl_opts["overwrites"] = False
            # Pacing/retry mappings
            ydl_opts["retries"] = retries
            ydl_opts["fragment_retries"] = fragment_retries
            ydl_opts["extractor_retries"] = extractor_retries
            ydl_opts["concurrent_fragment_downloads"] = concurrent_fragments
            if sleep_requests > 0:
                ydl_opts["sleep_interval_requests"] = sleep_requests
            if sleep_interval > 0:
                ydl_opts["sleep_interval"] = sleep_interval
            if sleep_max_interval > 0:
                ydl_opts["max_sleep_interval"] = sleep_max_interval
            if embed_metadata:
                ydl_opts["embedmetadata"] = True
            if audio_only:
                ydl_opts["format"] = fmt or "bestaudio/best"
                ydl_opts["postprocessors"] = [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": audio_format,
                        "preferredquality": audio_quality,
                    }
                ]
            if write_auto_subs:
                ydl_opts["writeautomaticsub"] = True
                ydl_opts["writesubtitles"] = True
                ydl_opts["subtitleslangs"] = [sub_langs]
                ydl_opts["subtitlesformat"] = "vtt"

            # Download and extract info
            info = None
            ydl_for_filename = None
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    ydl_for_filename = ydl
            except DownloadError as de:
                msg = str(de)
                # Retry once without cookies if browser/cookie issues detected
                if "cookiesfrombrowser" in ydl_opts and any(
                    phrase in msg for phrase in [
                        "unsupported browser",
                        "could not find firefox cookies database",
                        "could not find chrome cookies database",
                        "cookie",
                    ]
                ):
                    logger.warning("Browser cookies failed (%s); retrying without cookies", msg)
                    ydl_opts.pop("cookiesfrombrowser", None)
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        ydl_for_filename = ydl
                else:
                    raise

            logger.info("yt-dlp finished for %s (id=%s)", url, info.get("id") if info else "unknown")

            if not info:
                logger.warning("yt-dlp returned no video info; attempting fallback registration from pull/ for %s", ytid)
                media_exts = {".opus", ".m4a", ".mp3", ".mp4", ".mkv", ".webm", ".mka"}
                existing = None
                for media_file in sorted(download_dir.glob(f"{ytid}__*")):
                    if media_file.suffix.lower() in media_exts and media_file.is_file():
                        existing = media_file
                        break
                if existing and existing.exists():
                    meta = self._load_info_json(existing.with_suffix(".info.json")) or {}
                    upload_date = self._parse_upload_date(meta.get("upload_date"))
                    video = Video(
                        ytid=ytid,
                        url=meta.get("webpage_url") or url,
                        title=meta.get("title") or existing.stem,
                        upload_date=upload_date,
                        duration_sec=meta.get("duration"),
                        channel=meta.get("uploader") or meta.get("channel"),
                        channel_id=meta.get("channel_id") or meta.get("uploader_id"),
                        extractor_key=meta.get("extractor_key"),
                        tags=meta.get("tags") or [],
                        categories=meta.get("categories") or [],
                        upload_type=upload_type,
                    )
                    self.video_repo.upsert(video)

                    relative_path = str(Path("raw") / existing.name)
                    logger.info("Registering existing media in pull/: %s -> %s", existing, relative_path)
                    self.fs_cache.persist_local_artifact(
                        local_path=existing,
                        relative_path=relative_path,
                        video_repo=self.video_repo,
                        ytid=ytid,
                        kind='media'
                    )
                    subtitle_rel_paths = []
                    for sub in download_dir.glob(f"{ytid}__*.vtt"):
                        rel = Path("raw") / sub.name
                        try:
                            self.fs_cache.persist_local_artifact(
                                local_path=sub,
                                relative_path=str(rel),
                                video_repo=self.video_repo,
                                ytid=ytid,
                                kind='vtt'
                            )
                            subtitle_rel_paths.append(str(rel))
                        except Exception as exc:
                            logger.warning("Failed to register subtitle %s: %s", sub, exc)

                    if write_auto_subs and not subtitle_rel_paths:
                        log_path = project_root / no_transcript_log
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        with log_path.open("a", encoding="utf-8") as fh:
                            fh.write(f"{ytid}\n")

                    result = {
                        "ytid": ytid,
                        "relative_path": relative_path,
                        "absolute_path": str(self.fs_cache.get_central_path(relative_path)),
                        "title": meta.get("title") or existing.name,
                        "duration_sec": meta.get("duration"),
                        "filesize_bytes": existing.stat().st_size if existing.exists() else None,
                        "subtitles": subtitle_rel_paths,
                        "registered_media": [{"ytid": ytid, "rel_path": relative_path}],
                    }
                    self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=result)
                    logger.info("Registered existing download for %s via archive/no-info fallback", ytid)
                    return

                raise RuntimeError("yt-dlp returned no video info and no existing media in pull/")

            entries = []
            if isinstance(info, dict) and info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                logger.info("Detected playlist with %d entries", len(entries))
            else:
                entries = [info]

            registered_media: list[dict[str, str]] = []
            last_result: dict[str, Any] | None = None

            for entry in entries:
                entry_id = entry.get("id")
                if not entry_id:
                    logger.warning("Skipping playlist entry with no id: %s", entry)
                    continue

                # Resolve the actual output file (handles postprocessors/extension changes)
                downloaded_file = Path(ydl_for_filename.prepare_filename(entry)) if ydl_for_filename else None
                if not downloaded_file or not downloaded_file.exists():
                    # Try any recent file matching the id (handles postprocessors + existing files when no_overwrites=True)
                    downloaded_file = self._find_existing_download(download_dir, entry_id)
                if not downloaded_file or not downloaded_file.exists():
                    logger.warning("Could not locate download for %s", entry_id)
                    continue

                subtitles = []
                for sub in download_dir.glob(f"{entry_id}__*.vtt"):
                    # rename .en.vtt -> .transcript.en.vtt for consistency
                    if sub.name.endswith(".en.vtt") and "transcript.en.vtt" not in sub.name:
                        newname = sub.with_name(sub.stem + ".transcript.en.vtt")
                        try:
                            sub.rename(newname)
                            sub = newname
                        except OSError:
                            pass
                    subtitles.append(sub)

                # Parse upload_date (YYYYMMDD format)
                upload_date = self._parse_upload_date(entry.get('upload_date'))

                # Create Video object
                video = Video(
                    ytid=entry_id,
                    url=entry.get('webpage_url') or url,
                    title=entry.get('title'),
                    upload_date=upload_date,
                    duration_sec=entry.get('duration'),
                    channel=entry.get('uploader') or entry.get('channel'),
                    channel_id=entry.get('channel_id') or entry.get('uploader_id'),
                    extractor_key=entry.get('extractor_key'),
                    tags=entry.get('tags') or [],
                    categories=entry.get('categories') or [],
                    upload_type=upload_type,
                )

                # Upsert video to database
                self.video_repo.upsert(video)

                # Persist downloaded file into central storage (or broker)
                relative_path = str(Path("raw") / downloaded_file.name)
                logger.info("Uploading media to storage/broker: %s -> %s", downloaded_file, relative_path)
                stored_path = self.fs_cache.persist_local_artifact(
                    local_path=downloaded_file,
                    relative_path=relative_path,
                    video_repo=self.video_repo,
                    ytid=entry_id,
                    kind='media'
                )
                logger.info("Media persisted at %s", stored_path)

                subtitle_rel_paths = []
                for sub in subtitles:
                    rel = Path("raw") / sub.name
                    try:
                        self.fs_cache.persist_local_artifact(
                            local_path=sub,
                            relative_path=str(rel),
                            video_repo=self.video_repo,
                            ytid=entry_id,
                            kind='vtt'
                        )
                        subtitle_rel_paths.append(str(rel))
                    except Exception as exc:
                        logger.warning("Failed to register subtitle %s: %s", sub, exc)

                if write_auto_subs and not subtitle_rel_paths:
                    log_path = project_root / no_transcript_log
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a", encoding="utf-8") as fh:
                        fh.write(f"{entry_id}\n")

                last_result = {
                    "ytid": entry_id,
                    "relative_path": relative_path,
                    "absolute_path": str(self.fs_cache.get_central_path(relative_path)),
                    "title": entry.get('title'),
                    "duration_sec": entry.get('duration'),
                    "filesize_bytes": downloaded_file.stat().st_size if downloaded_file.exists() else None,
                    "subtitles": subtitle_rel_paths,
                }
                registered_media.append({"ytid": entry_id, "rel_path": relative_path})

            # Keep only real video ids; playlists/channels can return container ids
            registered_media = [
                rm for rm in registered_media
                if self._looks_like_video_id(rm.get("ytid"))
            ]
            if registered_media:
                result = last_result or {}
                result["registered_media"] = registered_media

                self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=result)
                logger.info("Successfully downloaded %d item(s) from %s", len(registered_media), url)
                return

            # Fallback: if nothing was registered (playlist/channel edge cases), register any media in pull/ since start_time
            fallback_registered: list[dict[str, str]] = []
            media_exts = {".opus", ".m4a", ".mp3", ".mp4", ".mkv", ".webm", ".mka"}
            for media_file in sorted(download_dir.glob("*")):
                if not media_file.is_file() or media_file.suffix.lower() not in media_exts:
                    continue
                fname = media_file.name
                if "__" not in fname:
                    continue
                ytid_candidate = fname.split("__", 1)[0]
                if not self._looks_like_video_id(ytid_candidate):
                    continue

                info_path = media_file.with_suffix(".info.json")
                meta = {}
                if info_path.exists():
                    meta = self._load_info_json(info_path) or {}

                upload_date = self._parse_upload_date(meta.get("upload_date"))

                # Upsert minimal video metadata
                video = Video(
                    ytid=ytid_candidate,
                    url=meta.get("webpage_url") or url,
                    title=meta.get("title") or fname,
                    upload_date=upload_date,
                    duration_sec=meta.get("duration"),
                    channel=meta.get("uploader") or meta.get("channel"),
                    channel_id=meta.get("channel_id") or meta.get("uploader_id"),
                    extractor_key=meta.get("extractor_key"),
                    tags=meta.get("tags") or [],
                    categories=meta.get("categories") or [],
                    upload_type=upload_type,
                )
                self.video_repo.upsert(video)

                relative_path = str(Path("raw") / media_file.name)
                self.fs_cache.persist_local_artifact(
                    local_path=media_file,
                    relative_path=relative_path,
                    video_repo=self.video_repo,
                    ytid=ytid_candidate,
                    kind='media'
                )

                subtitle_rel_paths = []
                for sub in download_dir.glob(f"{ytid_candidate}__*.vtt"):
                    rel = Path("raw") / sub.name
                    try:
                        self.fs_cache.persist_local_artifact(
                            local_path=sub,
                            relative_path=str(rel),
                            video_repo=self.video_repo,
                            ytid=ytid_candidate,
                            kind='vtt'
                        )
                        subtitle_rel_paths.append(str(rel))
                    except Exception as exc:
                        logger.warning("Failed to register subtitle %s: %s", sub, exc)

                fallback_registered.append({"ytid": ytid_candidate, "rel_path": relative_path})
                last_result = {
                    "ytid": ytid_candidate,
                    "relative_path": relative_path,
                    "absolute_path": str(self.fs_cache.get_central_path(relative_path)),
                    "title": video.title,
                    "duration_sec": video.duration_sec,
                    "filesize_bytes": media_file.stat().st_size if media_file.exists() else None,
                    "subtitles": subtitle_rel_paths,
                }

            if fallback_registered:
                result = last_result or {}
                result["registered_media"] = fallback_registered
                self.job_repo.update_status(job.job_id, JobStatus.COMPLETED, result=result)
                logger.info("Registered %d existing media file(s) from pull/ for %s", len(fallback_registered), url)
            else:
                raise RuntimeError("Download produced no media entries to register")

        except Exception as e:
            # Add guidance for common auth/age-restrict failures
            msg = str(e)
            hint = ""
            if "Sign in to confirm your age" in msg or "age-restricted" in msg:
                hint = " (set download.cookies_browser in config.yaml or pass --cookies-browser to use your browser cookies)"
            error_msg = f"Download failed for job {job.job_id}: {msg}{hint}"
            logger.error(error_msg, exc_info=True)
            self.job_repo.update_status(job.job_id, JobStatus.FAILED, error_msg)

    def _resolve_upload_type(self, job: Job, job_config: dict[str, Any] | None = None) -> str:
        """
        Return a provided upload_type or generate a unique identifier and persist it on the job.
        """
        config = job_config if isinstance(job_config, dict) else (job.config if isinstance(job.config, dict) else {})
        existing = ""
        if isinstance(config, dict):
            existing = (config.get("upload_type") or "").strip()
        if existing:
            return existing

        generated = f"upload-{uuid.uuid4().hex[:12]}"
        logger.info("No upload_type provided for job %s; generated %s", job.job_id, generated)
        updated_config = dict(config) if isinstance(config, dict) else {}
        updated_config["upload_type"] = generated
        job.config = updated_config
        try:
            self.job_repo.update_config(job.job_id, updated_config)
        except Exception:
            logger.warning("Failed to persist generated upload_type for job %s", job.job_id, exc_info=True)
        return generated

    def _extract_ytid(self, url: str) -> str:
        """
        Extracts YouTube video ID from URL.

        Args:
            url: YouTube URL

        Returns:
            YouTube video ID
        """
        # Handle various YouTube URL formats
        if "v=" in url:
            return url.split('v=')[-1].split('&')[0]
        elif "youtu.be/" in url:
            return url.split('youtu.be/')[-1].split('?')[0]
        else:
            # Assume it's already a video ID
            return url

    def _looks_like_video_id(self, ytid: str | None) -> bool:
        """Heuristic to decide if the identifier is a single-video ID."""
        if not ytid:
            return False
        if len(ytid) != 11:
            return False
        return all(ch.isalnum() or ch in "-_" for ch in ytid)

    def _find_existing_download(self, download_dir: Path, entry_id: str) -> Path | None:
        """
        Find an already-downloaded file for a given entry id in the pull directory.
        Handles audio-only extensions and prior runs with no_overwrites.
        """
        stem = f"{entry_id}__"
        candidates = sorted(
            download_dir.glob(f"{stem}*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _parse_upload_date(self, upload_date_str: str | None) -> date | None:
        """Parse YYYYMMDD upload_date strings from yt-dlp metadata."""
        if upload_date_str and len(upload_date_str) == 8 and upload_date_str.isdigit():
            return date(
                int(upload_date_str[0:4]),
                int(upload_date_str[4:6]),
                int(upload_date_str[6:8])
            )
        return None

    def _load_info_json(self, info_path: Path) -> dict[str, Any] | None:
        """Load a yt-dlp .info.json sidecar if present."""
        if not info_path.exists():
            return None
        try:
            return json.loads(info_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load info.json %s: %s", info_path, exc)
            return None
