from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import time
import random
import string
import wave
from pathlib import Path
from typing import List, Sequence

from vidops.db import check_connection, get_connection
from vidops.dal import VideoRepository
from vidops.models import Video

SMOKE_MEDIA_ROOT = Path("tmp/smoke_media")
SMOKE_LOG_ROOT = Path("logs/smoke")


def ensure_dirs() -> None:
    SMOKE_MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    SMOKE_LOG_ROOT.mkdir(parents=True, exist_ok=True)


YOUTUBE_ALPHABET = string.ascii_letters + string.digits + "-_"


def new_ytid(prefix: str) -> str:
    """
    Generate a pseudo YouTube ID (11 characters) with the provided prefix.
    """
    prefix = prefix[:11]
    remaining = 11 - len(prefix)
    suffix = "".join(random.choice(YOUTUBE_ALPHABET) for _ in range(max(0, remaining)))
    return (prefix + suffix)


def generate_sample_audio(path: Path, duration_sec: float = 2.0, base_freq: int = 300) -> Path:
    ensure_dirs()
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    amplitude = 16000
    total_samples = int(duration_sec * sample_rate)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        frames = bytearray()
        for i in range(total_samples):
            t = i / sample_rate
            sample = math.sin(2 * math.pi * base_freq * t) + 0.5 * math.sin(2 * math.pi * (base_freq * 2) * t)
            value = int(amplitude * sample)
            frames.extend(struct.pack("<h", max(-32768, min(32767, value))))

        wav_file.writeframes(frames)

    return path


def reset_smoke_state(ytids: Sequence[str]) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE transcribe_job_log,
                         transcribe_retry_queue,
                         transcribe_fragments,
                         transcribe_jobs,
                         transcribe_workers;
                """
            )
            if ytids:
                cur.execute(
                    "DELETE FROM jobs WHERE ytid = ANY(%s) AND job_type IN ('analysis', 'voice', 'download', 'diarization')",
                    (list(ytids),),
                )
                cur.execute("DELETE FROM words WHERE ytid = ANY(%s)", (list(ytids),))
                cur.execute("DELETE FROM transcripts WHERE ytid = ANY(%s)", (list(ytids),))
                cur.execute("DELETE FROM assets WHERE ytid = ANY(%s)", (list(ytids),))

    generated_root = Path("generated")
    if generated_root.exists():
        for ytid in ytids:
            for artifact in generated_root.glob(f"{ytid}*"):
                if artifact.is_dir():
                    shutil.rmtree(artifact, ignore_errors=True)
                else:
                    artifact.unlink(missing_ok=True)


def cleanup_voice_results(ytids: Sequence[str], storage_root: Path) -> None:
    for ytid in ytids:
        voice_dir = storage_root / "results" / "voice_filter" / ytid
        if voice_dir.exists():
            shutil.rmtree(voice_dir, ignore_errors=True)


def cleanup_diarization_results(ytids: Sequence[str], storage_root: Path) -> None:
    for ytid in ytids:
        diar_root = storage_root / "generated" / "diarization_resemblyzer" / ytid
        if diar_root.exists():
            shutil.rmtree(diar_root, ignore_errors=True)


def run_worker_with_logs(test_name: str, label: str, cmd: List[str], env: dict) -> subprocess.CompletedProcess:
    ensure_dirs()
    log_dir = SMOKE_LOG_ROOT / test_name
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{label}_stdout.log"
    stderr_path = log_dir / f"{label}_stderr.log"

    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)

    stdout_path.write_text(result.stdout)
    stderr_path.write_text(result.stderr)
    return result


def db_available() -> bool:
    try:
        return check_connection(max_retries=1, delay_sec=1)
    except Exception:
        return False


def ensure_video_stub(ytid: str) -> None:
    repo = VideoRepository()
    video = Video(
        ytid=ytid,
        url=f"https://www.youtube.com/watch?v={ytid}",
        title=f"Smoke Test {ytid}",
    )
    repo.upsert(video)
