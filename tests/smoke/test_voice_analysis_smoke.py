"""
Smoke tests for voice filtering and analysis services/workers.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from vidops.dal import JobRepository, TranscriptRepository, WordRepository
from vidops.models import Transcript, Word
from vidops.services import get_voice_service, get_analysis_service

from tests.smoke.helpers import (
    SMOKE_MEDIA_ROOT,
    cleanup_voice_results,
    ensure_video_stub,
    generate_sample_audio,
    new_ytid,
    reset_smoke_state,
    run_worker_with_logs,
)

pytestmark = pytest.mark.smoke


def test_voice_and_analysis_flow(smoke_env, smoke_storage_root):
    ytid = new_ytid("VOI")
    ensure_video_stub(ytid)
    reset_smoke_state([ytid])
    cleanup_voice_results([ytid], smoke_storage_root)

    clip_dir = (SMOKE_MEDIA_ROOT / ytid).resolve()
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip_path = generate_sample_audio(clip_dir / f"{ytid}_clip.wav")
    reference_path = generate_sample_audio(SMOKE_MEDIA_ROOT / f"{ytid}_reference.wav")

    voice_service = get_voice_service()
    voice_job = voice_service.enqueue_job(
        ytid=ytid,
        clips_path=str(clip_dir),
        reference_paths=[str(reference_path)],
        threshold=0.1,
    )

    env = smoke_env.copy()
    env["VIDOPS_WORKER_MAX_JOBS"] = "1"
    worker_cmd = [sys.executable, "vo_cli.py", "worker", "start", "voice"]
    voice_run = run_worker_with_logs("voice_filter", "worker", worker_cmd, env)
    assert voice_run.returncode == 0, f"Voice worker failed:\n{voice_run.stdout}\n{voice_run.stderr}"

    job_repo = JobRepository()
    stored_voice_job = job_repo.get(voice_job.job_id)
    assert stored_voice_job and stored_voice_job.result
    results_path = stored_voice_job.result.get("results_path")
    matches_path = stored_voice_job.result.get("matches_path")
    assert results_path and matches_path
    assert (smoke_storage_root / results_path).exists()
    assert (smoke_storage_root / matches_path).exists()

    # Bootstrap transcript + words for analysis
    transcript_repo = TranscriptRepository()
    word_repo = WordRepository()
    transcript_rel = Path("transcripts") / f"{ytid}_words_whisper_tiny.vtt"
    central_transcript = smoke_storage_root / transcript_rel
    central_transcript.parent.mkdir(parents=True, exist_ok=True)
    central_transcript.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n", encoding="utf-8")

    transcript_repo.upsert(
        Transcript(
            ytid=ytid,
            kind="words_whisper_tiny",
            lang="en",
            path=str(transcript_rel),
            word_count=2,
            segment_count=1,
        )
    )
    words = [
        Word(ytid=ytid, source="whisper-tiny", word="Hello", start_sec=0.0, end_sec=0.5, confidence=-0.1, idx=0),
        Word(ytid=ytid, source="whisper-tiny", word="World", start_sec=0.5, end_sec=1.0, confidence=-0.2, idx=1),
    ]
    word_repo.bulk_insert(words)

    analysis_service = get_analysis_service()
    analysis_job = analysis_service.enqueue_analysis_job(
        ytid=ytid,
        transcript_kind="words_whisper_tiny",
        analysis_model="smoke-summary",
    )

    analysis_cmd = [sys.executable, "vo_cli.py", "worker", "start", "analysis"]
    analysis_run = run_worker_with_logs("analysis", "worker", analysis_cmd, env)
    assert analysis_run.returncode == 0, f"Analysis worker failed:\n{analysis_run.stdout}\n{analysis_run.stderr}"

    stored_analysis_job = job_repo.get(analysis_job.job_id)
    assert stored_analysis_job and stored_analysis_job.result
    analysis_path = stored_analysis_job.result.get("analysis_path")
    assert analysis_path
    analysis_file = smoke_storage_root / analysis_path
    assert analysis_file.exists()

    payload = json.loads(analysis_file.read_text())
    assert payload["ytid"] == ytid
    assert payload["top_terms"]
