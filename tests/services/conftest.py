"""Fixtures for AnalysisEngine tests. Mirrors the *_test table pattern from tests/dal/conftest.py."""
from __future__ import annotations

from typing import Iterator
from unittest.mock import MagicMock

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
