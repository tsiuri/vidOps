# vidops/services/quickclip.py

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from vidops.dal import VideoRepository, QuickClipRepository, FilesystemCache
from vidops.services.download import DownloadService
from vidops.services.clipping import ClippingService
from vidops.config import load_config

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
        priority: int = 10,
    ) -> Dict[str, Any]:
        """
        Create a QuickClip session with multiple clips.

        Args:
            url: YouTube URL or video ID
            spans: List of timestamp spans (e.g., ["120-145", "300-320:label"])
            description: User description of the session
            tags: Optional tags for searchability
            clips_only: Skip downloading full video
            quality: Quality profile (best, 1080p, 720p, audio-only)
            session_name: Custom session name (auto-generated if None)
            output_dir: Custom output directory
            force: Force re-download even if video exists
            priority: Job priority (default 10)

        Returns:
            Dict with session info and clip paths
        """
        # 1. Extract YTID from URL
        ytid = self._extract_ytid(url)
        full_url = url if url.startswith("http") else f"https://www.youtube.com/watch?v={ytid}"

        # 2. Generate session ID
        session_id = self._generate_session_id(ytid, session_name)

        # 3. Parse spans
        parsed_spans = []
        for i, span_str in enumerate(spans, 1):
            span_data = self._parse_span(span_str, i)
            parsed_spans.append(span_data)

        logger.info(f"Creating QuickClip session {session_id} for {ytid} with {len(parsed_spans)} clips")

        # 4. Prepare output directory (organized by ytid only)
        if not output_dir:
            output_dir = f"generated/quickclips/{ytid}"
        session_dir = Path(output_dir)
        session_dir.mkdir(parents=True, exist_ok=True)

        # 5. Check if video exists, create if needed
        video = self.video_repo.get(ytid)
        if not video:
            # Create minimal video record so we can proceed
            from vidops.models import Video
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

        # 6. Download video if needed
        full_video_saved = False
        if not media_asset or force:
            if not clips_only:
                logger.info(f"Downloading full video {ytid} (quality: {quality})")
                # Enqueue download job (this will be processed by worker)
                # For now, we'll use synchronous approach for simplicity
                download_job = self.download_service.enqueue_download(
                    url=full_url,
                    priority=priority,
                )
                # TODO: Wait for download to complete or implement async flow
                # For MVP, assume video will be downloaded
                full_video_saved = True
            else:
                logger.info(f"Clips-only mode: skipping full video download")

        # 7. Resolve timestamps with video metadata
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
        )

        # 11. Save clip records to database
        total_duration = 0.0
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
            "clip_job_id": clip_job.job_id,
            "clips": resolved_spans,
        }

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
