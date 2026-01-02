"""
Native Python subtitle operations.

Replaces workspace.sh dl-subs and convert-captions bash scripts with pure Python.
Cross-platform compatible (Windows/Linux/macOS).
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any
import csv

logger = logging.getLogger(__name__)


class VTTConverter:
    """Convert VTT subtitle files to words.yt.tsv format."""

    @staticmethod
    def time_to_sec(time_str: str) -> float:
        """
        Convert VTT timestamp to seconds.

        Args:
            time_str: Timestamp like "00:01:23.456" or "00:01:23,456"

        Returns:
            Time in seconds as float
        """
        time_str = time_str.strip().replace(',', '.')
        match = re.match(r"^(\d{2}):(\d{2}):(\d{2})(?:[\.,](\d{1,3}))?", time_str)
        if not match:
            return 0.0

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        milliseconds = match.group(4)

        total_seconds = hours * 3600 + minutes * 60 + seconds
        if milliseconds is not None:
            # Pad to 3 digits and convert to decimal
            total_seconds += float(f"0.{milliseconds:0<3}")

        return float(total_seconds)

    @staticmethod
    def parse_vtt(vtt_path: Path) -> List[Dict[str, Any]]:
        """
        Parse VTT file into segments.

        Args:
            vtt_path: Path to VTT file

        Returns:
            List of segment dictionaries with start, end, text, confidence
        """
        lines = vtt_path.read_text(encoding='utf-8', errors='ignore').splitlines()
        segments = []
        i = 0

        while i < len(lines):
            line = lines[i].strip()
            confidence = None

            # Optional NOTE confidence before timestamp
            if line.startswith('NOTE Confidence:'):
                try:
                    confidence = float(line.split(':', 1)[1].strip())
                except Exception:
                    confidence = None
                i += 1
                if i >= len(lines):
                    break
                line = lines[i].strip()

            if '-->' in line:
                # Parse timestamp line
                # Format: "00:00:00.000 --> 00:00:02.000 align:start position:0%"
                left, right = line.split('-->', 1)
                start_str = left.strip().split()[0]
                end_str = right.strip().split()[0]
                start = VTTConverter.time_to_sec(start_str)
                end = VTTConverter.time_to_sec(end_str)

                # Capture subsequent non-empty text lines
                i += 1
                text_lines = []
                while i < len(lines) and lines[i].strip():
                    text_lines.append(lines[i].rstrip())
                    i += 1

                # Join with space, collapse multiple spaces
                raw = re.sub(r"\s+", " ", " ".join(text_lines)).strip()
                # Strip WebVTT inline tags like <c>...</c>, <00:00.000> and any <...>
                text = re.sub(r"<[^>]+>", "", raw)

                segments.append({
                    'start': start,
                    'end': end if end >= start else start,
                    'text': text,
                    'confidence': confidence if confidence is not None else 0.0,
                })

            i += 1

        return segments

    @staticmethod
    def write_words_tsv(output_path: Path, segments: List[Dict[str, Any]]) -> None:
        """
        Write parsed segments to words.yt.tsv format.

        Args:
            output_path: Path to write TSV file
            segments: List of segment dictionaries
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open('w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, delimiter='\t')
            # Header
            writer.writerow(['start', 'end', 'word', 'seg', 'confidence', 'retried'])

            for seg_idx, seg in enumerate(segments):
                text = seg['text']
                if not text:
                    continue

                # Split words on whitespace; preserve punctuation tokens as-is
                tokens = re.findall(r"\S+", text)
                if not tokens:
                    continue

                # Calculate time distribution for words
                duration = max(0.0, float(seg['end']) - float(seg['start']))
                step = duration / len(tokens) if len(tokens) > 0 else 0.0

                for token_idx, token in enumerate(tokens):
                    word_start = float(seg['start']) + step * token_idx
                    word_end = word_start + (step if step > 0 else 0.0)

                    # Write: start, end, word, seg, confidence, retried
                    writer.writerow([
                        f"{word_start:.3f}",
                        f"{word_end:.3f}",
                        token,
                        seg_idx,
                        f"{float(seg['confidence']):.3f}",
                        0  # retried flag
                    ])

    def convert_vtt_to_words(
        self,
        vtt_path: Path,
        output_path: Optional[Path] = None,
        overwrite: bool = False
    ) -> Path:
        """
        Convert VTT file to words.yt.tsv format.

        Args:
            vtt_path: Input VTT file path
            output_path: Output TSV file path (optional, auto-generated if not provided)
            overwrite: Whether to overwrite existing output file

        Returns:
            Path to the output TSV file

        Raises:
            FileExistsError: If output exists and overwrite=False
            FileNotFoundError: If input VTT doesn't exist
        """
        if not vtt_path.exists():
            raise FileNotFoundError(f"VTT file not found: {vtt_path}")

        # Auto-generate output path if not provided
        if output_path is None:
            # Strip .transcript.en.vtt or .vtt suffix
            base = vtt_path.name
            if base.endswith(".transcript.en.vtt"):
                base = base[:-len(".transcript.en.vtt")]
            elif base.endswith(".vtt"):
                base = base[:-len(".vtt")]
            output_path = vtt_path.parent / f"{base}.words.yt.tsv"

        if output_path.exists() and not overwrite:
            logger.info(f"Skip (exists): {output_path}")
            return output_path

        logger.info(f"Converting VTT: {vtt_path} -> {output_path}")
        segments = self.parse_vtt(vtt_path)
        self.write_words_tsv(output_path, segments)
        logger.info(f"Wrote: {output_path}")

        return output_path


class SubtitleDownloader:
    """Download subtitles using yt-dlp."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize subtitle downloader.

        Args:
            config: Optional download configuration (from config.yaml)
        """
        self.config = config or {}

    def download_subtitle(
        self,
        url: str,
        output_dir: Path,
        lang: str = "en",
        subtitle_format: str = "vtt",
        auto_subs: bool = True
    ) -> Optional[Path]:
        """
        Download subtitles for a YouTube video.

        Args:
            url: YouTube video URL
            output_dir: Directory to save subtitles
            lang: Language code (default: "en")
            subtitle_format: Subtitle format (vtt, srt, etc.)
            auto_subs: Whether to download auto-generated subtitles

        Returns:
            Path to downloaded subtitle file, or None if download failed

        Raises:
            subprocess.CalledProcessError: If yt-dlp fails
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build yt-dlp command to match legacy filename pattern: <id>__<title>.transcript.<lang>.<ext>
        output_template = "%(id)s__%(title)s.%(ext)s"
        cmd = [
            "yt-dlp",
            "--skip-download",  # Don't download video
            "--write-subs",     # Download subtitles
            "--convert-subs", subtitle_format,
            "--sub-format", subtitle_format,
            "--output", str(output_dir / output_template),
        ]

        if auto_subs:
            cmd.append("--write-auto-subs")

        # Force language selection (legacy flow assumed en.vtt by default)
        cmd.extend(["--sub-langs", lang])

        # Add cookies if configured
        cookies_browser = self.config.get("cookies_browser")
        if cookies_browser:
            cmd.extend(["--cookies-from-browser", cookies_browser])

        # Add retries
        retries = self.config.get("retries", 5)
        cmd.extend(["--retries", str(retries)])

        # Add rate limiting if configured
        sleep_requests = self.config.get("sleep_requests", 0)
        if sleep_requests > 0:
            cmd.extend(["--sleep-requests", str(sleep_requests)])

        cmd.append(url)

        logger.info(f"Downloading subtitles: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            cwd=output_dir,
            capture_output=True,
            text=True,
            check=True
        )

        # Find the downloaded subtitle file (prefer transcript suffix to match legacy)
        subtitle_files = list(output_dir.glob(f"*transcript.{lang}.{subtitle_format}"))
        if not subtitle_files:
            subtitle_files = list(output_dir.glob(f"*.{lang}.{subtitle_format}"))

        if subtitle_files:
            # Return the newest file
            subtitle_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            logger.info(f"Downloaded subtitle: {subtitle_files[0]}")
            return subtitle_files[0]

        logger.warning(f"No subtitle file found after download for {url}")
        return None
