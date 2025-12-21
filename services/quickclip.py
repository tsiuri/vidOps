# vidops/services/quickclip.py

import hashlib
import inspect
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from dal import VideoRepository, QuickClipRepository, FilesystemCache
from .download import DownloadService
from .clipping import ClippingService
from configuration import load_config

logger = logging.getLogger(__name__)


class QuickClipService:
    """
    Service for the QuickClip system - fast capture of video clips with metadata.
    """

    def __init__(
        self,
        video_repo: VideoRepository,
        quickclip_repo: QuickClipRepository,
        download_service: DownloadService,
        clipping_service: ClippingService,
        fs_cache: FilesystemCache,
    ):
        self.video_repo = video_repo
        self.quickclip_repo = quickclip_repo
        self.download_service = download_service
        self.clipping_service = clipping_service
        self.fs_cache = fs_cache
        self.config = load_config()

    def create_quickclip(
        self,
        url: str,
        spans: List[str],
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        clips_only: bool = False,
        quality: str = "best",
        session_name: Optional[str] = None,
        output_dir: Optional[str] = None,
        force: bool = False,
        priority: int = 90,
        transcribe_clips: bool = False,
        transcription_model: Optional[str] = None,
        transcription_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a QuickClip session with multiple clips, or download full video if no clips specified.

        Args:
            url: YouTube URL or video ID
            spans: List of timestamp spans (e.g., ["120-145", "300-320:label"]), or empty list for full video
            description: User description of the session
            tags: Optional tags for searchability
            clips_only: Skip downloading full video (only used when spans provided)
            quality: Quality profile (best, 1080p, 720p, audio-only)
            session_name: Custom session name (auto-generated if None)
            output_dir: Custom output directory
            force: Force re-download even if video exists
            priority: Job priority (default 90)

        Returns:
            Dict with session info and clip paths
        """
        # 1. Extract YTID from URL
        ytid = self._extract_ytid(url)
        full_url = url if url.startswith("http") else f"https://www.youtube.com/watch?v={ytid}"

        # 2. Generate session ID
        session_id = self._generate_session_id(ytid, session_name)

        # 3. Parse spans (or use empty list for full video download)
        parsed_spans = []
        for i, span_str in enumerate(spans, 1):
            span_data = self._parse_span(span_str, i)
            parsed_spans.append(span_data)

        if parsed_spans:
            logger.info(f"Creating QuickClip session {session_id} for {ytid} with {len(parsed_spans)} clips")
        else:
            logger.info(f"Creating QuickClip session {session_id} for {ytid} - full video download mode")

        # 4. Prepare output directory (organized by ytid only)
        if not output_dir:
            output_dir = f"generated/quickclips/{ytid}"
        session_dir = Path(output_dir)
        session_dir.mkdir(parents=True, exist_ok=True)

        # 5. Check if video exists, create if needed
        video = self.video_repo.get(ytid)
        if not video:
            # Create minimal video record so we can proceed
            from models import Video
            video = Video(
                ytid=ytid,
                url=full_url,
                title=None,  # Will be updated after download
                upload_date=None,
                duration_sec=None,
                channel=None,
                channel_id=None,
                extractor_key="youtube",
                tags=[],
                categories=[],
            )
            self.video_repo.upsert(video)
            logger.info(f"Created minimal video record for {ytid}")

        media_asset = self.video_repo.get_primary_asset(ytid, "media")
        if media_asset and (quality or "best").lower() != "audio-only":
            asset_path_str = getattr(media_asset, "rel_path", None) or getattr(media_asset, "path", "")
            ext = Path(asset_path_str).suffix.lower()
            audio_only_exts = {".opus", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".ogx", ".oga"}
            if ext in audio_only_exts:
                logger.info("Existing media asset for %s is audio-only (%s); re-downloading video.", ytid, ext)
                media_asset = None

        # 6. Download video if needed
        # Always download for full video mode (no clips), otherwise download if no media or force flag
        full_video_saved = False
        should_download = (not media_asset or force) or (not parsed_spans and not clips_only)

        if should_download and not clips_only:
            logger.info(f"Downloading full video {ytid} (quality: {quality})")
            # Force download if no clips (full video mode) or if force flag set
            force_dl = force or (not parsed_spans)
            download_job = self._enqueue_download_with_overrides(full_url, priority, quality, force=force_dl)
            # TODO: Wait for download to complete or implement async flow
            # For MVP, assume video will be downloaded
            full_video_saved = True
        elif clips_only:
            logger.info(f"Clips-only mode: skipping full video download")

        # 7. Handle clipping only if spans provided, otherwise skip to download-only mode
        clip_job = None
        resolved_spans = []

        if parsed_spans:
            # Resolve timestamps with video metadata
            if video and video.duration_sec:
                resolved_spans = self._resolve_spans(parsed_spans, video.duration_sec)
            else:
                # No duration available yet - resolve what we can, leave placeholders as-is
                resolved_spans = []
                for span in parsed_spans:
                    start_str = span["start_str"]
                    end_str = span["end_str"]

                    # Skip if placeholders are used without duration
                    if start_str == "(start)" or end_str == "(end)":
                        raise ValueError(
                            f"Cannot use (start) or (end) placeholders without video duration metadata. "
                            f"Video {ytid} needs to be downloaded first, or use explicit timestamps."
                        )

                    resolved_spans.append({
                        "start": float(start_str),
                        "end": float(end_str),
                        "label": span["label"],
                        "index": span["index"],
                    })

        # 8. Create session in database
        tags_str = ",".join(tags) if tags else None
        self.quickclip_repo.create_session(
            session_id=session_id,
            ytid=ytid,
            url=full_url,
            description=description,
            tags=tags_str,
            quality_profile=quality,
            session_dir=output_dir,
        )

        # 9-12. Enqueue clipping job only if spans provided
        total_duration = 0.0
        if parsed_spans:
            # 9. Create clips manifest for clipping service (with session ID in filename)
            manifest_path = session_dir / f"{session_id}.hits.tsv"
            self._create_hits_manifest(manifest_path, ytid, full_url, resolved_spans)

            # 10. Enqueue clipping job
            logger.info(f"Enqueuing clipping job for {len(resolved_spans)} clips")
            clip_job = self.clipping_service.enqueue_manifest_job(
                manifest_path=str(manifest_path.resolve()),
                run_name=session_id,
                output_dir=output_dir,
                ytid=ytid,
                priority=priority,
                mode="net",
                session_id=session_id,
                transcribe_clips=transcribe_clips,
                transcription_model=transcription_model,
                transcription_language=transcription_language,
            )

            # 11. Save clip records to database
            for span in resolved_spans:
                clip_id = f"{session_id}_clip{span['index']}"
                self.quickclip_repo.create_clip(
                    clip_id=clip_id,
                    session_id=session_id,
                    ytid=ytid,
                    start_sec=span['start'],
                    end_sec=span['end'],
                    label=span.get('label'),
                    clip_index=span['index'],
                    asset_path=None,  # Will be updated after clip extraction
                )
                total_duration += (span['end'] - span['start'])

        # 12. Update session stats
        self.quickclip_repo.update_session_stats(
            session_id=session_id,
            clips_count=len(resolved_spans),
            total_duration_sec=total_duration,
            full_video_saved=full_video_saved,
        )

        # 13. Save metadata JSON (with session ID in filename for uniqueness)
        metadata = {
            "session_id": session_id,
            "ytid": ytid,
            "url": full_url,
            "title": video.title if video else None,
            "description": description,
            "tags": tags,
            "created_at": datetime.now().isoformat(),
            "quality_profile": quality,
            "clips_only": clips_only,
            "clips": resolved_spans,
            "full_video": {
                "saved": full_video_saved,
            },
        }
        metadata_path = session_dir / f"{session_id}.metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        if description:
            desc_path = session_dir / f"{session_id}.description.txt"
            desc_path.write_text(description, encoding="utf-8")

        logger.info(f"QuickClip session {session_id} created successfully")

        return {
            "session_id": session_id,
            "session_dir": str(session_dir),
            "ytid": ytid,
            "clips_count": len(resolved_spans),
            "download_job_id": download_job.job_id if 'download_job' in locals() else None,
            "clip_job_id": clip_job.job_id if clip_job else None,
            "clips": resolved_spans,
        }

    def _enqueue_download_with_overrides(self, url: str, priority: int, quality: str, force: bool = False):
        """Helper to call DownloadService.enqueue_download safely with overrides."""
        overrides = self._build_download_overrides(quality, force=force)
        enqueue = getattr(self.download_service, "enqueue_download")
        try:
            params = inspect.signature(enqueue).parameters
        except (TypeError, ValueError):
            params = {}

        kwargs = {"url": url, "priority": priority}
        if "ytdlp_overrides" in params:
            kwargs["ytdlp_overrides"] = overrides
            try:
                return enqueue(**kwargs)
            except TypeError as exc:
                logger.warning(
                    "DownloadService.enqueue_download rejected ytdlp_overrides; retrying without it. (%s)",
                    exc,
                )
                kwargs.pop("ytdlp_overrides", None)
                return enqueue(**kwargs)

        logger.warning(
            "DownloadService.enqueue_download lacks ytdlp_overrides; downloading with default config."
        )
        return enqueue(**kwargs)

    def _build_download_overrides(self, quality: str, force: bool = False) -> Dict[str, Any]:
        """
        Translate QuickClip quality strings to yt-dlp overrides so we fetch video instead of audio-only.
        """
        normalized = (quality or "best").lower()
        overrides: Dict[str, Any] = {}
        if normalized == "audio-only":
            overrides["audio_only"] = True
            overrides.setdefault("format", "bestaudio/best")
        else:
            overrides["audio_only"] = False
            overrides["merge_output_format"] = "mp4"
            if normalized == "1080p":
                overrides["format"] = (
                    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo[height<=1080]+bestaudio"
                    "/best[ext=mp4]/best"
                )
            elif normalized == "720p":
                overrides["format"] = (
                    "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo[height<=720]+bestaudio"
                    "/best[ext=mp4]/best"
                )
            else:
                overrides["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

        # Force re-download if requested (skip archive checks, overwrite existing)
        if force:
            overrides["force_download"] = True
            overrides["no_overwrites"] = False  # Allow overwriting existing files

        return overrides

    def _extract_ytid(self, url_or_ytid: str) -> str:
        """Extract YouTube video ID from URL or return as-is if already an ID."""
        if "youtube.com/watch?v=" in url_or_ytid:
            return url_or_ytid.split("watch?v=")[-1].split("&")[0][:11]
        elif "youtu.be/" in url_or_ytid:
            return url_or_ytid.split("youtu.be/")[-1].split("?")[0][:11]
        else:
            # Assume it's already a video ID
            return url_or_ytid[:11]

    def _generate_session_id(self, ytid: str, custom_name: Optional[str] = None) -> str:
        """Generate a unique session ID."""
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%H%M%S")

        # Short hash from ytid + timestamp
        hash_input = f"{ytid}_{now.isoformat()}"
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:5]

        if custom_name:
            # Sanitize custom name
            safe_name = re.sub(r'[^a-z0-9_-]', '_', custom_name.lower())
            return f"quickclip_{date_str}_{time_str}_{safe_name}"
        else:
            return f"quickclip_{date_str}_{time_str}_{short_hash}"

    def _parse_span(self, span_str: str, index: int) -> Dict[str, Any]:
        """
        Parse a span string like "120-145" or "300-320:label".

        Returns dict with start, end, label, index.
        """
        # Check for label suffix
        if ":" in span_str:
            time_part, label = span_str.rsplit(":", 1)
        else:
            time_part = span_str
            label = None

        # Parse time range
        if "-" not in time_part:
            raise ValueError(f"Invalid span format: {span_str}. Expected START-END[:LABEL]")

        start_str, end_str = time_part.split("-", 1)
        start_str = start_str.strip()
        end_str = end_str.strip()

        return {
            "start_str": start_str,
            "end_str": end_str,
            "start": None,  # Will be resolved later
            "end": None,
            "label": label,
            "index": index,
        }

    def _resolve_spans(self, spans: List[Dict], video_duration: float) -> List[Dict]:
        """Resolve (start) and (end) placeholders to actual timestamps."""
        resolved = []
        for span in spans:
            start_str = span["start_str"]
            end_str = span["end_str"]

            # Resolve start
            if start_str == "(start)":
                start = 0.0
            else:
                start = float(start_str)

            # Resolve end
            if end_str == "(end)":
                end = video_duration
            else:
                end = float(end_str)

            if start >= end:
                raise ValueError(f"Start time ({start}) must be less than end time ({end})")

            resolved.append({
                "start": start,
                "end": end,
                "label": span["label"],
                "index": span["index"],
            })

        return resolved

    def _create_hits_manifest(
        self, manifest_path: Path, ytid: str, url: str, spans: List[Dict]
    ) -> None:
        """Create a hits.tsv manifest for the clipping service."""
        import csv

        with manifest_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter="\t")
            writer.writerow(["url", "start", "end", "label", "caption"])
            for span in spans:
                label = span.get("label") or f"clip_{span['index']}"
                writer.writerow([
                    url,
                    f"{span['start']:.3f}",
                    f"{span['end']:.3f}",
                    label,
                    label,
                ])

        logger.info(f"Created hits manifest: {manifest_path}")
