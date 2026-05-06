"""Tests for services.analysis_engine.AnalysisEngine."""
from __future__ import annotations

from unittest.mock import patch

from services.analysis_engine import AnalysisEngine
from scripts.analysis.analysis_task import AnalysisTask, TaskStatus


def _make_task(
    *,
    pass_id: str,
    chunk_id: int = 0,
    chunk_text: str = "hello world",
    job_id: str = "test_yt:test_cfg:123",
    task_id: int = 1,
    chunk_metadata: dict | None = None,
) -> AnalysisTask:
    return AnalysisTask(
        task_id=task_id,
        job_id=job_id,
        ytid="test_yt",
        chunk_id=chunk_id,
        pass_id=pass_id,
        chunk_text=chunk_text,
        chunk_metadata=chunk_metadata or {"config_id": "test_cfg", "total_chunks": 1},
        status=TaskStatus.CLAIMED,
        model_profile_id=1,
    )


def test_engine_construct_and_shutdown(analysis_db_test, mock_ollama):
    """Engine constructs cleanly, connects to DB, shutdown is idempotent."""
    with patch("services.analysis_engine.OllamaAnalyzer", return_value=mock_ollama):
        engine = AnalysisEngine(
            model_name="qwen2.5:7b-instruct",
            model_url="http://localhost:11434",
            model_profile_id=1,
        )
        assert engine.db is not None
        assert engine.task_repo is not None
        assert engine.results_repo is not None
        engine.shutdown()
        engine.shutdown()  # idempotent


def test_run_task_chunk_analysis_ok(engine_no_db):
    """run_task on a chunk_analysis task returns ok with the analyzer's payload. No DB."""
    task = _make_task(pass_id="chunk_analysis", task_id=999)
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok"
    assert result.pass_id == "chunk_analysis"
    assert result.payload is not None
    assert result.payload.get("status") == "completed"
    analysis = result.payload.get("analysis") or {}
    assert analysis.get("summary") == "stub summary"
    assert result.duration_s >= 0.0
    assert result.error_category is None


def test_run_task_sentiment_ok(engine_no_db):
    """run_task on a sentiment_pass task returns ok with sentiment label."""
    task = _make_task(
        pass_id="sentiment_pass",
        task_id=998,
        chunk_text="great news today",
    )
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok"
    assert result.pass_id == "sentiment_pass"
    assert result.payload is not None
    assert "sentiment" in result.payload


def test_run_task_categories_ok(engine_no_db):
    """run_task on a categories_pass task returns ok with categories list."""
    task = _make_task(
        pass_id="categories_pass",
        task_id=997,
        chunk_text="The senate voted on the inflation policy.",
    )
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok"
    assert result.pass_id == "categories_pass"
    assert result.payload is not None
    assert "categories" in result.payload
    assert isinstance(result.payload["categories"], list)


def test_run_task_subchunks_ok(engine_no_db):
    """run_task on a subchunks task returns ok with subchunks list."""
    task = _make_task(
        pass_id="subchunks",
        task_id=996,
        chunk_text="This is the first sentence. This is the second one. And a third.",
    )
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "ok"
    assert result.pass_id == "subchunks"
    assert result.payload is not None
    assert "subchunks" in result.payload
    assert isinstance(result.payload["subchunks"], list)
    assert len(result.payload["subchunks"]) >= 1


def test_run_task_data_error_classified(engine_no_db, mock_ollama):
    """KeyError / ValueError get classified as 'data' error_category."""
    mock_ollama.analyze_chunk.side_effect = KeyError("chunk_text")
    task = _make_task(pass_id="chunk_analysis", task_id=995)
    result = engine_no_db.run_task(task, worker_id="test-worker-1")
    assert result.status == "failed"
    assert result.error_category == "data"
    assert "chunk_text" in (result.error_message or "")
