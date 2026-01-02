"""
Native voice filtering runner (replaces workspace.sh voice) with legacy-compatible outputs.
Produces voice_analysis.json and hasan_clips.txt under the provided output directory.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)


def run_voice_filter(
    clips_dir: Path,
    reference_paths: List[Path],
    output_dir: Path,
    threshold: float,
    mode: str = "chunked",
) -> Tuple[Path, Path]:
    """
    Run voice filtering using the bundled Python scripts (chunked/parallel/simple).

    Args:
        clips_dir: Directory containing clips to check.
        reference_paths: List of reference audio clips.
        output_dir: Where to write outputs (voice_analysis.json, hasan_clips.txt).
        threshold: Similarity threshold.
        mode: One of chunked|parallel|simple.

    Returns:
        (voice_analysis.json path, hasan_clips.txt path)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    script_map = {
        "chunked": "filter_voice_parallel_chunked.py",
        "parallel": "filter_voice_parallel.py",
        "simple": "filter_voice.py",
    }
    script_name = script_map.get(mode, "filter_voice_parallel_chunked.py")

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "voice_filtering" / script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Voice filter script not found: {script_path}")

    cmd = [
        "python",
        str(script_path),
        str(clips_dir),
        *[str(p) for p in reference_paths],
        str(output_dir),
        str(threshold),
    ]
    logger.info("Running native voice filter (%s): %s", mode, " ".join(cmd))
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Voice filter failed (exit {result.returncode})")

    results_path = output_dir / "voice_analysis.json"
    matches_path = output_dir / "hasan_clips.txt"
    if not results_path.exists():
        raise FileNotFoundError(f"voice_analysis.json missing at {results_path}")
    if not matches_path.exists():
        # Backfill empty matches file for consistency
        matches_path.write_text("", encoding="utf-8")
    return results_path, matches_path
