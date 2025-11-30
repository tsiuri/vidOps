"""
Smoke test for the transcription queue worker.

Flow:
1. Generate a tiny WAV clip.
2. Enqueue it via `TranscriptionQueue`.
3. Run the CPU DB worker as a subprocess (USE_DB_QUEUE=1).
4. Verify queue status + transcript/word ingestion.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg2.extras
import pytest

from scripts.transcription.db_queue import TranscriptionQueue
from vidops.db import get_connection

from tests.smoke.helpers import (
    generate_sample_audio,
    new_ytid,
    reset_smoke_state,
    run_worker_with_logs,
    SMOKE_MEDIA_ROOT,
)

pytestmark = pytest.mark.smoke


def test_db_queue_transcription_flow(smoke_env):
    """
    End-to-end transcription flow using the DB queue worker.
    """
    ytid = new_ytid("TRN")
    media_path = generate_sample_audio(SMOKE_MEDIA_ROOT / f"{ytid}__sample.wav")
    reset_smoke_state([ytid])

    queue = TranscriptionQueue()
    job_id = queue.enqueue(
        media_path=str(media_path),
        model="tiny",
        language="en",
        output_format="vtt",
    )
    job_record = queue.get_job(job_id)
    assert job_record and job_record["status"] == "pending"

    env = smoke_env.copy()
    env["USE_DB_QUEUE"] = "1"
    env["WORKER_MAX_JOBS"] = "1"
    env["CPU_THREADS"] = "2"

    worker_cmd = [
        sys.executable,
        "scripts/transcription/transcribe_worker_cpu_db.py",
        "--model",
        "tiny",
        "--language",
        "en",
    ]
    result = run_worker_with_logs("transcription", "worker", worker_cmd, env)
    assert result.returncode == 0, f"Worker failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    time.sleep(1)
    completed_job = queue.get_job(job_id)
    assert completed_job and completed_job["status"] == "completed"
    assert completed_job["output_vtt"]
    assert completed_job["output_words_tsv"]

    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT source FROM words WHERE ytid = %s LIMIT 1", (ytid,))
            word_row = cur.fetchone()
            if word_row:
                assert word_row["source"] == "whisper-tiny"

            cur.execute(
                "SELECT kind FROM transcripts WHERE ytid = %s AND kind = %s",
                (ytid, "words_whisper_tiny"),
            )
            transcript_row = cur.fetchone()
            assert transcript_row and transcript_row["kind"] == "words_whisper_tiny"

    assert Path(completed_job["output_words_tsv"]).exists()
    assert Path(completed_job["output_vtt"]).exists()
