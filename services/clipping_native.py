"""
Native clipping helpers that mirror the legacy clips.sh behavior (cut-local/cut-net)
without relying on bash. Outputs and filenames match the legacy patterns so downstream
consumers remain unchanged.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class ClipRow:
    url: str
    start: float
    end: float
    label: str
    caption: str


class NativeClipper:
    """Pure-Python clipping implementation with legacy-compatible outputs."""

    def __init__(self) -> None:
        self.clip_container = os.environ.get("CLIP_CONTAINER", "opus")
        self.clip_audio_br = os.environ.get("CLIP_AUDIO_BR", "96k")
        self.pad_start = float(os.environ.get("PAD_START", "0") or 0)
        self.pad_end = float(os.environ.get("PAD_END", "0") or 0)
        self.name_max_title = int(os.environ.get("NAME_MAX_TITLE", "70") or 70)
        self.overwrite_sections = os.environ.get("OVERWRITE_SECTIONS", "0") == "1"
        self.allow_any_format = os.environ.get("ALLOW_ANY_FORMAT", "0") == "1"
        self.yt_sleep_requests = os.environ.get("YT_SLEEP_REQUESTS", "0.20")
        self.yt_sleep_interval = os.environ.get("YT_SLEEP_INTERVAL", "0.20")
        self.yt_max_sleep_interval = os.environ.get("YT_MAX_SLEEP_INTERVAL", "1.5")
        self.yt_retries = os.environ.get("YT_RETRIES", "15")
        self.yt_frag_retries = os.environ.get("YT_FRAG_RETRIES", "20")
        self.yt_extractor_retries = os.environ.get("YT_EXTRACTOR_RETRIES", "10")

    def cut_local(
        self,
        rows: Iterable[ClipRow],
        output_dir: Path,
        staged_media: Dict[str, Path],
    ) -> List[Path]:
        """Cut clips from local media using ffmpeg."""
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs: List[Path] = []
        for row in rows:
            ytid = self._extract_ytid(row.url)
            if not ytid:
                continue
            media_path = staged_media.get(ytid)
            if not media_path or not media_path.exists():
                continue

            start = max(0.0, float(row.start) - self.pad_start)
            end = max(start, float(row.end) + self.pad_end)
            duration = max(0.1, end - start)

            safe_label = self._safe_label(row.label or "hit")
            title_tail = self._title_tail(media_path.name)
            filename = f"{ytid}_{safe_label}_{title_tail}.{self.clip_container}"
            out_path = output_dir / filename

            self._run_ffmpeg_clip(
                source=media_path,
                dest=out_path,
                start=start,
                duration=duration,
                container=self.clip_container,
                audio_br=self.clip_audio_br,
            )

            # Copy legacy src.json sidecar if present
            src_json = media_path.with_suffix("").with_suffix(".src.json") if media_path.suffix else media_path.with_suffix(".src.json")
            if src_json.exists():
                try:
                    shutil.copy2(src_json, out_path.with_suffix(".src.json"))
                except Exception:
                    pass

            outputs.append(out_path)
        return outputs

    def cut_net(
        self,
        rows: Iterable[ClipRow],
        output_dir: Path,
    ) -> List[Path]:
        """Download clip sections from the network using yt-dlp with the legacy template."""
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs: List[Path] = []
        for row in rows:
            url = row.url
            ytid = self._extract_ytid(url)
            if not ytid:
                continue

            start = max(0.0, float(row.start) - self.pad_start)
            end = max(start, float(row.end) + self.pad_end)
            start_fmt = f"{start:06.2f}"
            end_fmt = f"{end:06.2f}"

            template = str(
                output_dir
                / f"%(id)s_%(title).{self.name_max_title}B_%(section_start)06.2f-%(section_end)06.2f.%(ext)s"
            )
            fmt = (
                "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
                if not self.allow_any_format
                else "b/bv*+ba"
            )
            cmd = [
                "yt-dlp",
                "--ignore-config",
                "-4",
                "--no-playlist",
                "--geo-bypass",
                "--concurrent-fragments",
                "1",
                "--force-keyframes-at-cuts",
                "--sleep-requests",
                str(self.yt_sleep_requests),
                "--sleep-interval",
                str(self.yt_sleep_interval),
                "--max-sleep-interval",
                str(self.yt_max_sleep_interval),
                "--retries",
                str(self.yt_retries),
                "--fragment-retries",
                str(self.yt_frag_retries),
                "--extractor-retries",
                str(self.yt_extractor_retries),
                "--ignore-no-formats-error",
                "-o",
                template,
                "--download-sections",
                f"*{start_fmt}-{end_fmt}",
                "-f",
                fmt,
                "--merge-output-format",
                "mp4",
            ]
            if self.overwrite_sections:
                cmd.append("--force-overwrites")
            else:
                cmd.append("--no-overwrites")

            # Run yt-dlp
            result = subprocess.run(cmd, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"yt-dlp failed for {url} (exit {result.returncode})")

            # Collect outputs matching the section pattern
            pattern = f"{ytid}_*_{start_fmt}-{end_fmt}.*"
            matches = list(output_dir.glob(pattern))
            outputs.extend(matches)
        return outputs

    def _run_ffmpeg_clip(
        self,
        source: Path,
        dest: Path,
        start: float,
        duration: float,
        container: str,
        audio_br: str,
    ) -> None:
        cmd: List[str]
        if container == "mp4":
            cmd = [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-map_metadata",
                "0",
                "-y",
                str(dest),
            ]
        elif container == "mp3":
            cmd = [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.3f}",
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                audio_br,
                "-map_metadata",
                "0",
                "-y",
                str(dest),
            ]
        elif container == "mka":
            cmd = [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.3f}",
                "-vn",
                "-c:a",
                "copy",
                "-map_metadata",
                "0",
                "-y",
                str(dest),
            ]
        else:  # opus default
            cmd = [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.3f}",
                "-vn",
                "-c:a",
                "libopus",
                "-b:a",
                audio_br,
                "-map_metadata",
                "0",
                "-y",
                str(dest),
            ]
        subprocess.run(cmd, check=True)

    def _extract_ytid(self, url: str) -> Optional[str]:
        if not url:
            return None
        m = re.search(r"v=([A-Za-z0-9_-]{11})", url)
        if m:
            return m.group(1)
        m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", url)
        if m:
            return m.group(1)
        # Already bare id?
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
            return url
        return None

    def _safe_label(self, label: str) -> str:
        cleaned = re.sub(r"[^-A-Za-z0-9_. ]+", "", label or "")
        cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
        return cleaned or "clip"

    def _title_tail(self, name: str) -> str:
        # Match legacy behavior: truncate basename to NAME_MAX_TITLE characters
        base = Path(name).name
        return base[: self.name_max_title]
