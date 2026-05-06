"""Fixtures for AnalysisEngine tests. Mirrors the *_test table pattern from tests/dal/conftest.py."""
from __future__ import annotations

from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

from dal.analysis_task_repository import AnalysisDatabase


@pytest.fixture(scope="session")
def analysis_db_test() -> Iterator[AnalysisDatabase]:
    """Connect to live `transcripts` Postgres; create *_test mirror tables once per session."""
    db = AnalysisDatabase()
    db.connect()
    cur = db.conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS analysis_tasks_test (LIKE analysis_tasks INCLUDING ALL)")
    cur.execute("CREATE TABLE IF NOT EXISTS analysis_results_test (LIKE analysis_results INCLUDING ALL)")
    cur.execute("CREATE TABLE IF NOT EXISTS drills_test (LIKE drills INCLUDING ALL)")
    db.conn.commit()
    yield db
    db.disconnect()


@pytest.fixture(autouse=True)
def truncate_test_tables(analysis_db_test: AnalysisDatabase) -> None:
    """Truncate the mirror tables before each test."""
    cur = analysis_db_test.conn.cursor()
    cur.execute("TRUNCATE analysis_tasks_test, analysis_results_test, drills_test")
    analysis_db_test.conn.commit()


@pytest.fixture
def mock_ollama() -> MagicMock:
    """A mocked OllamaAnalyzer with the methods AnalysisEngine touches."""
    m = MagicMock(name="OllamaAnalyzer")
    m.analyze_chunk.return_value = {
        "summary": "stub summary",
        "key_points": ["a", "b"],
        "quotes": [],
        "topics": ["politics"],
        "people": [],
        "sentiment": "neutral",
    }
    m.summarize.return_value = "stub job summary"
    m.summarize_speaker.return_value = "stub speaker summary"
    m.options = {"num_ctx": 8192}
    return m


@pytest.fixture
def engine_no_db(mock_ollama):
    """An AnalysisEngine with OllamaAnalyzer mocked and DB-reads stubbed.

    Tests using this fixture should NOT seed analysis_tasks or query the
    DB; they should construct AnalysisTask objects in-memory and pass
    them directly to engine.run_task().
    """
    from services.analysis_engine import AnalysisEngine, JobContext
    from scripts.analysis.analysis_config import AnalysisConfig

    default_ctx = JobContext(
        job_id="test_yt:test_cfg:123",
        ytid="test_yt",
        config_id="test_cfg",
        config=AnalysisConfig(id="test_cfg", name="Test", passes=[]),
        total_chunks=1,
    )

    with patch("services.analysis_engine.OllamaAnalyzer", return_value=mock_ollama):
        engine = AnalysisEngine(
            model_name="qwen2.5:7b-instruct",
            model_url="http://localhost:11434",
            model_profile_id=1,
        )
        # Tests can override the JobContext per-call by reassigning
        # engine._get_job_context = MagicMock(return_value=...) inside the test.
        engine._get_job_context = MagicMock(return_value=default_ctx)
        try:
            yield engine
        finally:
            engine.shutdown()
