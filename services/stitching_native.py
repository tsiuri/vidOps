"""
Native Python video stitching implementation.

Replaces workspace.sh stitch bash scripts with pure Python FFmpeg wrappers.
Cross-platform compatible (Windows/Linux/macOS).
"""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional
import shutil

logger = logging.getLogger(__name__)


class VideoStitcher:
    """Pure Python video stitching using FFmpeg directly."""

    def __init__(self, batch_size: int = 100):
        """
        Initialize stitcher.

        Args:
            batch_size: Number of files to process per batch (default 100)
        """
        self.batch_size = batch_size

    def stitch_videos(
        self,
        input_dir: Path,
        output_file: Path,
        method: str = "batch",
        sort_method: str = "date_timestamp",
    ) -> None:
        """
        Stitch videos from input directory into single output file.

        Args:
            input_dir: Directory containing MP4 files to stitch
            output_file: Output file path
            method: Stitching method (batch, cfr, concat, filter)
            sort_method: How to sort clips (name, time, timestamp, date_timestamp)

        Raises:
            FileNotFoundError: If input directory is empty
            subprocess.CalledProcessError: If FFmpeg fails
        """
        logger.info(f"Stitching videos from: {input_dir}")
        logger.info(f"Output file: {output_file}")
        logger.info(f"Method: {method}, Sort: {sort_method}")

        # Dispatch to appropriate method
        if method == "batch":
            self._stitch_batched(input_dir, output_file, sort_method)
        elif method == "cfr":
            self._stitch_cfr(input_dir, output_file, sort_method)
        elif method == "concat":
            self._stitch_concat(input_dir, output_file, sort_method)
        elif method == "filter":
            self._stitch_filter(input_dir, output_file, sort_method)
        else:
            raise ValueError(f"Unknown stitch method: {method}")

        logger.info(f"Done! Output: {output_file}")

    def _stitch_batched(
        self, input_dir: Path, output_file: Path, sort_method: str
    ) -> None:
        """
        Batched stitching with re-encoding (most reliable, handles mixed formats).

        Strategy:
        1. Sort clips by specified method
        2. Split into batches of ~100 files
        3. Re-encode each batch with CFR 60fps
        4. Stream-copy merge batches into final output
        """
        # Find and sort MP4 files
        mp4_files = self._find_and_sort_mp4s(input_dir, sort_method)
        if not mp4_files:
            raise FileNotFoundError(f"No MP4 files found in {input_dir}")

        file_count = len(mp4_files)
        batch_count = (file_count + self.batch_size - 1) // self.batch_size
        logger.info(f"Found {file_count} MP4 files")
        logger.info(f"Will process in {batch_count} batches of {self.batch_size}")

        # Show sample files
        logger.info("First 5 files:")
        for f in mp4_files[:5]:
            logger.info(f"  {f.name}")
        if file_count > 5:
            logger.info("...")
            logger.info("Last 5 files:")
            for f in mp4_files[-5:]:
                logger.info(f"  {f.name}")

        # Create temp directory for batch processing
        with tempfile.TemporaryDirectory(prefix="vidops_stitch_") as temp_dir:
            batch_dir = Path(temp_dir)
            logger.info(f"Using temp dir: {batch_dir}")

            # Stage 1: Process batches with re-encoding
            logger.info("Stage 1: Concatenating batches...")
            batch_outputs: List[Path] = []

            for batch_idx in range(batch_count):
                start = batch_idx * self.batch_size
                end = min(start + self.batch_size, file_count)
                batch_files = mp4_files[start:end]

                batch_output = batch_dir / f"batch_{batch_idx}.mp4"
                logger.info(f"  Processing batch {batch_idx} ({len(batch_files)} files)...")

                self._concat_batch_with_reencode(batch_files, batch_output)
                batch_outputs.append(batch_output)

            # Stage 2: Merge batches with stream copy (fast)
            logger.info("Stage 2: Merging batches into final output...")
            self._concat_with_copy(batch_outputs, output_file)

    def _stitch_cfr(
        self, input_dir: Path, output_file: Path, sort_method: str
    ) -> None:
        """
        Single-pass CFR stitching (constant framerate, re-encode).

        Good for ensuring consistent framerate across all clips.
        """
        mp4_files = self._find_and_sort_mp4s(input_dir, sort_method)
        if not mp4_files:
            raise FileNotFoundError(f"No MP4 files found in {input_dir}")

        logger.info(f"Found {len(mp4_files)} MP4 files")
        logger.info("Using single-pass CFR stitching (re-encode)")

        # Create concat list
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            concat_file = Path(f.name)
            for video in mp4_files:
                # Escape single quotes for FFmpeg concat demuxer
                escaped = str(video.absolute()).replace("'", r"'\''")
                f.write(f"file '{escaped}'\n")

        try:
            # Single-pass concat with CFR re-encoding
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-stats",
                "-fflags", "+genpts",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-vf", "settb=AVTB,setpts=PTS-STARTPTS,fps=60",
                "-af", "aresample=async=1:first_pts=0",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                "-vsync", "cfr",
                "-r", "60",
                "-movflags", "+faststart",
                "-y",  # Overwrite output
                str(output_file),
            ]
            logger.info(f"Running: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
        finally:
            concat_file.unlink(missing_ok=True)

    def _stitch_concat(
        self, input_dir: Path, output_file: Path, sort_method: str
    ) -> None:
        """
        Fast concat stitching (stream copy, no re-encode).

        Only works if all clips have identical codecs/parameters.
        Fastest method but can fail with mixed formats.
        """
        mp4_files = self._find_and_sort_mp4s(input_dir, sort_method)
        if not mp4_files:
            raise FileNotFoundError(f"No MP4 files found in {input_dir}")

        logger.info(f"Found {len(mp4_files)} MP4 files")
        logger.info("Using fast concat (stream copy, no re-encode)")
        self._concat_with_copy(mp4_files, output_file)

    def _stitch_filter(
        self, input_dir: Path, output_file: Path, sort_method: str
    ) -> None:
        """
        Filter complex stitching (re-encode with complex filter graph).

        Slower but handles format mismatches better than simple concat.
        """
        mp4_files = self._find_and_sort_mp4s(input_dir, sort_method)
        if not mp4_files:
            raise FileNotFoundError(f"No MP4 files found in {input_dir}")

        logger.info(f"Found {len(mp4_files)} MP4 files")
        logger.info("Using filter complex stitching (re-encode)")

        # Build filter complex for concatenation
        num_inputs = len(mp4_files)
        filter_parts = [f"[{i}:v][{i}:a]" for i in range(num_inputs)]
        filter_complex = (
            f"{''.join(filter_parts)}"
            f"concat=n={num_inputs}:v=1:a=1[outv][outa]"
        )

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats"]

        # Add all inputs
        for video in mp4_files:
            cmd.extend(["-i", str(video)])

        # Add filter complex and output mapping
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-y",
            str(output_file),
        ])

        logger.info(f"Running FFmpeg with {num_inputs} inputs")
        subprocess.run(cmd, check=True)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _find_and_sort_mp4s(
        self, directory: Path, sort_method: str
    ) -> List[Path]:
        """
        Find all MP4 files in directory and sort by specified method.

        Args:
            directory: Directory to search
            sort_method: One of: name, time, timestamp, date_timestamp

        Returns:
            Sorted list of MP4 file paths
        """
        mp4_files = list(directory.glob("*.mp4"))

        if sort_method == "name":
            # Sort by filename
            return sorted(mp4_files, key=lambda p: p.name)

        elif sort_method == "time":
            # Sort by modification time
            return sorted(mp4_files, key=lambda p: p.stat().st_mtime)

        elif sort_method == "timestamp":
            # Sort by timestamp in filename (e.g., _12345.67-67890.12.mp4)
            def extract_timestamp(path: Path) -> float:
                import re
                match = re.search(r"_(\d+\.\d+)-\d+\.\d+\.mp4$", path.name)
                return float(match.group(1)) if match else 0.0

            return sorted(mp4_files, key=extract_timestamp)

        elif sort_method == "date_timestamp":
            # Sort by date+timestamp using sort_clips.py utility
            # Fall back to timestamp method if utility not available
            tool_root = Path(__file__).resolve().parents[1]
            sort_script = tool_root / "scripts" / "utilities" / "sort_clips.py"

            if sort_script.exists():
                try:
                    # Use sort_clips.py
                    import subprocess
                    file_list = "\n".join(str(f.absolute()) for f in mp4_files)
                    result = subprocess.run(
                        ["python3", str(sort_script)],
                        input=file_list,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    sorted_paths = [
                        Path(line.strip())
                        for line in result.stdout.strip().split("\n")
                        if line.strip()
                    ]
                    return sorted_paths
                except Exception as e:
                    logger.warning(
                        f"sort_clips.py failed, falling back to timestamp sort: {e}"
                    )

            # Fallback to timestamp method
            return self._find_and_sort_mp4s(directory, "timestamp")

        else:
            raise ValueError(f"Unknown sort method: {sort_method}")

    def _concat_batch_with_reencode(
        self, files: List[Path], output: Path
    ) -> None:
        """
        Concatenate files with re-encoding to CFR 60fps.

        Args:
            files: List of input files
            output: Output file path
        """
        # Create concat file list
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            concat_file = Path(f.name)
            for video in files:
                escaped = str(video.absolute()).replace("'", r"'\''")
                f.write(f"file '{escaped}'\n")

        try:
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-stats",
                "-fflags", "+genpts",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-vf", "settb=AVTB,setpts=PTS-STARTPTS,fps=60",
                "-af", "aresample=async=1:first_pts=0",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                "-vsync", "cfr",
                "-r", "60",
                "-movflags", "+faststart",
                "-y",
                str(output),
            ]
            subprocess.run(cmd, check=True, capture_output=True)
        finally:
            concat_file.unlink(missing_ok=True)

    def _concat_with_copy(self, files: List[Path], output: Path) -> None:
        """
        Concatenate files with stream copy (no re-encode, fast).

        Args:
            files: List of input files
            output: Output file path
        """
        # Create concat file list
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            concat_file = Path(f.name)
            for video in files:
                escaped = str(video.absolute()).replace("'", r"'\''")
                f.write(f"file '{escaped}'\n")

        try:
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "info",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                "-y",
                str(output),
            ]
            subprocess.run(cmd, check=True)
        finally:
            concat_file.unlink(missing_ok=True)
